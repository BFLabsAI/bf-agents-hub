from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from app.db import get_conn, get_dict_conn

logger = logging.getLogger("italo.payment_generated")


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def ensure_table() -> None:
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS payment_generated (
                    id                  TEXT PRIMARY KEY,
                    generated_at        TEXT NOT NULL,
                    session_id          TEXT,
                    person_id           INTEGER,
                    person_name         TEXT,
                    event_title         TEXT,
                    account_receive_ids TEXT,
                    event_titles_map    TEXT,
                    method              TEXT,
                    amount              DOUBLE PRECISION,
                    gateway_ref         TEXT,
                    status              TEXT DEFAULT 'pending',
                    confirmed_at        TEXT,
                    webhook_id          TEXT
                )
            """)
            # Migrate: add column if table already exists without it
            cur.execute(
                "ALTER TABLE payment_generated ADD COLUMN IF NOT EXISTS event_titles_map TEXT"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_pg_person ON payment_generated(person_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_pg_status ON payment_generated(status)"
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("ensure_table failed: %s", exc)


def record(
    person_id: int | None,
    person_name: str,
    event_title: str,
    account_receive_ids: list[int],
    method: str,
    amount: float,
    gateway_ref: str = "",
    session_id: str = "",
    event_titles_map: dict[str, str] | None = None,
) -> str:
    """
    Insert one row per payment generation event.
    Always inserts — never updates — so multiple PIX/boletos for the same
    accountReceiveIds are all captured, enabling retry-count analysis.

    event_titles_map: optional {str(ar_id): event_title} for per-item titles.
    """
    row_id = uuid.uuid4().hex[:12]
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO payment_generated
                    (id, generated_at, session_id, person_id, person_name, event_title,
                     account_receive_ids, event_titles_map, method, amount, gateway_ref)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                row_id,
                _now(),
                session_id,
                person_id,
                person_name,
                event_title,
                json.dumps(account_receive_ids),
                json.dumps(event_titles_map) if event_titles_map else None,
                method,
                amount,
                gateway_ref[:120] if gateway_ref else "",
            ))
            conn.commit()
        finally:
            conn.close()
        logger.info(
            "payment_generated | id=%s session=%s method=%s amount=%.2f person_id=%s ar_ids=%s",
            row_id, session_id, method, amount, person_id, account_receive_ids,
        )
    except Exception as exc:
        logger.warning("record failed: %s", exc)
    return row_id


def get_recent(limit: int = 5) -> list[dict]:
    """Retorna os registros de pagamento gerados mais recentes."""
    try:
        conn = get_dict_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM payment_generated ORDER BY generated_at DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
        result = []
        for row in rows:
            r = dict(row)
            if r.get("account_receive_ids"):
                try:
                    r["account_receive_ids"] = json.loads(r["account_receive_ids"])
                except Exception:
                    # Linhas antigas guardam o campo como texto puro — manter o valor cru é intencional.
                    pass
            if r.get("event_titles_map"):
                try:
                    r["event_titles_map"] = json.loads(r["event_titles_map"])
                except Exception:
                    # Linhas antigas guardam o campo como texto puro — manter o valor cru é intencional.
                    pass
            result.append(r)
        return result
    except Exception as exc:
        logger.warning("get_recent failed: %s", exc)
        return []


def try_link(
    webhook_id: str,
    account_receive_id: int | None,
    person_id: int | None,
    amount: float | None,
) -> int:
    """
    Mark all matching pending generated payments as confirmed.

    Primary match (exact):  account_receive_id ∈ account_receive_ids JSON array.
    Fallback match:         person_id + amount within ±R$0.01, most recent rows.

    All matching rows are updated — so if 3 PIX were generated before payment,
    all 3 become 'confirmed', preserving the retry history.

    Returns the number of rows updated (0 = no match found).
    """
    if not account_receive_id and not person_id:
        return 0

    confirmed_at = _now()
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()

            # ── Primary: exact accountReceiveId overlap via JSON contains ─────────
            if account_receive_id is not None:
                cur.execute("""
                    SELECT id FROM payment_generated
                    WHERE status = 'pending'
                      AND account_receive_ids::jsonb @> %s::jsonb
                """, (json.dumps([account_receive_id]),))
                rows = cur.fetchall()

                if rows:
                    ids = [r[0] for r in rows]
                    for row_id in ids:
                        cur.execute("""
                            UPDATE payment_generated
                               SET status = 'confirmed', confirmed_at = %s, webhook_id = %s
                             WHERE id = %s
                        """, (confirmed_at, webhook_id, row_id))
                    conn.commit()
                    logger.info(
                        "try_link | confirmed %d row(s) via ar_id=%s webhook=%s",
                        len(ids), account_receive_id, webhook_id,
                    )
                    return len(ids)

            # ── Fallback: person_id + amount ──────────────────────────────────────
            if person_id is not None and amount is not None:
                cur.execute("""
                    SELECT id FROM payment_generated
                    WHERE status = 'pending'
                      AND person_id = %s
                      AND ABS(amount - %s) < 0.01
                    ORDER BY generated_at DESC
                    LIMIT 20
                """, (person_id, amount))
                rows = cur.fetchall()

                if rows:
                    ids = [r[0] for r in rows]
                    for row_id in ids:
                        cur.execute("""
                            UPDATE payment_generated
                               SET status = 'confirmed', confirmed_at = %s, webhook_id = %s
                             WHERE id = %s
                        """, (confirmed_at, webhook_id, row_id))
                    conn.commit()
                    logger.info(
                        "try_link | confirmed %d row(s) via person_id=%s amount=%.2f webhook=%s",
                        len(ids), person_id, amount, webhook_id,
                    )
                    return len(ids)

            logger.info(
                "try_link | no match for ar_id=%s person_id=%s webhook=%s",
                account_receive_id, person_id, webhook_id,
            )
            return 0
        finally:
            conn.close()

    except Exception as exc:
        logger.warning("try_link failed: %s", exc)
        return 0


def query(
    limit: int = 200,
    status: str | None = None,
    person_id: int | None = None,
    method: str | None = None,
) -> list[dict]:
    try:
        conn = get_dict_conn()
        try:
            cur = conn.cursor()
            clauses = ["1=1"]
            params: list = []
            if status:
                clauses.append("status = %s")
                params.append(status)
            if person_id is not None:
                clauses.append("person_id = %s")
                params.append(person_id)
            if method:
                clauses.append("method = %s")
                params.append(method)
            params.append(limit)
            q = f"SELECT * FROM payment_generated WHERE {' AND '.join(clauses)} ORDER BY generated_at DESC LIMIT %s"
            cur.execute(q, params)
            rows = cur.fetchall()
        finally:
            conn.close()
        result = []
        for row in rows:
            r = dict(row)
            if r.get("account_receive_ids"):
                try:
                    r["account_receive_ids"] = json.loads(r["account_receive_ids"])
                except Exception:
                    # Linhas antigas guardam o campo como texto puro — manter o valor cru é intencional.
                    pass
            if r.get("event_titles_map"):
                try:
                    r["event_titles_map"] = json.loads(r["event_titles_map"])
                except Exception:
                    # Linhas antigas guardam o campo como texto puro — manter o valor cru é intencional.
                    pass
            result.append(r)
        return result
    except Exception as exc:
        logger.warning("query failed: %s", exc)
        return []
