"""Persistent store for operator notes attached to a session (phone number)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("italo.operator_notes")

_DDL = """
CREATE TABLE IF NOT EXISTS operator_notes (
    id         SERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    note       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
)
"""

_DDL_INDEX = """
CREATE INDEX IF NOT EXISTS idx_operator_notes_session_id ON operator_notes (session_id)
"""


def ensure_table() -> None:
    from app.db import get_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_DDL)
        cur.execute(_DDL_INDEX)
        conn.commit()
    finally:
        conn.close()


def get_for_session(session_id: str) -> list[dict]:
    try:
        from app.db import get_dict_conn
        conn = get_dict_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, session_id, note, created_at, updated_at FROM operator_notes "
            "WHERE session_id = %s ORDER BY created_at ASC",
            (session_id,),
        )
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("operator_notes_store.get_for_session: %s", exc)
        return []


def create(session_id: str, note: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    try:
        from app.db import get_dict_conn
        conn = get_dict_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO operator_notes (session_id, note, created_at, updated_at)
            VALUES (%s, %s, %s, %s)
            RETURNING id, session_id, note, created_at, updated_at
            """,
            (session_id, note, now, now),
        )
        row = cur.fetchone()
        conn.commit()
        conn.close()
        return dict(row)
    except Exception as exc:
        logger.error("operator_notes_store.create: %s", exc)
        raise


def delete(id: int) -> bool:
    try:
        from app.db import get_conn
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM operator_notes WHERE id = %s", (id,))
        affected = cur.rowcount
        conn.commit()
        conn.close()
        return affected > 0
    except Exception as exc:
        logger.error("operator_notes_store.delete: %s", exc)
        return False


# Backward-compat shim — old code called init(db_path)
def init() -> None:
    """No-op kept only so old callers that still pass no args don't break."""
    ensure_table()
