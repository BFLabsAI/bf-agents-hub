"""
Session pause registry — tracks which WhatsApp phones have AI paused (human takeover).

No circular imports: both whatsapp_bridge and admin_router import this module.
whatsapp_api.py calls set_bridge() after bridge creation.

Persistence: Postgres via app.db (migrated from SQLite).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("italo.pause")

_paused: set[str] = set()
_bridge: Any = None  # set by whatsapp_api after bridge creation

AUTO_RESUME_HOURS: float = 3.0  # auto-resume after this many hours if no new user reply


# ── bridge reference ─────────────────────────────────────────────────────────

def set_bridge(b: Any) -> None:
    global _bridge
    _bridge = b


def _evict(phone: str) -> None:
    """Evict agent from in-memory pool so it re-reads DB (with injected messages)."""
    if _bridge:
        try:
            _bridge._runtime.evict(phone)
        except Exception as exc:
            logger.warning("evict failed for %s: %s", phone, exc)


# ── pause state ───────────────────────────────────────────────────────────────

def pause(phone: str) -> None:
    _paused.add(phone)
    _db_set(phone, True)
    logger.info("AI paused for %s", phone)


def resume(phone: str) -> None:
    _paused.discard(phone)
    _db_set(phone, False)
    _evict(phone)
    logger.info("AI resumed for %s", phone)


def is_paused(phone: str) -> bool:
    return phone in _paused


def list_paused() -> list[str]:
    return list(_paused)


def get_paused_with_timestamps() -> list[dict]:
    """Return [{phone, paused_at}] for all currently-paused sessions."""
    try:
        from app.db import get_dict_conn
        conn = get_dict_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT phone, paused_at FROM paused_sessions WHERE paused = TRUE"
        )
        rows = cur.fetchall()
        conn.close()
        return [{"phone": r["phone"], "paused_at": r["paused_at"]} for r in rows]
    except Exception as exc:
        logger.warning("get_paused_with_timestamps: %s", exc)
        return []


# ── agno session injection ────────────────────────────────────────────────────

def inject_assistant_message(phone: str, content: str) -> None:
    """
    Insert a synthetic assistant run into agno_sessions.runs for `phone`.
    Agno loads the last N runs as history — this makes admin messages part of context.
    After injecting, evict the agent from pool so it re-reads from DB.
    Uses Postgres (agno_italo.agno_sessions).
    """
    try:
        from app.db import get_dict_conn
        conn = get_dict_conn()
        cur = conn.cursor()

        cur.execute(
            "SELECT session_id, agent_id, agent_data, runs FROM agno_sessions "
            "WHERE session_id LIKE %s ORDER BY updated_at DESC LIMIT 1",
            (f"%-wa-{phone}",),
        )
        row = cur.fetchone()

        if not row:
            conn.close()
            return

        session_id = row["session_id"]
        agent_id = row["agent_id"] or "italo-itarget-agent"
        # agno_sessions (schema rich) não tem coluna agent_name — vem de agent_data.name
        ad = row["agent_data"]
        if isinstance(ad, str):
            try:
                ad = json.loads(ad)
            except (json.JSONDecodeError, TypeError):
                ad = {}
        agent_name = (ad.get("name") if isinstance(ad, dict) else None) or "Ítalo"

        # runs é JSONB (lista); tolera str (legado double-encoded)
        raw = row["runs"]
        val = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(val, str):
            val = json.loads(val)
        runs: list = val if isinstance(val, list) else []

        now_ts = int(time.time())
        synthetic_run = {
            "run_id": str(uuid.uuid4()),
            "agent_id": agent_id,
            "agent_name": agent_name,
            "session_id": session_id,
            "content": content,
            "content_type": "text",
            "reasoning_content": None,
            "model_provider_data": None,
            "model": None,
            "model_provider": None,
            "session_state": {},
            "created_at": now_ts,
            "updated_at": now_ts,
            "status": "success",
            "metrics": None,
            "input": "[Admin takeover message]",
            "messages": [
                {
                    "id": str(uuid.uuid4()),
                    "content": content,
                    "reasoning_content": None,
                    "from_history": False,
                    "stop_after_tool_call": False,
                    "role": "assistant",
                    "provider_data": None,
                    "metrics": None,
                    "created_at": now_ts,
                }
            ],
            "tools": [],
        }

        runs.append(synthetic_run)

        # runs é JSONB — grava a lista como JSON (single-encoded) com cast ::jsonb
        cur.execute(
            "UPDATE agno_sessions SET runs = %s::jsonb, updated_at = %s WHERE session_id = %s",
            (json.dumps(runs), now_ts, session_id),
        )
        conn.commit()
        conn.close()

        _evict(phone)
        logger.info("injected admin message into session %s", session_id)

    except Exception as exc:
        logger.error("inject_assistant_message: %s", exc)


# ── Postgres persistence ──────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS paused_sessions (
    phone      TEXT PRIMARY KEY,
    paused     BOOLEAN NOT NULL DEFAULT TRUE,
    paused_at  TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL
);
"""


def init(path: str | None = None) -> None:
    """No-op shim kept for backwards compatibility (SQLite era)."""
    return None


def ensure_table() -> None:
    try:
        from app.db import get_conn
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(_DDL)
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("pause_registry.ensure_table: %s", exc)


def _db_set(phone: str, paused: bool) -> None:
    try:
        from app.db import get_conn
        conn = get_conn()
        now = datetime.now(timezone.utc).isoformat()
        paused_at = now if paused else None
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO paused_sessions (phone, paused, paused_at, updated_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (phone) DO UPDATE SET
                    paused     = EXCLUDED.paused,
                    paused_at  = EXCLUDED.paused_at,
                    updated_at = EXCLUDED.updated_at
                """,
                (phone, paused, paused_at, now),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("_db_set: %s", exc)


def load_from_db() -> None:
    """Reload persisted paused phones on startup."""
    try:
        from app.db import get_conn
        conn = get_conn()
        try:
            cur = conn.cursor()
            # ensure the table exists silently if it doesn't yet
            cur.execute(_DDL)
            conn.commit()
            cur.execute("SELECT phone FROM paused_sessions WHERE paused = TRUE")
            rows = cur.fetchall()
            for (phone,) in rows:
                _paused.add(phone)
        finally:
            conn.close()
        if _paused:
            logger.info("loaded %d paused sessions from DB", len(_paused))
    except Exception as exc:
        logger.warning("load_from_db: %s", exc)


# Run on import (best-effort — app.db may not be initialised yet)
try:
    load_from_db()
except Exception as exc:
    logger.warning("pause_registry: load_from_db falhou no import — sessões pausadas não foram carregadas: %s", exc, exc_info=True)
