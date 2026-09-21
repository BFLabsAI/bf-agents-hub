"""
TEMPLATE EXAMPLE — Adapt endpoints, field names, and business logic to your client's API.

Demonstrates: listing a product/event catalog with WhatsApp interactive messages (carousel,
list picker), finding items by name, and loading full item context in a single parallel call.

Patterns shown:
  - list_events: public catalog → WhatsApp carousel/list with fallback to text
  - find_event_by_name: fuzzy name search over cached catalog
  - event_detail: load single item details + store key IDs in session context
  - get_event_context: composite tool — parallel auth checks + pricing in one call
  - Image filtering for WhatsApp (signed S3 URLs break carousel — see _whatsapp_safe_image_static)
  - nextActionHint pattern: every tool return tells the LLM exactly what to do next
"""
import asyncio
import json
import re

import httpx
from agno.tools import tool

from app.config import PUBLIC_BASE_URL
from app.context import SessionContext
from app.client_api import ClientAPI

# Words that stay lowercase in Title Case (Portuguese)
_LOWER_WORDS = {
    "a",
    "ao",
    "às",
    "com",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "ou",
    "para",
    "por",
    "que",
    "se",
}


def _title_case(text: str | None) -> str | None:
    """Convert ALL CAPS event names to Title Case for WhatsApp display."""
    if not text:
        return text
    # Only convert if the string is mostly uppercase
    letters = [c for c in text if c.isalpha()]
    if not letters or sum(1 for c in letters if c.isupper()) / len(letters) < 0.7:
        return text  # already mixed case, leave as-is
    words = text.split()
    result = []
    for i, word in enumerate(words):
        # Preserve slashes, parentheses, hyphens within words
        lower = word.lower()
        if i == 0 or lower not in _LOWER_WORDS:
            result.append(word.capitalize())
        else:
            result.append(lower)
    return " ".join(result)


def _pick_image_url(e: dict) -> str:
    """Pick the best card image: mobile dark/light, then desktop dark/light."""
    img = e.get("image") or {}
    for variant in ("mobile", "desktop"):
        card = (img.get(variant) or {}).get("card") or {}
        for tone in ("dark", "light"):
            url = card.get(tone) or ""
            if url:
                return url
    return ""


def _format_event_subtitle(e: dict) -> str:
    """Build a compact subtitle line: dates + price for a carousel card."""
    parts = []
    start = e.get("startDate") or ""
    end = e.get("endDate") or ""
    if start and end:
        parts.append(f"{start} - {end}")
    elif start:
        parts.append(start)
    if e.get("amount"):
        try:
            val = float(e["amount"])
            parts.append(
                f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            )
        except (ValueError, TypeError):
            parts.append(str(e["amount"]))
    return " • ".join(parts)


_carousel_sent_at: dict[str, float] = {}  # from_number → monotonic time of last send


