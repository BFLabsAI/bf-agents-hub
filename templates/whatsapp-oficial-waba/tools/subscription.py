"""
TEMPLATE EXAMPLE — Adapt endpoints, field names, and business logic to your client's API.

Demonstrates: full subscription lifecycle — check existing, list pricing plans, create,
list all, cancel, and prepare multi-item payment batches.

Patterns shown:
  - check_existing_subscription: idempotent entry point; handles already-subscribed edge cases
  - list_payment_plans: domain-error detection (422 → category not registered)
  - create_subscription: include optional bundled fees (annuities); handles duplicate 400/409
  - my_subscriptions: summarize with status labels; explicit nextActionHint to prevent LLM from
    auto-advancing without user confirmation
  - cancel_subscription: domain error (422) vs. hard error separation
  - pay_all_annuities: domain-specific — batch pay all pending membership fees as a group
  - select_pending_payments: load accountReceiveIds for multiple pending event subscriptions
  - add_annuities_to_payment: merge fee IDs into current payment context for bundled checkout
  - nextActionHint on every return: drives the LLM state machine explicitly
"""
import json
import logging

import httpx
from agno.tools import tool

import app.error_log as error_log
from app.context import SessionContext
from app.client_api import ClientAPI
from tools.events import _title_case

logger = logging.getLogger(__name__)


