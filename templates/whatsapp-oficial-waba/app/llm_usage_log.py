"""Persistent log of LLM usage metrics per agent run."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.db import get_conn, get_dict_conn

logger = logging.getLogger("italo.llm_usage")


def ensure_table() -> None:
    """Create llm_usage_log table if it does not exist."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS llm_usage_log (
                id                  SERIAL PRIMARY KEY,
                ts                  TEXT    NOT NULL,
                session_id          TEXT    NOT NULL,
                model               TEXT    NOT NULL,
                input_tokens        INTEGER DEFAULT 0,
                output_tokens       INTEGER DEFAULT 0,
                cache_read_tokens   INTEGER DEFAULT 0,
                cache_write_tokens  INTEGER DEFAULT 0,
                total_tokens        INTEGER DEFAULT 0,
                cost_usd            DOUBLE PRECISION,
                latency_ms          INTEGER
            )
        """)
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    """No-op kept only so old callers that still pass no args don't break."""
    ensure_table()


def save(
    session_id: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    total_tokens: int = 0,
    cost_usd: float | None = None,
    latency_ms: int | None = None,
) -> None:
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO llm_usage_log
                    (ts, session_id, model, input_tokens, output_tokens,
                     cache_read_tokens, cache_write_tokens, total_tokens, cost_usd, latency_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    session_id,
                    model,
                    input_tokens,
                    output_tokens,
                    cache_read_tokens,
                    cache_write_tokens,
                    total_tokens,
                    cost_usd,
                    latency_ms,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.error("llm_usage_log.save: %s", exc)


def get_all(
    session_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    try:
        conn = get_dict_conn()
        try:
            cur = conn.cursor()
            if session_id:
                cur.execute(
                    "SELECT * FROM llm_usage_log WHERE session_id=%s ORDER BY id DESC LIMIT %s",
                    (session_id, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM llm_usage_log ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as exc:
        logger.error("llm_usage_log.get_all: %s", exc)
        return []


def query(
    session_id: str | None = None,
    from_iso: str | None = None,
    to_iso: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    try:
        conn = get_dict_conn()
        try:
            cur = conn.cursor()
            clauses, params = [], []
            if session_id:
                clauses.append("session_id = %s")
                params.append(session_id)
            if from_iso:
                clauses.append("ts >= %s")
                params.append(from_iso)
            if to_iso:
                clauses.append("ts <= %s")
                params.append(to_iso)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(limit)
            cur.execute(
                f"SELECT * FROM llm_usage_log {where} ORDER BY id DESC LIMIT %s",
                params,
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.error("llm_usage_log.query: %s", exc)
            return []
        finally:
            conn.close()
    except Exception as exc:
        logger.error("llm_usage_log.query: %s", exc)
        return []


def totals(since_iso: str | None = None) -> dict[str, Any]:
    """Return aggregate totals (tokens, cost) optionally filtered by date."""
    try:
        conn = get_dict_conn()
        try:
            cur = conn.cursor()
            if since_iso:
                cur.execute(
                    """SELECT
                        COUNT(*)            AS runs,
                        SUM(input_tokens)   AS input_tokens,
                        SUM(output_tokens)  AS output_tokens,
                        SUM(cache_read_tokens) AS cache_read_tokens,
                        SUM(total_tokens)   AS total_tokens,
                        SUM(cost_usd)       AS cost_usd,
                        AVG(latency_ms)     AS avg_latency_ms
                    FROM llm_usage_log WHERE ts >= %s""",
                    (since_iso,),
                )
            else:
                cur.execute(
                    """SELECT
                        COUNT(*)            AS runs,
                        SUM(input_tokens)   AS input_tokens,
                        SUM(output_tokens)  AS output_tokens,
                        SUM(cache_read_tokens) AS cache_read_tokens,
                        SUM(total_tokens)   AS total_tokens,
                        SUM(cost_usd)       AS cost_usd,
                        AVG(latency_ms)     AS avg_latency_ms
                    FROM llm_usage_log"""
                )
            row = cur.fetchone()
            return dict(row) if row else {}
        finally:
            conn.close()
    except Exception as exc:
        logger.error("llm_usage_log.totals: %s", exc)
        return {}
