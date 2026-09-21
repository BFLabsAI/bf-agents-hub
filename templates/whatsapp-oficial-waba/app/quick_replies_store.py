"""Persistent store for quick-reply snippets used in the admin dashboard."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger("italo.quick_replies")

_DDL = """
CREATE TABLE IF NOT EXISTS quick_replies (
    id         SERIAL PRIMARY KEY,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
)
"""


def ensure_table() -> None:
    from app.db import get_conn
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_DDL)
        conn.commit()
    finally:
        conn.close()


def list_all() -> list[dict]:
    try:
        from app.db import get_dict_conn
        conn = get_dict_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, title, body, created_at, updated_at FROM quick_replies ORDER BY title ASC"
        )
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("quick_replies_store.list_all: %s", exc)
        return []


def create(title: str, body: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    try:
        from app.db import get_dict_conn
        conn = get_dict_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO quick_replies (title, body, created_at, updated_at)
            VALUES (%s, %s, %s, %s)
            RETURNING id, title, body, created_at, updated_at
            """,
            (title, body, now, now),
        )
        row = cur.fetchone()
        conn.commit()
        conn.close()
        return dict(row)
    except Exception as exc:
        logger.error("quick_replies_store.create: %s", exc)
        raise


def delete(id: int) -> bool:
    try:
        from app.db import get_conn
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM quick_replies WHERE id = %s", (id,))
        affected = cur.rowcount
        conn.commit()
        conn.close()
        return affected > 0
    except Exception as exc:
        logger.error("quick_replies_store.delete: %s", exc)
        return False


# Backward-compat shim — old code called init(db_path)
def init() -> None:
    """No-op kept only so old callers that still pass no args don't break."""
    ensure_table()
