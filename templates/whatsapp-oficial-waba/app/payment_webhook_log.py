from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from app.db import get_conn, get_dict_conn

logger = logging.getLogger("italo.payment_webhook")


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def ensure_table() -> None:
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS payment_webhooks (
                    id           TEXT PRIMARY KEY,
                    received_at  TEXT NOT NULL,
                    phone_raw    TEXT,
                    phone_norm   TEXT,
                    payload      TEXT,
                    status       TEXT,
                    error        TEXT,
                    wa_response  TEXT
                )
            """)
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("ensure_table failed: %s", exc)


def save(
    phone_raw: str,
    phone_norm: str,
    payload: dict,
    status: str,           # "sent" | "error" | "auth_failed" | "invalid_phone"
    error: str | None = None,
    wa_response: dict | None = None,
) -> str:
    row_id = uuid.uuid4().hex[:12]
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO payment_webhooks
                    (id, received_at, phone_raw, phone_norm, payload, status, error, wa_response)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                row_id,
                _now(),
                phone_raw,
                phone_norm,
                json.dumps(payload, ensure_ascii=False),
                status,
                error,
                json.dumps(wa_response, ensure_ascii=False) if wa_response else None,
            ))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("save failed: %s", exc)
    return row_id


def get_recent(limit: int = 5) -> list[dict]:
    """Retorna os webhooks mais recentes em ordem decrescente."""
    try:
        conn = get_dict_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM payment_webhooks ORDER BY received_at DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        result = []
        for row in rows:
            r = dict(row)
            for field in ("payload", "wa_response"):
                if r.get(field):
                    try:
                        r[field] = json.loads(r[field])
                    except Exception:
                        # Linhas antigas guardam o campo como texto puro — manter o valor cru é intencional.
                        pass
            result.append(r)
        return result
    except Exception as exc:
        logger.warning("get_recent failed: %s", exc)
        return []


def query(limit: int = 100, status: str | None = None) -> list[dict]:
    try:
        conn = get_dict_conn()
        try:
            cur = conn.cursor()
            if status:
                cur.execute(
                    "SELECT * FROM payment_webhooks WHERE status = %s ORDER BY received_at DESC LIMIT %s",
                    (status, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM payment_webhooks ORDER BY received_at DESC LIMIT %s",
                    (limit,),
                )
            rows = cur.fetchall()
        finally:
            conn.close()
        result = []
        for row in rows:
            r = dict(row)
            for field in ("payload", "wa_response"):
                if r.get(field):
                    try:
                        r[field] = json.loads(r[field])
                    except Exception:
                        # Linhas antigas guardam o campo como texto puro — manter o valor cru é intencional.
                        pass
            result.append(r)
        return result
    except Exception as exc:
        logger.warning("query failed: %s", exc)
        return []
