"""Persistence layer for messages sent by admins via the dashboard.

These bypass the agent runtime, so they're not in agno_sessions.runs.
We store them in a dedicated table and merge into chat history at read time.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.db import get_conn, get_dict_conn

logger = logging.getLogger("italo.admin_msg")

_DDL = """
CREATE TABLE IF NOT EXISTS admin_sent_messages (
    id            SERIAL PRIMARY KEY,
    phone         TEXT NOT NULL,
    content       TEXT NOT NULL,
    template_name TEXT,
    wamid         TEXT,
    sent_at       TEXT NOT NULL,
    sent_by       TEXT DEFAULT 'admin'
)
"""

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_admin_sent_phone ON admin_sent_messages (phone)",
    "CREATE INDEX IF NOT EXISTS idx_admin_sent_sent_at ON admin_sent_messages (sent_at)",
]


def init_db() -> None:
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(_DDL)
        for idx in _DDL_INDEXES:
            cur.execute(idx)
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.error("init_db: %s", exc)


def insert(
    phone: str,
    content: str,
    template_name: str | None = None,
    wamid: str | None = None,
) -> None:
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO admin_sent_messages (phone, content, template_name, wamid, sent_at) VALUES (%s, %s, %s, %s, %s)",
            (phone, content, template_name, wamid, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.error("insert: %s", exc)


def query_by_phone(phone: str, limit: int = 500) -> list[dict]:
    try:
        conn = get_dict_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT content, template_name, wamid, sent_at FROM admin_sent_messages WHERE phone = %s ORDER BY sent_at ASC LIMIT %s",
            (phone, limit),
        )
        rows = cur.fetchall()
        conn.close()
        return [
            {
                "role": "assistant",
                "content": (
                    f"📋 Template: {r['template_name']}" if r["template_name"]
                    else r["content"]
                ),
                "created_at": r["sent_at"],
                "from_admin": True,
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error("query_by_phone: %s", exc)
        return []


# Auto-init on import
try:
    init_db()
except Exception as exc:
    logger.warning("admin_message_log: init_db falhou no import — writes vão falhar depois: %s", exc, exc_info=True)