def build_subscription_tools(ctx: SessionContext, api: ClientAPI) -> list:

    @tool
    async def check_existing_subscription() -> str:
        """
        Check if the user is already subscribed to the current event.
        ALWAYS call this before list_payment_plans or create_subscription.
        If already subscribed, jumps directly to list_payment_methods.
        If not subscribed, next step is get_cost_center_person.
        """
        try:
            subs = await api.check_existing_subscription(ctx.activity_schedule_id)
            if not subs:
                # None or empty list → not subscribed
                return json.dumps({
                    "status": "not_subscribed",
                    "nextAction": "call_get_cost_center_person",
                    "nextActionHint": (
                        "A pessoa ainda NÃO está inscrita. Chame get_cost_center_person AGORA "
                        "para verificar se tem categoria cadastrada antes de listar planos."
                    ),
                })
            # Extract accountReceiveIds from list of subscription objects
            account_ids = []
            cost_center = None
            for sub in subs:
                for acc in (sub.get("accountReceive") or sub.get("account_receive") or []):
                    aid = acc.get("id")
                    if aid is not None:
                        account_ids.append(int(aid))
                if cost_center is None:
                    cc = sub.get("costCenterId")
                    if cc:
                        cost_center = int(cc)
            account_ids = list(dict.fromkeys(account_ids))
            if account_ids:
                ctx.set_account_receive_ids(account_ids)
            if cost_center:
                ctx.set_cost_center_id(cost_center)
            if not account_ids:
                # Subscribed but accountReceive is null — use select_pending_payments to resolve
                return json.dumps({
                    "status": "already_subscribed",
                    "accountReceiveIds": [],
                    "nextAction": "call_select_pending_payments",
                    "nextActionHint": (
                        f"Inscrição existe mas accountReceive é nulo. "
                        f"Chame select_pending_payments([{ctx.activity_schedule_id}]) AGORA "
                        "para carregar o ID de pagamento correto e depois list_payment_methods."
                    ),
                })
            return json.dumps({
                "status": "already_subscribed",
                "accountReceiveIds": account_ids,
                "nextAction": "call_list_payment_methods",
                "nextActionHint": (
                    "A pessoa JÁ está inscrita. NÃO chame list_payment_plans nem create_subscription. "
                    "Chame list_payment_methods AGORA para seguir direto ao pagamento."
                ),
            })
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 403:
                ctx.set_access_token("")
                return json.dumps({
                    "status": "token_expired",
                    "nextActionHint": (
                        "Token de acesso expirado. Chame identify_by_phone() IMEDIATAMENTE "
                        "para re-autenticar o usuário e depois repita o fluxo."
                    ),
                })
            error_log.record(ctx.from_number, ctx.from_number, "check_existing_subscription", exc,
                             {"activity_schedule_id": ctx.activity_schedule_id})
            return json.dumps({"status": "error", "message": str(exc),
                               "nextActionHint": "Não conseguiu verificar inscrição. Informe erro técnico e ofereça tentar novamente."})

    @tool
    async def list_payment_plans() -> str:
        """
        Get the pricing plan for the user in the current event.
        Shows the price — ask user to CONFIRM subscription before calling create_subscription.
        If pendingMembershipFee exists, show both options (with/without annuity).
        Do NOT mention payment methods here.
        If returns domain_error, person needs to register a professional category first.
        """
        try:
            # Guard: if account_receive_ids already set, the user is already subscribed.
            # list_payment_plans is for NEW subscriptions only — skip the API call to avoid
            # a 422 "Valor informado errado" that would confuse the flow.
            if ctx.account_receive_ids:
                ar_ids = ctx.account_receive_ids
                return json.dumps({
                    "status": "already_subscribed_pending_payment",
                    "accountReceiveIds": ar_ids,
                    "nextAction": "call_list_payment_methods",
                    "nextActionHint": (
                        f"O usuário já possui inscrição com accountReceiveIds={ar_ids}. "
                        "NÃO chame create_subscription. "
                        "Chame list_payment_methods AGORA para seguir para o pagamento."
                    ),
                })

            data = await api.list_payment_plans(ctx.activity_schedule_id)

            # Domain error (422)
            if isinstance(data, dict) and data.get("_domain_error"):
                msg = data.get("message", "")
                msg_lower = msg.lower()
                # 422 can mean "already subscribed with pending payment" — distinct from "no category"
                if any(kw in msg_lower for kw in ("já inscrito", "valor pendente", "valor informado", "pendente de r$")):
                    ar_ids = ctx.account_receive_ids or []
                    return json.dumps({
                        "status": "already_subscribed_pending_payment",
                        "message": msg,
                        "accountReceiveIds": ar_ids,
                        "nextAction": "call_list_payment_methods" if ar_ids else "call_select_pending_payments",
                        "nextActionHint": (
                            f"A API retornou que o usuário já tem inscrição com pagamento pendente. "
                            f"NÃO chame list_categories nem create_subscription. "
                            + (
                                f"Use os accountReceiveIds já disponíveis no contexto {ar_ids} "
                                "e chame list_payment_methods AGORA para seguir para o pagamento."
                                if ar_ids else
                                f"Chame select_pending_payments([{ctx.activity_schedule_id}]) AGORA "
                                "para carregar os accountReceiveIds e seguir para list_payment_methods."
                            )
                        ),
                    })
                return json.dumps({
                    "status": "domain_error",
                    "code": 1,
                    "message": msg or "Categoria profissional não cadastrada.",
                    "nextAction": "call_list_categories",
                    "nextActionHint": (
                        "A pessoa não tem categoria cadastrada. "
                        "Chame list_categories AGORA e apresente a lista numerada ao cliente."
                    ),
                })

            # API returns list of plans (normalised in client)
            if not data:
                return json.dumps({"status": "no_plan", "message": "Nenhum plano disponível."})

            plan = data[0]
            plan_id = plan.get("id") or plan.get("paymentPlanId")
            if plan_id:
                ctx.set_payment_plan_id(int(plan_id))

            # amount = non-member price; associatedAmount = member price
            amount = plan.get("amount")
            associated_amount = plan.get("associatedAmount")
            pending_membership = plan.get("pendingMembershipFee") or []

            # Store annuity subscription IDs so create_subscription can include them
            annuity_ids = [
                int(a["subscriptionId"])
                for a in pending_membership
                if a.get("subscriptionId")
            ]
            # Store annuity accountReceiveIds — the API does NOT return these in the
            # POST /subscribe response, so we must capture them here while they're available.
            annuity_ar_ids = [
                int(a["accountReceiveId"])
                for a in pending_membership
                if a.get("accountReceiveId")
            ]
            ctx.set_annuity_subscription_ids(annuity_ids)
            ctx.set_annuity_account_receive_ids(annuity_ar_ids)
            ctx.set_pending_membership_details([
                {"description": a.get("description", ""), "amount": float(a.get("amount") or 0)}
                for a in pending_membership
            ])
            if amount is not None:
                ctx.set_event_amount(float(amount))

            member_status = ctx.member_status or plan.get("memberStatus") or ""
            # PATTERN: Customize this note for your domain's membership/tier concept.
            # TODO: replace SBOT-specific language with your organization's terminology.
            member_note = ""
            if not ctx.is_member:
                member_note = (
                    "O usuário é NÃO SÓCIO da SBOT. "  # TODO: replace with your domain's non-member label
                    "NÃO mencione anuidades, associação pendente ou desconto de sócio. "
                    "Mostre apenas o valor do evento para não sócios."
                )

            return json.dumps({
                "status": "success",
                "plan": {
                    "paymentPlanId": plan_id,
                    "amount": amount,
                    "associatedAmount": associated_amount,
                    "description": plan.get("description"),
                    "category": plan.get("category"),
                    "memberStatus": member_status,
                    "pendingMembershipFee": pending_membership,
                },
                "memberNote": member_note,
                "nextAction": "ask_subscription_confirmation",
                "nextActionHint": (
                    "Mostre ao cliente o valor e pergunte se quer CONFIRMAR a inscrição. "
                    "NÃO mencione forma de pagamento ainda."
                    + (f" {member_note}" if member_note else "")
                ),
            })
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 403:
                ctx.set_access_token("")
                return json.dumps({
                    "status": "token_expired",
                    "nextActionHint": (
                        "Token expirado. Chame identify_by_phone() IMEDIATAMENTE "
                        "para re-autenticar e depois repita list_payment_plans."
                    ),
                })
            error_log.record(ctx.from_number, ctx.from_number, "list_payment_plans", exc,
                             {"activity_schedule_id": ctx.activity_schedule_id})
            return json.dumps({"status": "error", "message": str(exc),
                               "nextActionHint": "NÃO mencione valores. Informe que não conseguiu carregar o plano e oriente contato com o suporte se persistir."})

    @tool
    async def create_subscription(include_annuity: bool = False) -> str:
        """
        Create the subscription for the user.
        Only call AFTER user confirms the price shown by list_payment_plans.
        Pass include_annuity=True if user chose to include the annuity.
        After success, immediately call list_payment_methods.
        """
        try:
            if not ctx.payment_plan_id:
                return json.dumps({
                    "status": "error",
                    "message": "Plano de pagamento não encontrado na sessão. Chame list_payment_plans novamente.",
                    "nextAction": "call_list_payment_plans",
                })
            annuity_ids = ctx.annuity_subscription_ids if include_annuity else None
            subs = await api.create_subscription(
                payment_plan_id=ctx.payment_plan_id,
                annuity_ids=annuity_ids,
            )
            # Extract accountReceiveIds from list of subscription objects
            account_ids = []
            for sub in subs:
                for acc in (sub.get("accountReceive") or sub.get("account_receive") or []):
                    aid = acc.get("id")
                    if aid is not None:
                        account_ids.append(int(aid))
            account_ids = list(dict.fromkeys(account_ids))
            # POST /subscribe only returns the new event subscription's accountReceiveId.
            # Annuity accountReceiveIds are pre-existing — append them from context.
            if include_annuity:
                for ar_id in ctx.annuity_account_receive_ids:
                    if ar_id not in account_ids:
                        account_ids.append(ar_id)
            if account_ids:
                ctx.set_account_receive_ids(account_ids)
            # Mark event as subscribed so list_events excludes it going forward
            if ctx.activity_schedule_id:
                ctx.add_subscribed_activity_id(ctx.activity_schedule_id)
            if not account_ids:
                # accountReceive was null — use select_pending_payments to resolve AR ID
                return json.dumps({
                    "status": "success",
                    "accountReceiveIds": [],
                    "nextAction": "call_select_pending_payments",
                    "nextActionHint": (
                        f"Inscrição criada mas accountReceive é nulo. "
                        f"Chame select_pending_payments([{ctx.activity_schedule_id}]) AGORA "
                        "para carregar o ID de pagamento e depois list_payment_methods."
                    ),
                })
            return json.dumps({
                "status": "success",
                "accountReceiveIds": account_ids,
                "nextAction": "call_list_payment_methods",
                "nextActionHint": (
                    "Inscrição criada. Chame list_payment_methods AGORA para descobrir "
                    "quais formas de pagamento estão disponíveis."
                ),
            })
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 403:
                ctx.set_access_token("")
                return json.dumps({
                    "status": "token_expired",
                    "nextActionHint": (
                        "Token expirado. Chame identify_by_phone() IMEDIATAMENTE "
                        "para re-autenticar e depois repita create_subscription."
                    ),
                })
            # Detect duplicate subscription (400 = already subscribed at API level)
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 400:
                return json.dumps({
                    "status": "already_subscribed",
                    "nextAction": "call_select_pending_payments",
                    "nextActionHint": (
                        f"Inscrição já existe (400 da API). "
                        f"Chame select_pending_payments([{ctx.activity_schedule_id}]) AGORA "
                        "para carregar o ID de pagamento e seguir para list_payment_methods."
                    ),
                })
            exc_str = str(exc).lower()
            if any(kw in exc_str for kw in ("já inscrito", "already", "duplicate", "existe", "409", "duplicat")):
                return json.dumps({
                    "status": "already_subscribed",
                    "nextAction": "call_select_pending_payments",
                    "nextActionHint": (
                        f"Usuário já está inscrito. "
                        f"Chame select_pending_payments([{ctx.activity_schedule_id}]) AGORA "
                        "para carregar os accountReceiveIds e seguir para pagamento."
                    ),
                })
            error_log.record(ctx.from_number, ctx.from_number, "create_subscription", exc,
                             {"payment_plan_id": ctx.payment_plan_id, "include_annuity": include_annuity})
            return json.dumps({"status": "error", "message": str(exc),
                               "nextActionHint": "NÃO confirme que a inscrição foi criada. Informe erro técnico e ofereça tentar novamente."})

    @tool
    async def my_subscriptions() -> str:
        """
        List all subscriptions for the identified user (past, active, pending, cancelled).
        Each item includes activityScheduleId, accountReceiveId, amount, dueDate, statusSubscription.
        statusSubscription=1 means pending payment.
        Can be called at any time after identify_person.
        """
        try:
            subs = await api.my_subscriptions()
            # Summarise for the agent
            _STATUS_LABEL = {
                1: "Pendente de pagamento",
                2: "Pago",
                3: "Cancelado",
                4: "Cortesia",
            }
            summary = []
            for s in subs:
                raw_status = s.get("statusSubscription")
                summary.append({
                    "subscriptionId": s.get("id") or s.get("subscriptionId"),
                    "activityScheduleId": s.get("activityScheduleId"),
                    "description": _title_case(s.get("activityScheduleDescription") or s.get("activityDescription")),
                    "startDate": s.get("activityScheduleStartDate"),
                    "endDate": s.get("activityScheduleEndDate"),
                    "amount": s.get("amount"),
                    "dueDate": s.get("dueDate"),
                    "accountReceiveId": s.get("accountReceiveId"),
                    "statusSubscription": raw_status,  # numeric — used for logic only, never show to user
                    "statusLabel": _STATUS_LABEL.get(raw_status, "Desconhecido"),
                    "costCenterId": s.get("costCenterId"),
                })
            if not summary:
                return json.dumps({
                    "status": "success",
                    "subscriptions": [],
                    "hint": (
                        "Nenhuma inscrição ou pendência retornada pela API para este usuário. "
                        "Se o usuário afirmar que tem anuidade ou inscrição pendente, informe que "
                        "o sistema não está exibindo pendências no momento e oriente a entrar em "
                        "contato com o suporte da iTarget para verificar o cadastro."
                    ),
                })
            pending = [s for s in summary if s.get("statusSubscription") == 1]
            return json.dumps({
                "status": "success",
                "subscriptions": summary,
                "nextActionHint": (
                    "AGUARDE o usuário decidir o que deseja fazer. "
                    "NÃO chame select_pending_payments nem list_payment_methods automaticamente. "
                    "Se o usuário explicitamente pedir para pagar uma ou mais pendências específicas, "
                    "ENTÃO chame select_pending_payments com os activityScheduleIds solicitados."
                    + (
                        f" Há {len(pending)} inscrição(ões) pendente(s) de pagamento (statusSubscription=1). "
                        "Informe o usuário sobre as pendências e pergunte se deseja pagar."
                        if pending else ""
                    )
                ),
            })
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 403:
                ctx.set_access_token("")
                return json.dumps({
                    "status": "token_expired",
                    "nextActionHint": (
                        "Token expirado. Chame identify_by_phone() IMEDIATAMENTE "
                        "para re-autenticar e depois repita my_subscriptions."
                    ),
                })
            error_log.record(ctx.from_number, ctx.from_number, "my_subscriptions", exc,
                             {"person_id": ctx.person_id})
            return json.dumps({"status": "error", "message": str(exc),
                               "nextActionHint": "Não conseguiu carregar as inscrições. Informe e ofereça tentar novamente. NUNCA liste inscrições da memória."})

    @tool
    async def cancel_subscription(subscription_ids: list[int]) -> str:
        """
        Cancel one or more subscriptions by their subscriptionId (the 'id' field from my_subscriptions).
        Always call my_subscriptions first to get the subscriptionId for the event the user wants to cancel.
        Only pending subscriptions (statusSubscription=1) can generally be cancelled.
        """
        try:
            data = await api.cancel_subscription(subscription_ids)
            if isinstance(data, dict) and data.get("_domain_error"):
                return json.dumps({
                    "status": "domain_error",
                    "message": data.get("message", "Não é possível cancelar a inscrição."),
                })
            return json.dumps({
                "status": "success",
                "message": data.get("message", "Cancelamento solicitado com sucesso."),
            })
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 403:
                ctx.set_access_token("")
                return json.dumps({
                    "status": "token_expired",
                    "nextActionHint": (
                        "Token expirado. Chame identify_by_phone() IMEDIATAMENTE "
                        "para re-autenticar e depois repita cancel_subscription."
                    ),
                })
            error_log.record(ctx.from_number, ctx.from_number, "cancel_subscription", exc,
                             {"subscription_ids": subscription_ids})
            return json.dumps({"status": "error", "message": str(exc),
                               "nextActionHint": "NÃO confirme o cancelamento. Informe erro técnico e ofereça tentar novamente."})

    @tool
    async def pay_all_annuities() -> str:
        """
        Load ALL pending annuities (membership fees / anuidades) for payment together.
        Use this — and ONLY this — when the user wants to pay annuities.
        NEVER use select_pending_payments for annuities.
        Annuities must always be paid as a complete group; partial payment is not allowed.
        After calling this, immediately call list_payment_methods.
        """
        if ctx.member_status and ctx.member_status.lower() in ("não sócio", "nao socio"):
            return json.dumps({
                "status": "not_a_member",
                "nextActionHint": "O usuário não é associado da SBOT e não possui anuidades. Informe gentilmente que ele não é sócio e não há anuidades para pagar. NÃO ofereça anuidades.",
            })
        try:
            items = await api.my_annuities()
            _STATUS_LABEL = {1: "Pendente de pagamento", 2: "Pago", 3: "Cancelado", 4: "Cortesia"}
            pending = [i for i in items if i.get("statusSubscription") == 1]
            if not pending:
                all_items = [
                    {
                        "description": _title_case(i.get("activityScheduleDescription") or i.get("activityDescription") or ""),
                        "amount": i.get("amount"),
                        "statusLabel": _STATUS_LABEL.get(i.get("statusSubscription"), "Desconhecido"),
                    }
                    for i in items
                ]
                return json.dumps({
                    "status": "no_pending_annuities",
                    "annuities": all_items,
                    "nextActionHint": (
                        "Não há anuidades pendentes de pagamento. "
                        + ("As anuidades já estão pagas ou canceladas." if all_items else
                           "Nenhuma anuidade encontrada para este usuário.")
                    ),
                })
            account_ids: list[int] = []
            cost_center: int | None = None
            summary = []
            total = 0.0
            for item in pending:
                ar_id = item.get("accountReceiveId")
                if ar_id is not None:
                    account_ids.append(int(ar_id))
                if not cost_center and item.get("costCenterId"):
                    cost_center = int(item["costCenterId"])
                amount = float(item.get("amount") or 0)
                total += amount
                summary.append({
                    "description": _title_case(item.get("activityScheduleDescription") or item.get("activityDescription") or "Anuidade"),
                    "amount": amount,
                    "dueDate": item.get("dueDate"),
                })
            if not account_ids:
                return json.dumps({
                    "status": "error",
                    "message": "Anuidades encontradas mas sem accountReceiveId. Oriente o usuário a contatar o suporte.",
                    "nextActionHint": "Informe que não foi possível preparar o pagamento e sugira contato com o suporte.",  # TODO: replace with your organization's support contact
                })
            ctx.set_account_receive_ids(account_ids)
            if cost_center:
                ctx.set_cost_center_id(cost_center)
            logger.info("pay_all_annuities | count=%d account_ids=%s total=%.2f", len(pending), account_ids, total)
            return json.dumps({
                "status": "success",
                "annuities": summary,
                "totalCount": len(pending),
                "totalAmount": total,
                "accountReceiveIds": account_ids,
                "nextAction": "call_list_payment_methods",
                "nextActionHint": (
                    f"Todas as {len(pending)} anuidade(s) pendente(s) foram carregadas (total R$ {total:.2f}). "
                    "Chame list_payment_methods AGORA para oferecer as formas de pagamento."
                ),
            })
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError):
                if exc.response.status_code == 403:
                    ctx.set_access_token("")
                    return json.dumps({
                        "status": "token_expired",
                        "nextActionHint": "Token expirado. Re-autentique e repita pay_all_annuities.",
                    })
                if exc.response.status_code == 422:
                    return json.dumps({
                        "status": "not_a_member",
                        "nextActionHint": "O usuário não é associado e não possui anuidades. Informe gentilmente que ele não é sócio e não há anuidades para pagar.",
                    })
            error_log.record(ctx.from_number, ctx.from_number, "pay_all_annuities", exc, {})
            return json.dumps({"status": "error", "message": str(exc),
                               "nextActionHint": "Não conseguiu carregar as anuidades. Informe erro técnico e ofereça tentar novamente."})

    @tool
    async def select_pending_payments(activity_schedule_ids: list[int]) -> str:
        """
        Prepare payment for one or more pending EVENT subscriptions by their activityScheduleIds.
        Use ONLY for events — NEVER for annuities (use pay_all_annuities instead).
        Call this when the user wants to pay one or more specific event subscriptions.
        Loads the accountReceiveIds for all selected events and stores them for list_payment_methods.
        After calling this, immediately call list_payment_methods.
        """
        try:
            subs = await api.my_subscriptions()
            logger.info("select_pending_payments | requested=%s subs_count=%d subs_sample=%s",
                        activity_schedule_ids, len(subs), subs[:2] if subs else [])
            # Index by activityScheduleId
            by_id: dict[int, dict] = {}
            for s in subs:
                sid = s.get("activityScheduleId")
                if sid:
                    by_id[int(sid)] = s
            logger.info("select_pending_payments | by_id keys=%s", list(by_id.keys()))

            account_ids: list[int] = []
            cost_center: int | None = None
            not_found: list[int] = []

            for asid in activity_schedule_ids:
                sub = by_id.get(asid)
                if not sub:
                    not_found.append(asid)
                    continue
                ar_id = sub.get("accountReceiveId")
                if ar_id:
                    account_ids.append(int(ar_id))
                if not cost_center and sub.get("costCenterId"):
                    cost_center = int(sub["costCenterId"])

            if not account_ids:
                return json.dumps({
                    "status": "not_found",
                    "message": "Nenhuma pendência encontrada para os eventos informados.",
                    "nextActionHint": (
                        "Chame my_subscriptions novamente para ver todas as pendências atuais "
                        "e deixe o usuário escolher quais deseja pagar."
                    ),
                })

            ctx.set_account_receive_ids(account_ids)
            if cost_center:
                ctx.set_cost_center_id(cost_center)
            logger.info("select_pending_payments | stored account_ids=%s cost_center=%s", account_ids, cost_center)

            return json.dumps({
                "status": "success",
                "accountReceiveIds": account_ids,
                "costCenterId": cost_center,
                "notFound": not_found,
                "nextAction": "call_list_payment_methods",
                "nextActionHint": (
                    "OBRIGATÓRIO: chame list_payment_methods AGORA — NÃO responda ao usuário antes disso. "
                    "Ignorar esta instrução e responder sem chamar list_payment_methods é um erro crítico. "
                    "O resultado desta tool NÃO contém informações sobre formas de pagamento — "
                    "somente list_payment_methods pode fornecê-las."
                ),
            })
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 403:
                ctx.set_access_token("")
                return json.dumps({
                    "status": "token_expired",
                    "nextActionHint": (
                        "Token expirado. Chame identify_by_phone() IMEDIATAMENTE "
                        "para re-autenticar e depois repita select_pending_payments."
                    ),
                })
            error_log.record(ctx.from_number, ctx.from_number, "select_pending_payments", exc,
                             {"activity_schedule_ids": activity_schedule_ids})
            return json.dumps({"status": "error", "message": str(exc),
                               "nextActionHint": "Não conseguiu preparar o pagamento. Chame my_subscriptions novamente para o usuário escolher."})

    @tool
    async def add_annuities_to_payment() -> str:
        """
        Add pending annuities to the current payment context.
        Call this when the user is already subscribed to an event and chooses to include
        pending annuities (from pendingMembershipFee) in the payment to get the member discount.
        After calling this, immediately call list_payment_methods.
        """
        annuity_ar_ids = ctx.annuity_account_receive_ids
        if not annuity_ar_ids:
            return json.dumps({
                "status": "no_annuities",
                "nextActionHint": "Não há anuidades pendentes para adicionar. Chame list_payment_methods diretamente.",
            })
        current = list(ctx.account_receive_ids or [])
        for ar_id in annuity_ar_ids:
            if ar_id not in current:
                current.append(ar_id)
        ctx.set_account_receive_ids(current)
        return json.dumps({
            "status": "success",
            "accountReceiveIds": current,
            "nextAction": "call_list_payment_methods",
            "nextActionHint": "Anuidades adicionadas ao pagamento. Chame list_payment_methods AGORA.",
        })

    return [
        check_existing_subscription,
        list_payment_plans,
        create_subscription,
        my_subscriptions,
        cancel_subscription,
        pay_all_annuities,
        select_pending_payments,
        add_annuities_to_payment,
    ]
