"""Follow-up dispatcher — asyncio background task (runs inside app.py).

Polls due follow-ups (cadence_engine.due_followups), sends each THROUGH
agent.arun() so the sent message lands in Agno session history (the agent must
remember what it sent), marks the queue row sent, and logs it as a GHL note.

Logging contract (decision 2):
  - every dispatch  -> INFO
  - every failure   -> ERROR + stacktrace
  - every skip      -> WARNING
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from core import cadence_engine

logger = logging.getLogger("sdr.followup")

# How the dispatcher derives the agent session/user id from a phone (must match
# app.py's _handle so follow-ups land in the SAME Agno session as live chat).
_SLUG = "sdr"


def _session_id(slug: str, phone: str) -> str:
    return f"{slug}-{phone}"


def _followup_prompt(content: dict, cadence_day: int | None) -> str:
    """Build the instruction we feed agent.arun for a scheduled follow-up.

    The follow-up runs THROUGH the agent so the sent message lands in history.
    `content` is the queue row's content JSONB ({"type","text","media_url"}).
    """
    text = (content or {}).get("text") or ""
    return (
        "[FOLLOW-UP AUTOMATICO] Esta e a hora de um follow-up agendado da "
        f"cadencia (dia {cadence_day}). Envie esta mensagem de follow-up ao "
        f"lead, adaptando o tom naturalmente se necessario: {text}"
    )


async def _mark_sent(conn: Any, prefix: str, row_id: int, note_logged: bool) -> None:
    await conn.execute(
        f'UPDATE "{prefix}followup_queue" '
        "SET status = 'sent', ghl_note_logged = %s WHERE id = %s",
        (note_logged, row_id),
    )


async def _get_contact_id(conn: Any, prefix: str, phone: str) -> str | None:
    cur = await conn.execute(
        f'SELECT ghl_contact_id FROM "{prefix}leads" WHERE phone = %s', (phone,)
    )
    row = await cur.fetchone()
    return row[0] if row and row[0] else None


async def dispatch_due_followups(
    conn: Any,
    prefix: str,
    agent: Any,
    send_reply: Any,
    crm: Any | None = None,
    now: datetime | None = None,
    slug: str = _SLUG,
) -> int:
    """Process all currently-due follow-ups once.

    For each due row: run it through agent.arun() (session_id keyed by phone),
    send the agent's output via `send_reply`, mark the row 'sent', and log a GHL
    note via `crm` (if provided). Returns the number successfully dispatched.
    Honors the logging contract above.
    """
    now = now or datetime.now(timezone.utc)
    due = await cadence_engine.due_followups(conn, prefix, now)

    dispatched = 0
    for row in due:
        row_id = row["id"]
        phone = row["phone"]
        content = row.get("content") or {}
        cadence_day = row.get("cadence_day")

        try:
            session_id = _session_id(slug, phone)
            prompt = _followup_prompt(content, cadence_day)
            result = await agent.arun(prompt, session_id=session_id, user_id=phone)

            text = getattr(result, "content", None)
            if not text:
                logger.warning(
                    "follow-up skip: empty agent output id=%s phone=%s", row_id, phone
                )
                # Still mark sent so we don't loop forever on a dead row.
                await _mark_sent(conn, prefix, row_id, note_logged=False)
                continue

            jid = phone if "@" in phone else f"{phone}@s.whatsapp.net"
            await send_reply(jid, text, "")

            note_logged = False
            if crm is not None:
                try:
                    contact_id = await _get_contact_id(conn, prefix, phone)
                    if contact_id:
                        await crm.add_note(contact_id, f"Follow-up enviado: {text}")
                        note_logged = True
                except Exception:
                    logger.error(
                        "follow-up CRM note failed id=%s phone=%s", row_id, phone,
                        exc_info=True,
                    )

            await _mark_sent(conn, prefix, row_id, note_logged=note_logged)
            dispatched += 1
            logger.info(
                "follow-up dispatched id=%s phone=%s day=%s chars=%d note=%s",
                row_id, phone, cadence_day, len(text), note_logged,
            )
        except Exception:
            logger.error(
                "follow-up dispatch FAILED id=%s phone=%s", row_id, phone,
                exc_info=True,
            )

    return dispatched


async def run_dispatcher_loop(
    conn_factory: Any,
    prefix: str,
    agent: Any,
    send_reply: Any,
    interval_seconds: float = 30.0,
    crm: Any | None = None,
) -> None:
    """Long-running loop: every `interval_seconds`, dispatch due follow-ups.

    Started as an asyncio task in the FastAPI lifespan. `conn_factory` is an
    async callable yielding a DB connection per tick. Never raises out of the
    loop — logs and continues. Cancellation (shutdown) propagates cleanly.
    """
    logger.info("follow-up dispatcher loop started (interval=%ss)", interval_seconds)
    while True:
        try:
            conn = await conn_factory()
            try:
                count = await dispatch_due_followups(
                    conn, prefix, agent, send_reply, crm=crm
                )
                if count:
                    logger.info("follow-up tick dispatched=%d", count)
            finally:
                close = getattr(conn, "close", None)
                if close is not None:
                    res = close()
                    if asyncio.iscoroutine(res):
                        await res
        except asyncio.CancelledError:
            logger.info("follow-up dispatcher loop cancelled — shutting down")
            raise
        except Exception:
            logger.error("follow-up dispatcher tick crashed (continuing)", exc_info=True)

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("follow-up dispatcher loop cancelled — shutting down")
            raise
