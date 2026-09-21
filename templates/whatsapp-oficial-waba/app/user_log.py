from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("italo.user_log")


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


_DDL = """
CREATE TABLE IF NOT EXISTS users_sbot (
    id               TEXT PRIMARY KEY,
    person_id        INTEGER UNIQUE,
    name             TEXT,
    first_name       TEXT,
    phone            TEXT,
    session_id       TEXT,
    financial_status TEXT,
    persona_id       INTEGER,
    photo_url        TEXT,
    full_payload     TEXT,
    auth_user        INTEGER DEFAULT 1,
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL
)
"""

_DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_users_phone     ON users_sbot(phone)",
    "CREATE INDEX IF NOT EXISTS idx_users_created   ON users_sbot(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_users_person_id ON users_sbot(person_id)",
]


def ensure_table() -> None:
    try:
        from app.db import get_conn
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(_DDL)
            for idx in _DDL_INDEXES:
                cur.execute(idx)
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("ensure_table failed: %s", exc)


def upsert(
    person_id: int | None,
    name: str,
    first_name: str = "",
    phone: str = "",
    session_id: str = "",
    financial_status: str = "",
    persona_id: int | None = None,
    photo_url: str | None = None,
    full_payload: dict | None = None,
    auth_user: bool = True,
) -> str:
    """
    Insert or update a user by person_id.
    On conflict, updates all mutable fields — empty strings don't overwrite existing values.
    Returns the row id.
    """
    now = _now()
    row_id = uuid.uuid4().hex[:12]
    payload_json = json.dumps(full_payload) if full_payload else None
    try:
        from app.db import get_dict_conn, get_conn
        if person_id is not None:
            # Check for existing row
            conn_chk = get_dict_conn()
            try:
                cur = conn_chk.cursor()
                cur.execute("SELECT id FROM users_sbot WHERE person_id = %s", (person_id,))
                existing = cur.fetchone()
            finally:
                conn_chk.close()

            if existing:
                row_id = existing["id"]
                conn = get_conn()
                try:
                    cur = conn.cursor()
                    cur.execute(
                        """
                        UPDATE users_sbot
                           SET name        = %s,
                               first_name  = COALESCE(NULLIF(%s, ''), first_name),
                               phone       = COALESCE(NULLIF(%s, ''), phone),
                               session_id  = COALESCE(NULLIF(%s, ''), session_id),
                               financial_status = COALESCE(NULLIF(%s, ''), financial_status),
                               persona_id  = COALESCE(%s, persona_id),
                               photo_url   = COALESCE(%s, photo_url),
                               full_payload = COALESCE(%s, full_payload),
                               auth_user   = %s,
                               updated_at  = %s
                         WHERE person_id = %s
                        """,
                        (
                            name, first_name, phone, session_id, financial_status,
                            persona_id, photo_url, payload_json, int(auth_user), now, person_id,
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
                return row_id

        # Insert new row
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO users_sbot
                    (id, person_id, name, first_name, phone, session_id,
                     financial_status, persona_id, photo_url, full_payload, auth_user, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    row_id, person_id, name, first_name, phone, session_id,
                    financial_status, persona_id, photo_url, payload_json, int(auth_user), now, now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        logger.info("user_log.upsert | person_id=%s name=%s session=%s", person_id, name, session_id)
    except Exception as exc:
        logger.warning("upsert failed: %s", exc)
    return row_id


def query(
    limit: int = 500,
    since_iso: str | None = None,
    until_iso: str | None = None,
) -> list[dict]:
    try:
        from app.db import get_dict_conn
        conn = get_dict_conn()
        try:
            cur = conn.cursor()
            q = "SELECT * FROM users_sbot WHERE 1=1"
            params: list = []
            if since_iso:
                q += " AND created_at >= %s"
                params.append(since_iso)
            if until_iso:
                q += " AND created_at <= %s"
                params.append(until_iso)
            q += " ORDER BY created_at DESC LIMIT %s"
            params.append(limit)
            cur.execute(q, params)
            rows = cur.fetchall()
        finally:
            conn.close()
        result = []
        for row in rows:
            r = dict(row)
            if r.get("full_payload"):
                try:
                    r["full_payload"] = json.loads(r["full_payload"])
                except Exception:
                    # Linhas antigas guardam full_payload como texto puro — manter o valor cru é intencional.
                    pass
            r["auth_user"] = bool(r.get("auth_user", 1))
            result.append(r)
        return result
    except Exception as exc:
        logger.warning("query failed: %s", exc)
        return []