def build_event_tools(
    ctx: SessionContext, api: ClientAPI, waba_client=None
) -> list:

    @tool
    async def list_events() -> str:
        """
        List all events, courses, and congresses available for this user.
        Call this when the user asks what is available or wants to browse events.
        When _rendered=carousel is returned, do NOT list events in text — just say a brief intro.
        """
        try:
            data = await api.list_events()
            events = data.get("data", data) if isinstance(data, dict) else data
            ctx.set_available_events(events)

            normalized = [
                {
                    "activityScheduleId": e.get("activityScheduleId") or e.get("id"),
                    "title": _title_case(e.get("title")),
                    "startDate": e.get("startDate"),
                    "endDate": e.get("endDate"),
                    "amount": e.get("amount"),
                    "typeActivity": e.get("activityTypeCategory")
                    or e.get("typeActivity")
                    or "",
                    "activityLocation": e.get("activityLocation") or "",
                    "imageUrl": _pick_image_url(e),
                }
                for e in events
            ]

            # Filter for carousel:
            # - exclude anuidades/associações (separate flow)
            # - on WhatsApp ONLY: exclude non-PNG/JPEG images. Meta only accepts
            #   image/png and image/jpeg; .webp and S3 signed URLs served as
            #   application/octet-stream get the whole carousel dropped with
            #   error 131053 (confirmed via webhook status). On web any image
            #   type is fine — browsers detect by magic bytes.
            _EXCLUDE_CATEGORIES = {"ASSOCIAÇÃO"}

            # Detect whether we're targeting WhatsApp (WABAClient) or web (WebPresenter/None).
            # Lazy import avoids a circular dep at module load time.
            from app.waba_client import WABAClient

            is_whatsapp = isinstance(waba_client, WABAClient)

            def _whatsapp_safe_image_static(url: str) -> bool:
                if not url:
                    return False
                # Signed URLs → backend serves as octet-stream → Meta rejects
                if "?X-Amz-" in url:
                    return False
                # No file extension → unsigned S3 path, assume valid image
                lower = url.lower().split("?", 1)[0]
                last_segment = lower.split("/")[-1]
                if "." not in last_segment:
                    return True
                # Has extension — must be png/jpeg
                return lower.endswith((".png", ".jpg", ".jpeg"))

            # Generate placeholder cards for events without WhatsApp-safe images — in parallel
            if is_whatsapp and PUBLIC_BASE_URL:
                from app.event_card_image import generate_event_card_image
                import asyncio as _asyncio

                async def _maybe_gen_card(ev: dict) -> None:
                    if not _whatsapp_safe_image_static(ev.get("imageUrl", "")):
                        try:
                            path = await generate_event_card_image(
                                ev.get("activityScheduleId"),
                                ev.get("title") or "",
                                ev.get("startDate") or "",
                            )
                            ev["imageUrl"] = f"{PUBLIC_BASE_URL}/static/event-cards/{path.name}"
                        except Exception as _exc:
                            import logging
                            logging.getLogger("italo").warning("event_card_image failed: %s", _exc)

                await _asyncio.gather(*[_maybe_gen_card(ev) for ev in normalized])

            # Already-subscribed events are excluded from display
            subscribed_ids = ctx.subscribed_activity_ids

            # All displayable events (no anuidades/associações, no already-subscribed)
            display_events = [
                e for e in normalized
                if e.get("typeActivity", "").upper() not in _EXCLUDE_CATEGORIES
                and e.get("activityScheduleId") not in subscribed_ids
            ]

            # Subset safe for WhatsApp carousel (requires valid image)
            carousel_events = [
                e for e in display_events
                if not is_whatsapp or _whatsapp_safe_image_static(e.get("imageUrl", ""))
            ]

            # Send interactive message via WhatsApp if waba_client is available
            if waba_client:
                first_name = ctx.first_name
                intro = f"Aqui os eventos disponíveis{', ' + first_name if first_name else ''}:"

                def _date_subtitle(ev: dict) -> str:
                    s, end = ev.get("startDate") or "", ev.get("endDate") or ""
                    if s and end and s != end:
                        return f"📅 {s} a {end}"
                    if s:
                        return f"📅 {s}"
                    return ""

                cards = [
                    {
                        "activity_schedule_id": e["activityScheduleId"],
                        "title": e["title"] or "",
                        "subtitle": e.get("activityLocation") or _date_subtitle(e),
                        "image_url": e["imageUrl"],
                    }
                    for e in carousel_events
                ]
                # When no carousel-safe events exist, fall back to full display list
                send_events = carousel_events if carousel_events else display_events
                send_cards = [
                    {
                        "activity_schedule_id": e["activityScheduleId"],
                        "title": e["title"] or "",
                        "subtitle": e.get("activityLocation") or _date_subtitle(e),
                        "image_url": e["imageUrl"],
                    }
                    for e in send_events
                ]
                try:
                    import time as _time
                    _now = _time.monotonic()
                    _key = ctx.from_number
                    _last = _carousel_sent_at.get(_key, 0.0)
                    if _now - _last < 30.0:
                        return json.dumps({
                            "status": "success",
                            "_rendered": "carousel",
                            "count": len(send_cards),
                            "hint": "Carrossel já enviado neste turno. Aguarde a resposta do usuário.",
                        })
                    _carousel_sent_at[_key] = _now
                    if len(send_cards) >= 2 and carousel_events:
                        await waba_client.send_event_carousel(
                            to=ctx.from_number,
                            intro_text=intro,
                            events=send_cards,
                        )
                    else:
                        await waba_client.send_event_list(
                            to=ctx.from_number,
                            intro_text=intro,
                            events=[
                                {**c, "type_activity": e.get("typeActivity", "")}
                                for c, e in zip(send_cards, send_events)
                            ],
                        )
                    return json.dumps(
                        {
                            "status": "success",
                            "_rendered": "carousel" if (len(send_cards) >= 2 and carousel_events) else "list",
                            "count": len(send_cards),
                            "hint": (
                                "O carrossel de eventos foi enviado via WhatsApp. "
                                "Responda com UMA frase curta e natural convidando o usuário a selecionar um evento — "
                                "por exemplo: 'Selecione um evento para ver detalhes ou se inscrever.' "
                                "NÃO mencione 'carrossel', NÃO diga quantos eventos foram enviados, "
                                "NÃO liste os eventos em texto."
                            ),
                        }
                    )
                except Exception as exc:
                    import logging

                    logging.getLogger("italo").warning(
                        "send_event_carousel/list failed, falling back to text: %s", exc
                    )

            # Fallback: return text list for the LLM to format (also excludes anuidades)
            return json.dumps(
                {
                    "status": "success",
                    "count": len(display_events),
                    "events": display_events,
                }
            )
        except Exception as exc:
            return json.dumps({"status": "error", "message": str(exc)})

    @tool
    async def find_event_by_name(event_name: str) -> str:
        """
        Find an event by its name (or partial name).
        Call this when the user mentions an event by name instead of clicking the carousel.
        Auto-loads the event catalog if not yet cached — does NOT show the carousel.
        Returns the event details including activity_schedule_id so you can call event_detail next.
        """
        try:
            events = ctx.available_events or []
            if not events:
                # Load catalog silently — no carousel rendered, just populates the cache.
                data = await api.list_events()
                raw = data.get("data", data) if isinstance(data, dict) else data
                normalized = [
                    {
                        "activityScheduleId": e.get("activityScheduleId") or e.get("id"),
                        "title": _title_case(e.get("title")),
                        "startDate": e.get("startDate"),
                        "endDate": e.get("endDate"),
                        "amount": e.get("amount"),
                        "typeActivity": e.get("activityTypeCategory") or e.get("typeActivity") or "",
                        "activityLocation": e.get("activityLocation") or "",
                        "imageUrl": _pick_image_url(e),
                    }
                    for e in raw
                ]
                ctx.set_available_events(normalized)
                events = normalized

            query = event_name.lower().strip()
            # Exact match first
            for e in events:
                title = (e.get("title") or "").lower()
                if query == title:
                    return json.dumps(
                        {
                            "status": "success",
                            "match_type": "exact",
                            "event": e,
                            "nextActionHint": f"Chame get_event_context(activity_schedule_id={e.get('activityScheduleId')}) imediatamente.",
                        }
                    )

            # Partial match
            for e in events:
                title = (e.get("title") or "").lower()
                if query in title or any(
                    word in title for word in query.split() if len(word) > 3 and not word.isdigit()
                ):
                    return json.dumps(
                        {
                            "status": "success",
                            "match_type": "partial",
                            "event": e,
                            "nextActionHint": f"Chame get_event_context(activity_schedule_id={e.get('activityScheduleId')}) imediatamente.",
                        }
                    )

            return json.dumps(
                {
                    "status": "not_found",
                    "message": f"Não encontrei um evento chamado '{event_name}'. Peça para o usuário verificar o nome ou listar os eventos novamente.",
                }
            )
        except Exception as exc:
            return json.dumps({"status": "error", "message": str(exc)})

    @tool
    async def event_detail(activity_schedule_id: int) -> str:
        """
        Get full details of a specific event: description, instructor, dates, vacancies.
        Also returns payment_plan for this user — DO NOT show price yet unless user asked.
        """
        try:
            data = await api.event_detail(activity_schedule_id)
            ctx.set_activity_schedule_id(activity_schedule_id)
            # Response structure: { data: { general: { costCenterId: X, ... } } }
            inner = data.get("data", {}) if isinstance(data, dict) else {}
            general = inner.get("general", {}) if isinstance(inner, dict) else {}
            cost_center = (
                general.get("costCenterId")
                or general.get("cost_center_id")
                or inner.get("costCenterId")
                or data.get("costCenterId")
            )
            if cost_center:
                ctx.set_cost_center_id(int(cost_center))
            # Store event title for payment messages
            # API returns title in general.activity / general.activitySchedule
            title = (
                general.get("activity")
                or general.get("activitySchedule")
                or general.get("title")
                or inner.get("title")
                or data.get("title")
            )
            if title:
                ctx.set_event_title(_title_case(str(title)))

            summary = {
                "title": ctx.event_title,
                "typeActivity": general.get("typeActivity"),
                "startDate": general.get("startDate") or general.get("activityScheduleStartDate"),
                "endDate": general.get("endDate") or general.get("activityScheduleEndDate"),
                "location": general.get("activityLocation") or general.get("location"),
                "isSoldOut": general.get("isSoldOut"),
                "isSubscriptionPeriodClosed": general.get("isSubscriptionPeriodClosed"),
                "vacancies": general.get("vacancies") or general.get("availableVacancies"),
            }
            return json.dumps({
                "status": "success",
                "detail": summary,
                "nextActionHint": (
                    "Apresente os detalhes do evento ao usuário usando APENAS os campos acima. "
                    "NUNCA mostre activityScheduleId, costCenterId ou qualquer ID numérico. "
                    "NUNCA mencione faixa de valores (min/max) — o preço exato virá de list_payment_plans conforme a categoria do usuário. "
                    "Pergunte se deseja verificar inscrição existente ou prosseguir com nova inscrição."
                ),
            })
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                # Auth token doesn't have permission for this endpoint.
                # Fall back to cached public event data from list_events.
                cached = ctx.available_events or []
                cached_event = next(
                    (
                        e for e in cached
                        if (e.get("activityScheduleId") or e.get("id")) == activity_schedule_id
                    ),
                    None,
                )
                if cached_event:
                    ctx.set_activity_schedule_id(activity_schedule_id)
                    title = _title_case(cached_event.get("title") or "")
                    if title:
                        ctx.set_event_title(title)
                    return json.dumps({
                        "status": "success",
                        "detail": {"data": {"general": cached_event}},
                        "_source": "public_cache",
                        "nextActionHint": (
                            "Dados básicos disponíveis (sem detalhes completos). "
                            "Apresente o evento ao usuário com as informações disponíveis. "
                            "Para se inscrever, o usuário precisa estar logado no sistema."
                        ),
                    })
            return json.dumps({"status": "error", "message": str(exc)})
        except Exception as exc:
            return json.dumps({"status": "error", "message": str(exc)})

    @tool
    async def get_event_context(activity_schedule_id: int) -> str:
        """
        PREFERRED entry point for event selection — use instead of calling event_detail +
        check_existing_subscription + get_cost_center_person + list_payment_plans separately.

        Fetches full event context in one step (parallel API calls):
        - Event details (title, dates, location, vacancies)
        - Whether the user is already subscribed
        - User's professional category status for this event
        - Pricing plan (if category is registered)
        - Auto-registers category when only ONE option exists (no user input needed)

        Use whenever the user selects or asks about a specific event.
        """
        try:
            # Step 1: event_detail (must run first — sets cost_center_id in ctx)
            detail_raw = await api.event_detail(activity_schedule_id)
            ctx.set_activity_schedule_id(activity_schedule_id)
            inner = detail_raw.get("data", {}) if isinstance(detail_raw, dict) else {}
            general = inner.get("general", {}) if isinstance(inner, dict) else {}
            cost_center = (
                general.get("costCenterId") or general.get("cost_center_id")
                or inner.get("costCenterId") or detail_raw.get("costCenterId")
            )
            if cost_center:
                ctx.set_cost_center_id(int(cost_center))
            title = (
                general.get("activity")
                or general.get("activitySchedule")
                or general.get("title")
                or inner.get("title")
                or detail_raw.get("title")
            )
            if title:
                ctx.set_event_title(_title_case(str(title)))

            # Enrich with catalog data (store/offers has dates/location/vacancies
            # that the detailing endpoint does not return). Load catalog if not cached yet.
            if not ctx.available_events:
                try:
                    raw_catalog = await api.list_events()
                    catalog_items = raw_catalog if isinstance(raw_catalog, list) else (
                        raw_catalog.get("data", []) if isinstance(raw_catalog, dict) else []
                    )
                    ctx.set_available_events(catalog_items)
                except Exception:
                    pass

            cached = next(
                (e for e in (ctx.available_events or [])
                 if e.get("activityScheduleId") == activity_schedule_id),
                {}
            )

            # Extract plain-text description from HTML content block
            description_text: str | None = None
            content_blocks = inner.get("content") or []
            for block in content_blocks:
                raw_html = block.get("description") or ""
                if raw_html:
                    # Remove style/script blocks first, then strip remaining tags
                    clean = re.sub(r"<style[^>]*>.*?</style>", " ", raw_html, flags=re.DOTALL)
                    clean = re.sub(r"<script[^>]*>.*?</script>", " ", clean, flags=re.DOTALL)
                    plain = re.sub(r"<[^>]+>", " ", clean)
                    plain = re.sub(r"\s+", " ", plain).strip()
                    if len(plain) > 50:
                        description_text = plain[:800]
                    break

            detail_summary = {
                "title": ctx.event_title,
                "typeActivity": general.get("typeActivity") or cached.get("activityTypeCategory"),
                "startDate": general.get("startDate") or general.get("activityScheduleStartDate") or cached.get("startDate"),
                "endDate": general.get("endDate") or general.get("activityScheduleEndDate") or cached.get("endDate"),
                "location": general.get("activityLocation") or general.get("location") or cached.get("activityLocation"),
                "isSoldOut": general.get("isSoldOut"),
                "isSubscriptionPeriodClosed": general.get("isSubscriptionPeriodClosed"),
                "vacancies": general.get("vacancies") or general.get("availableVacancies") or cached.get("vacancies"),
                "description": description_text,
            }

            # Step 2: check_existing_subscription + get_cost_center_person in parallel
            async def _check_subscription():
                try:
                    subs = await api.check_existing_subscription(activity_schedule_id)
                    if not subs:
                        return {"subscribed": False}
                    account_ids = []
                    for sub in subs:
                        for acc in sub.get("accountReceive", sub.get("account_receive", [])):
                            aid = acc.get("id")
                            if aid is not None:
                                account_ids.append(int(aid))
                    account_ids = list(dict.fromkeys(account_ids))
                    if account_ids:
                        ctx.set_account_receive_ids(account_ids)
                    return {"subscribed": bool(account_ids), "accountReceiveIds": account_ids}
                except Exception:
                    return {"subscribed": False, "error": "check_failed"}

            async def _check_category():
                if not ctx.cost_center_id:
                    return {"registered": False, "error": "no_cost_center"}
                try:
                    data = await api.get_cost_center_person(ctx.cost_center_id)
                    return {"registered": data.get("_registered", False)}
                except Exception:
                    return {"registered": False, "error": "check_failed"}

            sub_result, cat_result = await asyncio.gather(_check_subscription(), _check_category())

            already_subscribed = sub_result.get("subscribed", False)
            category_registered = cat_result.get("registered", False)

            # Step 3: if not registered, check available categories.
            # Auto-register if exactly ONE option exists (no user input needed).
            auto_registered_category = None
            if not category_registered and ctx.cost_center_id:
                try:
                    cats = await api.list_categories(ctx.cost_center_id, activity_schedule_id)
                    ctx.set_available_categories([
                        {"id": c.get("id"), "description": c.get("description") or c.get("name", ""), "amount": c.get("amount")}
                        for c in cats
                    ])
                    if len(cats) == 1:
                        # Only one option — auto-register silently
                        cat = cats[0]
                        await api.register_category(
                            cost_center_id=ctx.cost_center_id,
                            cost_center_category_professional_id=int(cat["id"]),
                        )
                        category_registered = True
                        auto_registered_category = _title_case(cat.get("description") or cat.get("name", ""))
                except Exception:
                    pass

            # Step 4: list_payment_plans (only if NOT subscribed and category registered)
            # Skip when already subscribed — the API returns a 422 "Valor informado errado"
            # that would pollute the response; account_receive_ids from step 2 are sufficient.
            plan_result = None
            if category_registered and not already_subscribed:
                try:
                    data = await api.list_payment_plans(activity_schedule_id)
                    if data and not (isinstance(data, dict) and data.get("_domain_error")):
                        plan = data[0] if isinstance(data, list) else data
                        plan_id = plan.get("id") or plan.get("paymentPlanId")
                        amount = plan.get("amount")
                        pending_membership = plan.get("pendingMembershipFee") or []
                        annuity_ids = [int(a["subscriptionId"]) for a in pending_membership if a.get("subscriptionId")]
                        annuity_ar_ids = [int(a["accountReceiveId"]) for a in pending_membership if a.get("accountReceiveId")]
                        ctx.set_annuity_subscription_ids(annuity_ids)
                        ctx.set_annuity_account_receive_ids(annuity_ar_ids)
                        ctx.set_pending_membership_details([
                            {"description": a.get("description", ""), "amount": float(a.get("amount") or 0)}
                            for a in pending_membership
                        ])
                        if not already_subscribed:
                            if plan_id:
                                ctx.set_payment_plan_id(int(plan_id))
                            if amount is not None:
                                ctx.set_event_amount(float(amount))
                        plan_result = {
                            "paymentPlanId": plan_id,
                            "amount": amount,
                            "associatedAmount": plan.get("associatedAmount"),
                            "pendingMembershipFee": pending_membership,
                        }
                except Exception:
                    pass

            # Build next action hint
            pending_annuities = plan_result.get("pendingMembershipFee") if plan_result else []
            if already_subscribed:
                if pending_annuities:
                    next_hint = (
                        "Usuário JÁ está inscrito com pagamento pendente. "
                        "Informe que já está inscrito e o vencimento. "
                        "Há anuidades pendentes em plan.pendingMembershipFee — liste cada uma com nome e valor. "
                        "Pergunte se quer incluir as anuidades junto com o evento para garantir desconto de sócio, "
                        "ou pagar só o evento. AGUARDE a resposta antes de chamar qualquer outra ferramenta."
                    )
                else:
                    next_hint = (
                        "Usuário JÁ está inscrito com pagamento pendente e sem anuidades pendentes. "
                        "Informe: nome do evento em Title Case, valor pendente e vencimento. "
                        "Diga que ele já está inscrito mas com pagamento pendente e pergunte se deseja realizar o pagamento agora. "
                        "AGUARDE a confirmação. Só APÓS o usuário confirmar que quer pagar, pergunte a forma de pagamento (PIX, Boleto ou Cartão) e ENTÃO chame list_payment_methods."
                    )
            elif not category_registered:
                next_hint = (
                    "Usuário NÃO tem categoria cadastrada e há mais de uma opção disponível. "
                    "Apresente os detalhes do evento e chame list_categories para mostrar as categorias disponíveis. "
                    "NÃO pergunte — apenas liste numericamente para o usuário escolher."
                )
            elif auto_registered_category:
                next_hint = (
                    f"Categoria '{auto_registered_category}' registrada automaticamente (única opção disponível). "
                    "NÃO mencione ao usuário que a categoria foi registrada automaticamente — siga direto para os valores. "
                    "Apresente os detalhes do evento com o preço e pergunte se deseja se inscrever."
                )
            elif plan_result and pending_annuities:
                next_hint = (
                    "Contexto completo carregado. Apresente os detalhes do evento com o preço. "
                    "Mostre as opções (com ou sem anuidade) e pergunte qual o usuário prefere "
                    "ANTES de criar a inscrição."
                )
            elif plan_result:
                next_hint = (
                    "Contexto completo carregado. Apresente os detalhes do evento com o preço "
                    "e pergunte se o usuário deseja se inscrever."
                )
            else:
                next_hint = "Apresente os detalhes do evento ao usuário."

            return json.dumps({
                "status": "success",
                "detail": detail_summary,
                "subscription": {
                    "alreadySubscribed": already_subscribed,
                    "accountReceiveIds": sub_result.get("accountReceiveIds", []),
                },
                "category": {
                    "registered": category_registered,
                    "autoRegistered": auto_registered_category,
                },
                "plan": plan_result,
                "nextActionHint": next_hint,
            })

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                return json.dumps({
                    "status": "token_expired",
                    "nextActionHint": (
                        "Token expirado. Informe o usuário e peça para recarregar a página."
                    ),
                })
            return json.dumps({"status": "error", "message": str(exc),
                               "nextActionHint": "Erro ao carregar contexto do evento. Tente novamente."})
        except Exception as exc:
            return json.dumps({"status": "error", "message": str(exc),
                               "nextActionHint": "Erro ao carregar contexto do evento. Tente novamente."})

    return [list_events, find_event_by_name, event_detail, get_event_context]
