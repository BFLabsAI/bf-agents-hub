import asyncio
import json
import logging
import re
import sys
from typing import Any

import httpx

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)

from app.config import WABA_WEBHOOK_VERIFY_TOKEN, validate_config, SESSION_DB_PATH, PAYMENT_WEBHOOK_SECRET, WABA_PHONE_NUMBER_ID
import app.payment_webhook_log as pwlog
import app.payment_generated_log as pglog
import app.llm_usage_log as llm_log
import app.error_log as error_log
import app.quick_replies_store as qr_store
import app.operator_notes_store as notes_store
import app.operators_store as operators_store
from app.waba_client import WABAClient
from app.whatsapp_bridge import WABABridge, parse_waba_payload, verify_signature
from app.agent_factory import create_agent
from app.log_store import log_store
from app.admin_router import router as admin_router
from app.message_buffer import message_buffer

logger = logging.getLogger("whatsapp_agent")

app = FastAPI(title="Agno WhatsApp Agent (WABA)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_router)

# ── Static files (generated images, receipts) ─────────────────────────────────
_STATIC_DIR = Path(__file__).parent.parent / "static"
_STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

waba = WABAClient()
bridge = WABABridge(
    agent_factory=lambda **kw: create_agent(**kw, waba_client=waba)
)

from app import pause_registry as _pause_registry
_pause_registry.set_bridge(bridge)


@app.on_event("startup")
async def startup() -> None:
    validate_config()
    import app.db as _db
    from app.config import DATABASE_URL
    _db.init(DATABASE_URL)
    import app.agno_session_store as session_store
    session_store.ensure_table()
    import app.user_log as ulog
    pwlog.ensure_table()
    pglog.ensure_table()
    ulog.ensure_table()
    llm_log.init()
    error_log.init()
    qr_store.init()
    notes_store.init()
    operators_store.init()
    from app.auto_resume import start_auto_resume_loop
    asyncio.create_task(start_auto_resume_loop())
    logger.info("WhatsApp Agent WABA webhook is up (PostgreSQL).")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/webhooks/waba")
def verify_webhook(
    hub_mode: str | None = None,
    hub_verify_token: str | None = None,
    hub_challenge: str | None = None,
):
    if hub_mode == "subscribe" and hub_verify_token == WABA_WEBHOOK_VERIFY_TOKEN:
        logger.info("Webhook verified by Meta.")
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhooks/waba")
async def receive_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not verify_signature(raw_body, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    payload = await request.json()
    msg = parse_waba_payload(payload)

    if msg is None:
        return {"ok": True, "skipped": True}

    if WABA_PHONE_NUMBER_ID and msg.phone_number_id != WABA_PHONE_NUMBER_ID:
        return {"ok": True, "skipped": True}

    asyncio.create_task(_preprocess_and_buffer(msg))
    return {"ok": True}


async def _preprocess_and_buffer(msg) -> None:
    """
    Run multimodal preprocessing (audio/image/PDF) BEFORE the message buffer,
    so that by the time the buffer flushes the message into _handle_message,
    msg.text already contains plain text the main agent can understand.
    Fires immediately so the user sees a typing indicator while Gemini works.
    """
    if msg.media_id and msg.media_kind:
        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(_typing_keepalive(msg.wamid, stop_typing))
        try:
            try:
                binary, actual_mime = await waba.download_media(msg.media_id)
                mime = actual_mime or msg.media_mime or ""
                from app.media_processor import process_media
                extracted = await process_media(binary, mime, msg.media_kind)
                label = {"audio": "áudio", "image": "imagem", "document": "PDF"}.get(msg.media_kind, "mídia")
                caption_part = f' (legenda: "{msg.media_caption}")' if msg.media_caption else ""
                msg.text = f"[{label} enviado pelo usuário{caption_part}]: {extracted}"
                logger.info("media preprocessed | kind=%s chars=%d", msg.media_kind, len(extracted))
            except Exception as e:
                logger.error("media preprocessing failed: %s", e)
                fallback = {
                    "audio": "Não consegui ouvir seu áudio agora. Pode digitar a mensagem?",
                    "image": "Não consegui analisar a imagem agora. Pode descrever em texto?",
                    "document": "Não consegui ler o PDF agora. Pode descrever o que precisa?",
                }.get(msg.media_kind, "Não consegui processar essa mídia. Pode tentar em texto?")
                await waba.send_text(to=msg.from_number, body=fallback, reply_to_wamid=msg.wamid)
                return
        finally:
            stop_typing.set()
            typing_task.cancel()

    await message_buffer.add(msg, _handle_message)


def _flush_session(from_number: str) -> bool:
    """Delete session from DB and evict from in-memory agent pool. Returns True if found."""
    from app.agno_session_store import delete_session
    session_id = f"agent-wa-{from_number}"

    # Remove from in-memory pool
    bridge._runtime._agents.pop(session_id, None)

    # Remove from DB (Postgres agno_sessions)
    deleted = delete_session(session_id)
    logger.info("flush | session_id=%s deleted=%d", session_id, deleted)
    return deleted > 0

async def _handle_receipt_request(msg) -> None:
    """Handle the 'Emitir Recibo' button click: fetch receipt HTML → extract PDF → send as document."""
    parts = msg.text.strip().split()
    try:
        account_receive_id = int(parts[1])
    except (IndexError, ValueError):
        await waba.send_text(to=msg.from_number, body="Não consegui identificar o recibo. Tente novamente.", reply_to_wamid=msg.wamid)
        return

    session_id = f"agent-wa-{msg.from_number}"
    person = _lookup_person_by_id_from_session(session_id)
    access_token = (person or {}).get("access_token", "")

    if not access_token:
        await waba.send_text(to=msg.from_number, body="Não consegui autenticar. Por favor, inicie uma conversa normalmente.", reply_to_wamid=msg.wamid)
        return

    sub_info = await _fetch_subscription_info(access_token, account_receive_id)
    link_receipt = sub_info.get("link_receipt", "")
    event_title = sub_info.get("event_title", "") or "recibo"

    if not link_receipt:
        await waba.send_text(to=msg.from_number, body="Recibo ainda não disponível. Tente novamente em instantes.", reply_to_wamid=msg.wamid)
        return

    try:
        from app.pdf_renderer import render_receipt_pdf
        pdf_bytes = await render_receipt_pdf(link_receipt)
        slug = event_title[:40].lower().replace(" ", "-")
        filename = f"recibo-{slug}.pdf"
        await waba.send_document_from_bytes(
            to=msg.from_number,
            pdf_bytes=pdf_bytes,
            filename=filename,
            caption="🧾 Seu recibo de pagamento",
        )
        log_store.complete_transaction("recibo pdf enviado")
    except Exception as exc:
        logger.error("_handle_receipt_request | render_receipt_pdf failed: %s", exc)
        await waba.send_text(
            to=msg.from_number,
            body=f"🧾 *Seu Recibo*\n\n{link_receipt}",
            reply_to_wamid=msg.wamid,
        )
        log_store.complete_transaction("recibo link enviado")


def _lookup_person_by_id_from_session(session_id: str) -> dict | None:
    """Look up access_token from session by session_id directly (Postgres agno_sessions)."""
    from app.agno_session_store import get_session_state
    state = get_session_state(session_id)
    if not state:
        return None
    return {
        "access_token": state.get("access_token", ""),
        "first_name": state.get("first_name", ""),
    }

async def _typing_keepalive(wamid: str, stop: asyncio.Event) -> None:
    """Refresh the typing indicator every 20s while the agent is thinking."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(asyncio.shield(stop.wait()), timeout=20.0)
        except asyncio.TimeoutError:
            if not stop.is_set():
                await waba.send_typing(wamid)


async def _handle_message(msg) -> None:
    session_id = bridge.session_id_for(msg)
    log_store.new_transaction(session_id, msg.from_number, msg.text)
    try:
        await waba.mark_as_read(msg.wamid)

        # ── /flush command ────────────────────────────────────────────────────
        if msg.text.strip().lower() == "/flush":
            found = _flush_session(msg.from_number)
            reply = "Sessão apagada. Próxima mensagem começa do zero." if found else "Nenhuma sessão ativa encontrada."
            await waba.send_text(to=msg.from_number, body=reply, reply_to_wamid=msg.wamid)
            log_store.complete_transaction(reply)
            return

        # ── /recibo {accountReceiveId} — botão "Emitir Recibo" ───────────────
        if msg.text.strip().startswith("/recibo "):
            await _handle_receipt_request(msg)
            return
        # ─────────────────────────────────────────────────────────────────────

        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(_typing_keepalive(msg.wamid, stop_typing))
        try:
            reply = await bridge.generate_reply(msg)
        finally:
            stop_typing.set()
            typing_task.cancel()

        log_store.complete_transaction(reply)
        if reply:
            await waba.send_text(
                to=msg.from_number,
                body=reply,
                reply_to_wamid=msg.wamid,
            )
    except Exception as exc:
        log_store.fail_transaction(str(exc))
        logger.error("Failed to handle message %s: %s", msg.wamid, exc)


# ── Payment confirmation webhook ──────────────────────────────────────────────

def _normalize_phone(raw: str) -> str:
    """Normalize any BR phone format to E.164 digits (no +) for WhatsApp API."""
    digits = re.sub(r"\D", "", raw)
    # Strip international dialing prefixes (00, 0011, etc.) and leading zeros
    digits = re.sub(r"^0+", "", digits)
    # Add BR country code if missing (number without 55 prefix)
    if not digits.startswith("55"):
        digits = "55" + digits
    return digits


def _lookup_person_by_id(person_id: int) -> dict | None:
    """Look up phone, name, and access_token from Postgres agno_sessions cache by personId."""
    from app.agno_session_store import find_session_state_by_person_id
    res = find_session_state_by_person_id(person_id)
    if not res:
        return None
    return {
        "phone": res.get("from_number", ""),
        "name": res.get("first_name", ""),
        "access_token": res.get("access_token", ""),
    }


async def _fetch_subscription_info(access_token: str, account_receive_id: int) -> dict:
    """
    Fetch event title and payment method label for a given accountReceiveId
    using the user's iTarget access token.
    Returns {'event_title': '...', 'payment_method': '...'} — empty strings if not found.
    """
    if not access_token or not account_receive_id:
        return {}
    try:
        async with httpx.AsyncClient(base_url=ITARGET_API_BASE_URL, timeout=10.0) as client:
            resp = await client.get(
                "/api/subscription/persons/my-subscription",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"origin": "agent"},
            )
            resp.raise_for_status()
            body = resp.json()
            tabs_data = (body.get("data") or {}).get("tabs", {}).get("data", {})
            if isinstance(tabs_data, dict):
                all_items: list[dict] = []
                for items in tabs_data.values():
                    if isinstance(items, list):
                        all_items.extend(items)
                for item in all_items:
                    if item.get("accountReceiveId") == account_receive_id:
                        title = (
                            item.get("activityScheduleDescription")
                            or item.get("activityDescription")
                            or ""
                        )
                        method_raw = item.get("controlBankCardTypeOriginDescription") or ""
                        # Strip "translation." prefix used by the iTarget API
                        method = method_raw.replace("translation.", "").strip()
                        return {
                            "event_title": title,
                            "payment_method": method,
                            "bank_token": item.get("bankToken") or "",
                            "link_receipt": item.get("linkReceipt") or "",
                        }
    except Exception as exc:
        logger.warning("_fetch_subscription_info | failed for accountReceiveId=%s: %s", account_receive_id, exc)
    return {}


def _extract_field(body: dict, *keys):
    """Try multiple field name variants and return the first non-empty value."""
    for k in keys:
        v = body.get(k)
        if v is not None and v != "":
            return v
    return None


def _build_confirmation_message(body: dict) -> str:
    name_raw = _extract_field(body, "person_name", "name", "nome", "personName", "userName")
    name = name_raw.split()[0].capitalize() if name_raw else None

    event_name = _extract_field(body, "event_name", "eventName", "event", "evento", "activity", "activityDescription", "activityScheduleDescription", "title")

    amount_raw = _extract_field(body, "amount", "valor", "value", "totalAmount", "total_amount", "amountWithDiscount")
    amount_str: str | None = None
    if amount_raw is not None:
        try:
            amount_str = f"R$ {float(amount_raw):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except (ValueError, TypeError):
            pass

    method_raw = _extract_field(body, "payment_method", "paymentMethod", "method", "metodo", "tipo_pagamento", "paymentType")
    method_labels = {
        "pix": "PIX", "boleto": "Boleto", "cartao": "Cartão",
        "cartão": "Cartão", "credit_card": "Cartão", "bank_slip": "Boleto",
        "creditcard": "Cartão", "bankslip": "Boleto",
        # iTarget API returns these after stripping "translation." prefix
        "bankpayment": "Boleto", "card": "Cartão",
    }
    method_key = str(method_raw or "").lower().strip()
    method_str = method_labels.get(method_key, str(method_raw) if method_raw is not None else "")

    installments_raw = _extract_field(body, "installments", "parcelas", "installment")
    try:
        installments = int(installments_raw) if installments_raw else None
    except (ValueError, TypeError):
        installments = None

    opener = f"{name}, sua inscrição foi confirmada! 🎉" if name else "Sua inscrição foi confirmada! 🎉"
    event_line = f"📌 *{event_name}*" if event_name else None

    detail_parts = []
    if amount_str:
        detail_parts.append(amount_str)
    if method_str:
        detail_parts.append(f"via *{method_str}*")
    if installments and installments > 1:
        detail_parts.append(f"em {installments}x")
    details_line = f"💰 {' '.join(detail_parts)}" if detail_parts else None

    if not event_name:
        opener = f"Olá, {name}!" if name else "Olá!"
        body_line = "Seu pagamento foi confirmado."
    else:
        body_line = None

    closing = "Qualquer dúvida, estou por aqui."
    lines = [opener, ""]
    if body_line:
        lines.append(body_line)
    if event_line:
        lines.append(event_line)
    if details_line:
        lines.append(details_line)
    lines.append("")
    lines.append(closing)
    return "\n".join(lines)


@app.post("/webhooks/payment-confirmed")
async def payment_confirmed(
    request: Request,
    x_webhook_secret: str | None = Header(default=None),
):
    """
    Receive payment confirmation from iTarget and send a WhatsApp message to the user.
    Accepts any JSON payload — fields are extracted by multiple name variants.
    Authentication via X-Webhook-Secret header (enable by setting PAYMENT_WEBHOOK_SECRET env var).
    """
    raw_body = await request.body()
    try:
        body = json.loads(raw_body)
    except Exception:
        body = {}
    logger.info("payment_confirmed | raw_body=%s", body)

    # Auth check — disabled when PAYMENT_WEBHOOK_SECRET is empty (current default)
    # To enable: set PAYMENT_WEBHOOK_SECRET in .env
    if PAYMENT_WEBHOOK_SECRET and x_webhook_secret != PAYMENT_WEBHOOK_SECRET:
        pwlog.save("", "", body, "auth_failed", error="Invalid webhook secret")
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON object expected")

    # Flatten nested iTarget payload fields (payment.*, settlement.*) into top-level body
    # so _extract_field and _build_confirmation_message can find them without aliases.
    body = dict(body)
    for sub_key in ("payment", "settlement"):
        sub = body.get(sub_key)
        if isinstance(sub, dict):
            for k, v in sub.items():
                if k not in body:
                    body[k] = v

    phone_raw = _extract_field(body, "phone", "telefone", "celular", "phoneNumber", "phone_number", "whatsapp", "mobile")
    _session_person: dict | None = None  # set below if we resolve via personId

    # No phone in payload — try personId → SQLite session lookup
    if not phone_raw:
        person_id_raw = body.get("personId") or body.get("person_id")
        if person_id_raw:
            try:
                _session_person = _lookup_person_by_id(int(person_id_raw))
            except (ValueError, TypeError):
                _session_person = None
            if _session_person:
                phone_raw = _session_person["phone"]
                logger.info("payment_confirmed | resolved phone=%s via personId=%s", phone_raw, person_id_raw)
                if not _extract_field(body, "person_name", "name", "nome", "personName", "userName") and _session_person.get("name"):
                    body["person_name"] = _session_person["name"]
            else:
                logger.warning("payment_confirmed | personId=%s not found in session DB", person_id_raw)

    if not phone_raw:
        pwlog.save("", "", body, "missing_phone", error="No phone field found")
        raise HTTPException(status_code=422, detail="No phone field found in payload")

    to = _normalize_phone(str(phone_raw))
    if len(to) < 10:
        pwlog.save(str(phone_raw), to, body, "invalid_phone", error=f"Too short: {to!r}")
        raise HTTPException(status_code=422, detail=f"Invalid phone number: {phone_raw!r}")

    # Enrich body with event title and payment method from iTarget API when missing.
    # Uses the person's access_token (retrieved from session DB above) to call my-subscription.
    account_receive_id_raw = body.get("accountReceiveId")
    needs_event = not _extract_field(body, "event_name", "eventName", "event", "evento", "activity", "activityDescription", "activityScheduleDescription", "title")
    _method_val = _extract_field(body, "payment_method", "paymentMethod", "method", "metodo")
    # Treat numeric-only values (e.g. paymentMethod=8) as missing — they are internal IDs, not labels
    needs_method = not _method_val or str(_method_val).strip().isdigit()

    sub_info: dict = {}
    if account_receive_id_raw:
        access_token = (_session_person or {}).get("access_token", "")
        if not access_token:
            person_id_for_token = body.get("personId") or body.get("person_id")
            if person_id_for_token:
                p = _lookup_person_by_id(int(person_id_for_token))
                access_token = (p or {}).get("access_token", "")
        sub_info = await _fetch_subscription_info(access_token, int(account_receive_id_raw))
        if sub_info.get("event_title") and needs_event:
            body["event_name"] = sub_info["event_title"]
        if sub_info.get("payment_method"):
            body["payment_method"] = sub_info["payment_method"]
        logger.info("payment_confirmed | enriched event=%r method=%r", sub_info.get("event_title"), sub_info.get("payment_method"))

    message = _build_confirmation_message(body)
    logger.info(
        "payment_confirmed | to=%s event=%r",
        to, _extract_field(body, "event_name", "eventName", "event", "activity"),
    )

    # ── Build interactive message with image card + "Emitir Recibo" button ────
    event_name_final = _extract_field(body, "event_name", "eventName", "event", "activity", "activityScheduleDescription") or ""
    amount_raw_final = _extract_field(body, "amount", "valor", "value", "totalAmount", "total_amount", "amountWithDiscount", "settlementValue")
    method_final = _extract_field(body, "payment_method") or ""
    method_labels_final = {
        "pix": "PIX", "boleto": "Boleto", "cartao": "Cartão", "cartão": "Cartão",
        "credit_card": "Cartão", "bank_slip": "Boleto", "bankpayment": "Boleto", "card": "Cartão",
    }
    method_display = method_labels_final.get(str(method_final).lower().strip(), str(method_final))
    try:
        amount_display = f"R$ {float(amount_raw_final):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if amount_raw_final else ""
    except (ValueError, TypeError):
        amount_display = str(amount_raw_final) if amount_raw_final else ""

    # Pre-extract linking params for payment_generated_log.
    _ar_id_for_link: int | None = int(account_receive_id_raw) if account_receive_id_raw else None
    _person_id_for_link: int | None = None
    try:
        _pid_raw = body.get("personId") or body.get("person_id")
        if _pid_raw:
            _person_id_for_link = int(_pid_raw)
    except (TypeError, ValueError):
        pass
    _amount_for_link: float | None = None
    try:
        _amt_raw = _extract_field(body, "amount", "valor", "value", "totalAmount", "total_amount", "amountWithDiscount", "settlementValue")
        if _amt_raw:
            _amount_for_link = float(_amt_raw)
    except (TypeError, ValueError):
        pass

    interactive_sent = False
    if event_name_final and amount_display and account_receive_id_raw:
        try:
            from app.receipt_image import generate_confirmation_image
            img_path = await generate_confirmation_image(
                event_name=event_name_final,
                amount_str=amount_display,
                method=method_display or "Pagamento",
                account_receive_id=int(account_receive_id_raw),
            )
            # Upload image to Meta and get media_id
            media_id = await waba.upload_media(
                file_bytes=img_path.read_bytes(),
                filename=img_path.name,
                mime_type="image/png",
            )
            wa_resp = await waba.send_payment_confirmation_button(
                to=to,
                body_text=message,
                image_media_id=media_id,
                account_receive_id=int(account_receive_id_raw),
            )
            interactive_sent = True
            wh_id = pwlog.save(str(phone_raw), to, body, "sent", wa_response=wa_resp)
            pglog.try_link(wh_id, _ar_id_for_link, _person_id_for_link, _amount_for_link)
            logger.info("payment_confirmed | interactive sent to=%s", to)
        except Exception as exc:
            logger.warning("payment_confirmed | interactive failed, falling back to text: %s", exc)

    if not interactive_sent:
        try:
            wa_resp = await waba.send_text(to=to, body=message)
            wh_id = pwlog.save(str(phone_raw), to, body, "sent", wa_response=wa_resp)
            pglog.try_link(wh_id, _ar_id_for_link, _person_id_for_link, _amount_for_link)
        except Exception as exc:
            pwlog.save(str(phone_raw), to, body, "error", error=str(exc))
            logger.error("payment_confirmed | send failed to=%s: %s", to, exc)
            raise HTTPException(status_code=502, detail=f"WhatsApp send failed: {exc}")

    return {"ok": True, "sent_to": to}
