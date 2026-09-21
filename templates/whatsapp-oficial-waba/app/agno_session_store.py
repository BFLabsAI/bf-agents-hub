"""
Acesso à tabela Postgres `agno_sessions` (escrita pelo Agno via PostgresDb,
db_schema="public").

Schema nativo do Agno (rich): o `session_state` atual vive na coluna JSONB
`session_data` (`session_data->'session_state'`). O histórico fica em `runs`
(JSONB, lista de runs — cada run também carrega um snapshot de session_state).

Este módulo centraliza a leitura/escrita do estado de sessão, substituindo as
consultas SQLite a `session_data` que existiam em whatsapp_api.py e token_refresh.py.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.db import get_conn, get_dict_conn

logger = logging.getLogger("italo.agno_session_store")


def _as_dict(val: Any) -> dict:
    """jsonb vem do psycopg2 já como dict; tolera str (single/double-encoded)."""
    if val is None:
        return {}
    v = val
    if isinstance(v, (str, bytes)):
        try:
            v = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return {}
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return {}
    return v if isinstance(v, dict) else {}


def _state_of(session_data: Any) -> dict:
    st = _as_dict(session_data).get("session_state")
    return st if isinstance(st, dict) else {}


# ── leitura ───────────────────────────────────────────────────────────────────

def get_session_state(session_id: str) -> dict:
    """session_state atual da sessão (de session_data); {} se ausente."""
    try:
        conn = get_dict_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT session_data FROM agno_sessions WHERE session_id = %s", (session_id,))
            row = cur.fetchone()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("get_session_state(%s): %s", session_id, exc)
        return {}
    if not row:
        return {}
    return _state_of(row["session_data"])


def find_session_state_by_person_id(person_id: int) -> dict | None:
    """Retorna {session_id, **session_state} cuja session_state.person_id bate."""
    try:
        conn = get_dict_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT session_id, session_data FROM agno_sessions "
                "WHERE session_data->'session_state'->>'person_id' = %s",
                (str(person_id),),
            )
            rows = cur.fetchall()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("find_session_state_by_person_id(%s): %s", person_id, exc)
        return None
    for row in rows:
        st = _state_of(row["session_data"])
        if st.get("person_id") == person_id:
            return {"session_id": row["session_id"], **st}
    return None


# ── escrita ───────────────────────────────────────────────────────────────────

def delete_session(session_id: str) -> int:
    """Apaga uma sessão (usado pelo /flush). Retorna nº de linhas removidas."""
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM agno_sessions WHERE session_id = %s", (session_id,))
            n = cur.rowcount
            conn.commit()
        finally:
            conn.close()
        return n
    except Exception as exc:
        logger.warning("delete_session(%s): %s", session_id, exc)
        return 0


def clear_tokens(prefix: str) -> int:
    """Zera session_data.session_state.access_token das sessões `prefix%`.
    Retorna quantas sessões foram alteradas."""
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE agno_sessions
                   SET session_data = jsonb_set(session_data, '{session_state,access_token}', '""'::jsonb)
                 WHERE session_id LIKE %s
                   AND coalesce(session_data->'session_state'->>'access_token', '') <> ''
                """,
                (f"{prefix}%",),
            )
            n = cur.rowcount
            conn.commit()
        finally:
            conn.close()
        return n
    except Exception as exc:
        logger.warning("clear_tokens(%s): %s", prefix, exc)
        return 0


# ── schema (Agno cria normalmente; recriamos aqui só para testes/segurança) ───

_DDL = """
CREATE TABLE IF NOT EXISTS agno_sessions (
    session_id    VARCHAR NOT NULL PRIMARY KEY,
    session_type  VARCHAR NOT NULL DEFAULT 'agent',
    agent_id      VARCHAR,
    team_id       VARCHAR,
    workflow_id   VARCHAR,
    user_id       VARCHAR,
    session_data  JSONB,
    agent_data    JSONB,
    team_data     JSONB,
    workflow_data JSONB,
    metadata      JSONB,
    runs          JSONB,
    summary       JSONB,
    created_at    BIGINT NOT NULL DEFAULT 0,
    updated_at    BIGINT
);
"""


def ensure_table() -> None:
    try:
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute(_DDL)
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("ensure_table: %s", exc)
