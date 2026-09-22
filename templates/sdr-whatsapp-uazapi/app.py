"""SDR template — FastAPI app (webhook + background cron).

Mirrors sdr-plus-um-passo/app.py and wires the full SDR runtime:

  - lifespan: (optionally) register the uazapi webhook APPEND-only [guarded by
    env so tests never hit the network], start the follow-up dispatcher asyncio
    task, start the APScheduler daily-advance job; cancel/shutdown on exit.
  - POST /{slug}/webhook: normalize_payload -> filters (is_bot_sent / is_allowed
    / empty / dedup) -> MessageBuffer.push -> schedule a debounced flush. The
    flush builds the combined text and runs it through agent.arun (session_id=
    f"{slug}-{phone}", user_id=phone), then send_reply. Inbound media is routed
    through media_handler first and its transcript/extraction fed to the agent.
  - /health endpoint.

Follow-ups are dispatched THROUGH agent.arun() (decision 4). uvicorn --workers 1
(async engine is not fork-safe).

`create_app` accepts injectable collaborators (agent, send_reply, register_fn,
conn_factory, media transcriber) so integration tests can run the webhook +
dispatcher without touching the network or the live model.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

# Make the bf-agents repo root importable so `shared` resolves, regardless of
# how deep this agent folder is nested (blueprints/sdr-template vs sdr-plus-um-passo).
_d = os.path.dirname(os.path.abspath(__file__))
while _d != "/" and not os.path.isdir(os.path.join(_d, "shared")):
    _d = os.path.dirname(_d)
if _d not in sys.path:
    sys.path.insert(0, _d)

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv optional
    pass

from shared.webhook_core import (
    MessageDedup,
    is_allowed,
    is_bot_sent,
    normalize_payload,
)
from shared.webhook_core import send_reply as _default_send_reply

from config import (
    BUSINESS_HOURS,
    CADENCE_SCHEDULE,
    CHANNEL,
    CRM,
    HANDOFF,
    make_followup_content_provider,
)
from core import lead_manager
from core.message_buffer import MessageBuffer
from cron import daily_advance
from cron.followup_dispatcher import run_dispatcher_loop

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger("sdr")

SLUG = os.getenv("SLUG", "sdr")
TABLE_PREFIX = os.getenv("TABLE_PREFIX", "")
BUFFER_WINDOW = float(os.getenv("BUFFER_WINDOW_SECONDS", "8"))
DISPATCH_INTERVAL = float(os.getenv("DISPATCH_INTERVAL_SECONDS", "30"))


def _phone_prefix(jid: str) -> str:
    return (jid or "").split("@")[0]


async def _default_conn_factory():
    """Open a raw psycopg AsyncConnection for cron jobs (prod default).

    Uses the plain postgresql:// DSN (psycopg form) derived from DATABASE_URL.
    """
    import psycopg

    dsn = os.getenv(
        "DATABASE_URL_PSYCOPG",
        os.getenv("DATABASE_URL", "postgresql://user:password@localhost:5432/sdr_template")
        .replace("postgresql+psycopg_async://", "postgresql://"),
    )
    return await psycopg.AsyncConnection.connect(dsn, autocommit=True)


def create_app(
    agent: Any | None = None,
    send_reply: Callable | None = None,
    register_fn: Callable | None = None,
    conn_factory: Callable | None = None,
    media_transcriber: Callable | None = None,
    crm: Any | None = None,
    slug: str = SLUG,
    table_prefix: str = TABLE_PREFIX,
    buffer_window: float = BUFFER_WINDOW,
    start_background: bool | None = None,
    allowed_senders: set[str] | None = None,
) -> FastAPI:
    """Build and return the FastAPI app (lifespan, /health, /{slug}/webhook).

    Collaborators default to the real ones at first use, but every one can be
    injected for tests. `start_background` controls whether lifespan launches
    the dispatcher loop + scheduler (default: env SDR_START_BACKGROUND != "0").
    """
    if agent is None:
        from agent import agent as agent  # lazy: avoid model import in some tests
    send = send_reply or _default_send_reply
    cfac = conn_factory or _default_conn_factory
    crm_client = crm

    if start_background is None:
        start_background = os.getenv("SDR_START_BACKGROUND", "1") != "0"

    dedup = MessageDedup()
    buffer = MessageBuffer(window_seconds=buffer_window)
    _pending_flushes: set[asyncio.Task] = set()

    deps_template = {
        "table_prefix": table_prefix,
        "handoff_config": HANDOFF,
        "business_hours": BUSINESS_HOURS,
        "crm_client": crm_client,
    }

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks: list[asyncio.Task] = []
        scheduler = None

        # Register webhook (APPEND-only) — guarded so tests never hit network.
        if start_background and os.getenv("SDR_REGISTER_WEBHOOK", "0") == "1":
            try:
                reg = register_fn
                if reg is None:
                    from channels.uazapi import register_webhook as reg
                webhook_url = os.getenv("WEBHOOK_URL", "")
                await reg(
                    CHANNEL.get("base", ""),
                    CHANNEL.get("token", ""),
                    CHANNEL.get("instance", ""),
                    webhook_url,
                )
                logger.info("uazapi webhook registered: %s", webhook_url)
            except Exception:
                logger.error("webhook registration failed", exc_info=True)

        if start_background:
            # Follow-up dispatcher loop.
            tasks.append(
                asyncio.create_task(
                    run_dispatcher_loop(
                        cfac, table_prefix, agent, send,
                        interval_seconds=DISPATCH_INTERVAL, crm=crm_client,
                    )
                )
            )
            # Daily cadence advance (APScheduler).
            try:
                from apscheduler.schedulers.asyncio import AsyncIOScheduler

                from cron.cadence_scheduler import schedule_cadence_job

                scheduler = AsyncIOScheduler(timezone=BUSINESS_HOURS.get("timezone"))

                _content_provider = make_followup_content_provider()

                async def _job():
                    await daily_advance.run_daily_advance(
                        cfac, table_prefix, CADENCE_SCHEDULE,
                        crm=crm_client, crm_stages=CRM.get("stages"),
                        content_provider=_content_provider,
                    )

                schedule_cadence_job(
                    scheduler, func=_job, timezone=BUSINESS_HOURS.get("timezone")
                )
                scheduler.start()
                logger.info("daily cadence scheduler started")
            except Exception:
                logger.error("scheduler start failed", exc_info=True)

        try:
            yield
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.error("background task shutdown error", exc_info=True)
            if scheduler is not None:
                try:
                    scheduler.shutdown(wait=False)
                except Exception:
                    logger.error("scheduler shutdown error", exc_info=True)

    app = FastAPI(title=f"SDR Template ({slug})", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "agent": slug}

    async def _run_agent_and_reply(phone: str, chat_jid: str, text: str, reply_to: str):
        """Run the combined buffered text through the agent and reply."""
        session_id = f"{slug}-{phone}"
        deps = dict(deps_template)
        try:
            conn = await cfac()
        except Exception:
            conn = None
            logger.error("agent run: DB connection failed (continuing w/o conn)", exc_info=True)
        deps["conn"] = conn
        try:
            result = await agent.arun(
                text, session_id=session_id, user_id=phone, dependencies=deps
            )
            out = getattr(result, "content", None)
            if out:
                await send(chat_jid, out, reply_to)
                logger.info("replied to %s (%d chars)", chat_jid, len(out))
            else:
                logger.warning("agent produced empty output for %s", chat_jid)
        except Exception:
            logger.error("agent run failed for %s", chat_jid, exc_info=True)
        finally:
            if conn is not None:
                close = getattr(conn, "close", None)
                if close is not None:
                    res = close()
                    if asyncio.iscoroutine(res):
                        await res

    async def _flush_after_window(phone: str, chat_jid: str, reply_to: str):
        """Debounced flush: wait the window, and if no newer push arrived, flush.

        MessageBuffer.push() resets last_ts on every new part, so a later push
        leaves this sender out of due() and this task no-ops (the later push's
        own task does the flush).
        """
        await asyncio.sleep(buffer_window)
        if phone not in buffer.due(_now()):
            return
        combined = buffer.flush(phone)
        if not combined:
            return
        await _run_agent_and_reply(phone, chat_jid, combined, reply_to)

    @app.post("/" + slug + "/webhook")
    async def webhook(request: Request, bg: BackgroundTasks):
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)

        media = _extract_media(payload)
        msg = normalize_payload(payload)
        if msg is None:
            # normalize_payload drops empty-text messages. A media-only message
            # is still actionable — synthesize a minimal msg dict from raw data.
            if media is not None:
                msg = _synthesize_media_msg(payload)
            if msg is None:
                return JSONResponse({"ok": True, "skipped": "not_message"})
        if is_bot_sent(msg):
            logger.info("skip bot_sent msg_id=%s", msg.get("msg_id"))
            return JSONResponse({"ok": True, "skipped": "bot_sent"})
        if not is_allowed(msg["sender_jid"], allowed_senders):
            logger.info("skip not_allowed sender=%s", msg.get("sender_jid"))
            return JSONResponse({"ok": True, "skipped": "not_allowed"})

        # Resolve media into text (if any) so we always have something to buffer.
        text = msg["text"]
        if media is not None:
            transcribe = media_transcriber
            if transcribe is None:
                from core.media_handler import transcribe_or_extract as transcribe
            try:
                extracted = await transcribe(
                    media["url"], media["type"], _multimodal_caller
                )
                text = (text + "\n" + extracted).strip() if text else extracted
            except Exception:
                logger.error("media processing failed", exc_info=True)

        if not text:
            return JSONResponse({"ok": True, "skipped": "empty"})

        if msg["msg_id"] and dedup.seen(msg["msg_id"]):
            return JSONResponse({"ok": True, "skipped": "duplicate"})
        if msg["msg_id"]:
            dedup.mark(msg["msg_id"])

        chat_jid = msg["group_jid"] if msg["is_group"] else msg["sender_jid"]
        phone = _phone_prefix(chat_jid)

        buffer.push(phone, text, _now())
        # Schedule the debounced flush immediately (not via BackgroundTasks),
        # so the window starts at push time and a later push within the window
        # correctly resets the debounce. Keep a reference so it isn't GC'd.
        task = asyncio.create_task(_flush_after_window(phone, chat_jid, msg["msg_id"]))
        _pending_flushes.add(task)
        task.add_done_callback(_pending_flushes.discard)
        return JSONResponse({"ok": True})

    return app


def _now() -> float:
    import time

    return time.monotonic()


def _extract_media(payload: dict) -> dict | None:
    """Pull a media descriptor {url,type} out of a uazapi payload, or None.

    Tolerant of Format A/B shapes; returns None when there is no media.
    """
    data = payload.get("data") or payload.get("message") or {}
    media_url = (
        data.get("mediaUrl")
        or data.get("media_url")
        or data.get("fileUrl")
        or data.get("url")
    )
    if not media_url:
        return None
    mtype = (
        data.get("mediaType")
        or data.get("media_type")
        or data.get("messageType")
        or "document"
    ).lower()
    for kind in ("audio", "image", "video"):
        if kind in mtype:
            mtype = kind
            break
    else:
        mtype = "document"
    return {"url": media_url, "type": mtype}


def _synthesize_media_msg(payload: dict) -> dict | None:
    """Build a minimal normalized msg dict for a media-only payload.

    normalize_payload returns None when text is empty, but a media message is
    still actionable. Mirrors normalize_payload's key shape so the rest of the
    webhook handler is uniform.
    """
    import re

    data = payload.get("data") or payload.get("message") or {}
    chat_jid = data.get("chatId") or data.get("chatid") or ""
    if not chat_jid:
        return None
    sender_jid = data.get("senderJid") or chat_jid
    return {
        "group_jid": chat_jid,
        "text": "",
        "sender_name": re.sub(r"@.*$", "", sender_jid) if sender_jid else "",
        "msg_id": data.get("messageId") or data.get("messageid") or "",
        "sender_jid": sender_jid,
        "is_group": data.get("isGroup", False),
        "from_me": data.get("fromMe", False),
        "source": data.get("source") or "",
    }


async def _multimodal_caller(media_url: str, media_type: str) -> str:
    """Default multimodal model caller (MiMo v2.5 regular — non-pro).

    Wraps the regular multimodal model to transcribe/extract. Only invoked in
    production; tests inject their own transcriber so this never hits the model.
    """
    from shared.model_factory import nine_router, omniroute

    model = nine_router(os.getenv("AGNO_MULTIMODAL_MODEL", "mimo/mimo-v2.5"))
    # O formato exato da chamada multimodal depende do modelo configurado no gateway;
    # production wiring fills this in. Kept minimal to avoid network in import.
    raise NotImplementedError(
        "multimodal model caller must be wired with the omniroute media adapter"
    )


if __name__ == "__main__":
    import uvicorn

    app = create_app()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "7780")),
        workers=1,
        reload=False,
    )
