import json
import logging
import traceback
from datetime import datetime, timezone

import httpx

from app.db import get_conn, get_dict_conn

_logger = logging.getLogger("italo.error_log")


def ensure_table() -> None:
    """Create tool_errors table if it does not exist."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tool_errors (
                id              SERIAL PRIMARY KEY,
                created_at      TEXT    NOT NULL,
                session_id      TEXT    NOT NULL,
                from_number     TEXT    NOT NULL,
                tool_name       TEXT    NOT NULL,
                error_type      TEXT    NOT NULL,
                http_status     INTEGER,
                error_message   TEXT    NOT NULL,
                request_context TEXT,
                raw_traceback   TEXT,
                eval_requested  INTEGER NOT NULL DEFAULT 0,
                eval_notes      TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    """No-op kept only so old callers that still pass no args don't break."""
    ensure_table()


def _classify(exc: Exception, http_status: int | None) -> str:
    if http_status == 403:
        msg = str(exc).lower()
        return "auth_token_expired" if "expirado" in msg else "auth_token_invalid"
    if http_status == 404:
        return "auth_not_found"
    if http_status == 422:
        return "api_domain_error"
    if http_status and http_status >= 500:
        return "api_server_error"
    exc_name = type(exc).__name__.lower()
    if "timeout" in exc_name or "connect" in exc_name or "network" in exc_name:
        return "api_network_error"
    exc_str = str(exc).lower()
    if "timeout" in exc_str or "connection" in exc_str:
        return "api_network_error"
    return "unknown"


def log_error(
    session_id: str,
    tool_name: str,
    error_message: str,
    from_number: str = "",
    error_type: str = "unknown",
    http_status: int | None = None,
    request_context: dict | None = None,
    raw_traceback: str = "",
) -> None:
    """Simple interface: persist an error row with minimal required fields."""
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO tool_errors
                   (created_at, session_id, from_number, tool_name, error_type,
                    http_status, error_message, request_context, raw_traceback)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    session_id or "",
                    from_number or "",
                    tool_name,
                    error_type,
                    http_status,
                    error_message[:2000],
                    json.dumps(request_context or {}, default=str)[:4000],
                    raw_traceback[:4000],
                ),
            )
            conn.commit()
        finally:
            conn.close()
        _logger.info("error_log | recorded tool=%s type=%s session=%s", tool_name, error_type, session_id)
    except Exception as e:
        _logger.warning("error_log | failed to record: %s", e)


def record(
    session_id: str,
    from_number: str,
    tool_name: str,
    exc: Exception,
    context: dict | None = None,
    error_type: str | None = None,
) -> None:
    """Full interface for recording exceptions with auto-classification."""
    try:
        http_status: int | None = None
        if isinstance(exc, httpx.HTTPStatusError):
            http_status = exc.response.status_code

        etype = error_type or _classify(exc, http_status)
        tb = traceback.format_exc()

        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO tool_errors
                   (created_at, session_id, from_number, tool_name, error_type,
                    http_status, error_message, request_context, raw_traceback)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    datetime.now(timezone.utc).isoformat(),
                    session_id or "",
                    from_number or "",
                    tool_name,
                    etype,
                    http_status,
                    str(exc)[:2000],
                    json.dumps(context or {}, default=str)[:4000],
                    tb[:4000],
                ),
            )
            conn.commit()
        finally:
            conn.close()
        _logger.info("error_log | recorded tool=%s type=%s session=%s", tool_name, etype, session_id)

        # Broadcast to SSE stream so dashboard updates in real time
        try:
            from app.log_store import log_store
            log_store._broadcast({
                "type": "tool_error",
                "error": {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "session_id": session_id or "",
                    "from_number": from_number or "",
                    "tool_name": tool_name,
                    "error_type": etype,
                    "http_status": http_status,
                    "error_message": str(exc)[:300],
                },
            })
        except Exception:
            # Broadcast SSE é best-effort: falhar aqui não pode impedir o registro do erro em si.
            pass
    except Exception as e:
        _logger.warning("error_log | failed to record: %s", e)


def get_recent(limit: int = 100) -> list[dict]:
    """Return the most recent errors, newest first."""
    try:
        conn = get_dict_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM tool_errors ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as e:
        _logger.warning("error_log | get_recent failed: %s", e)
        return []


def query(
    tool_name: str | None = None,
    error_type: str | None = None,
    from_number: str | None = None,
    eval_requested: int | None = None,
    limit: int = 100,
) -> list[dict]:
    try:
        conn = get_dict_conn()
        try:
            cur = conn.cursor()
            sql = "SELECT * FROM tool_errors WHERE 1=1"
            params: list = []
            if tool_name:
                sql += " AND tool_name=%s"; params.append(tool_name)
            if error_type:
                sql += " AND error_type=%s"; params.append(error_type)
            if from_number:
                sql += " AND from_number=%s"; params.append(from_number)
            if eval_requested is not None:
                sql += " AND eval_requested=%s"; params.append(eval_requested)
            sql += " ORDER BY id DESC LIMIT %s"; params.append(limit)
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as e:
        _logger.warning("error_log | query failed: %s", e)
        return []


def mark_eval(error_id: int, notes: str = "") -> bool:
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "UPDATE tool_errors SET eval_requested=1, eval_notes=%s WHERE id=%s",
                (notes, error_id),
            )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as e:
        _logger.warning("error_log | mark_eval failed: %s", e)
        return False
