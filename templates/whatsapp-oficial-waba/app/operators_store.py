from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

_CREATE_OPERATORS = """
CREATE TABLE IF NOT EXISTS operators (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    name          TEXT,
    role          TEXT NOT NULL DEFAULT 'operator',
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL,
    must_change_password BOOLEAN NOT NULL DEFAULT false
)
"""

# Migração idempotente para bancos que já existiam antes da coluna
# `must_change_password` (troca de senha obrigatória no primeiro login).
_MIGRATE_MUST_CHANGE_PASSWORD = """
ALTER TABLE operators
    ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false
"""

_CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS operator_sessions (
    token       TEXT PRIMARY KEY,
    operator_id INTEGER NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL
)
"""

_CREATE_IDX_SESSIONS = """
CREATE INDEX IF NOT EXISTS idx_op_sessions_op ON operator_sessions(operator_id)
"""


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
    return salt.hex() + ":" + key.hex()


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, key_hex = stored.split(":", 1)
        salt = bytes.fromhex(salt_hex)
        key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100_000)
        return hmac.compare_digest(key.hex(), key_hex)
    except Exception:
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_tables() -> None:
    from app.db import get_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(_CREATE_OPERATORS)
        cur.execute(_MIGRATE_MUST_CHANGE_PASSWORD)
        cur.execute(_CREATE_SESSIONS)
        cur.execute(_CREATE_IDX_SESSIONS)
        conn.commit()
    finally:
        conn.close()


# ── Operators CRUD ────────────────────────────────────────────────────────────

def list_operators() -> list[dict]:
    from app.db import get_dict_conn

    conn = get_dict_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, name, role, created_at, must_change_password "
            "FROM operators ORDER BY created_at"
        )
        rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_operator(
    username: str,
    name: str,
    role: str,
    password: str,
    must_change_password: bool = False,
) -> dict:
    from app.db import get_dict_conn

    conn = get_dict_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO operators (username, name, role, password_hash, created_at, must_change_password)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, username, name, role, created_at, must_change_password
            """,
            (
                username.strip(),
                name.strip(),
                role,
                _hash_password(password),
                _now(),
                bool(must_change_password),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def update_operator(
    operator_id: int,
    *,
    name: str | None = None,
    role: str | None = None,
    password: str | None = None,
    must_change_password: bool | None = None,
) -> dict | None:
    from app.db import get_dict_conn

    conn = get_dict_conn()
    try:
        cur = conn.cursor()
        if name is not None:
            cur.execute("UPDATE operators SET name = %s WHERE id = %s", (name, operator_id))
        if role is not None:
            cur.execute("UPDATE operators SET role = %s WHERE id = %s", (role, operator_id))
        if password is not None:
            # Reset administrativo de senha força troca no próximo login.
            cur.execute(
                "UPDATE operators SET password_hash = %s, must_change_password = true WHERE id = %s",
                (_hash_password(password), operator_id),
            )
        if must_change_password is not None:
            cur.execute(
                "UPDATE operators SET must_change_password = %s WHERE id = %s",
                (bool(must_change_password), operator_id),
            )
        cur.execute(
            "SELECT id, username, name, role, created_at, must_change_password FROM operators WHERE id = %s",
            (operator_id,),
        )
        row = cur.fetchone()
        conn.commit()
        return dict(row) if row else None
    finally:
        conn.close()


def delete_operator(operator_id: int) -> bool:
    from app.db import get_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM operator_sessions WHERE operator_id = %s", (operator_id,))
        cur.execute("DELETE FROM operators WHERE id = %s", (operator_id,))
        affected = cur.rowcount
        conn.commit()
        return affected > 0
    finally:
        conn.close()


# ── Sessions ──────────────────────────────────────────────────────────────────

def login(username: str, password: str) -> dict | None:
    from app.db import get_dict_conn

    conn = get_dict_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM operators WHERE username = %s", (username.strip(),))
        row = cur.fetchone()
        if not row or not _verify_password(password, row["password_hash"]):
            return None
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
        cur.execute(
            "INSERT INTO operator_sessions (token, operator_id, expires_at) VALUES (%s, %s, %s)",
            (token, row["id"], expires_at),
        )
        conn.commit()
        return {
            "token": token,
            "id": row["id"],
            "username": row["username"],
            "name": row["name"],
            "role": row["role"],
            "must_change_password": bool(row["must_change_password"]),
        }
    finally:
        conn.close()


def verify_token(token: str) -> dict | None:
    """Return operator dict if token is valid and not expired, else None."""
    from app.db import get_dict_conn

    conn = get_dict_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT s.token, s.expires_at, o.id, o.username, o.name, o.role,
                   o.must_change_password
            FROM operator_sessions s
            JOIN operators o ON o.id = s.operator_id
            WHERE s.token = %s
            """,
            (token,),
        )
        row = cur.fetchone()
        if not row:
            return None
        expires_at = row["expires_at"]
        # Handle both timezone-aware and naive datetimes
        if hasattr(expires_at, "tzinfo") and expires_at.tzinfo is None:
            from datetime import timezone as tz

            expires_at = expires_at.replace(tzinfo=tz.utc)
        elif isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        if expires_at < datetime.now(timezone.utc):
            cur.execute("DELETE FROM operator_sessions WHERE token = %s", (token,))
            conn.commit()
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "name": row["name"],
            "role": row["role"],
            "must_change_password": bool(row["must_change_password"]),
        }
    finally:
        conn.close()


def change_password(
    operator_id: int,
    current_password: str,
    new_password: str,
    keep_token: str | None = None,
) -> bool:
    """Troca a senha do operador, valida a atual e limpa a flag de troca
    obrigatória. Invalida todas as outras sessões do operador, preservando
    `keep_token` (a sessão que está fazendo a troca)."""
    from app.db import get_dict_conn

    conn = get_dict_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT password_hash FROM operators WHERE id = %s", (operator_id,))
        row = cur.fetchone()
        if not row or not _verify_password(current_password, row["password_hash"]):
            return False
        cur.execute(
            "UPDATE operators SET password_hash = %s, must_change_password = false WHERE id = %s",
            (_hash_password(new_password), operator_id),
        )
        cur.execute(
            "DELETE FROM operator_sessions WHERE operator_id = %s AND token <> %s",
            (operator_id, keep_token or ""),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def logout(token: str) -> None:
    from app.db import get_conn

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM operator_sessions WHERE token = %s", (token,))
        conn.commit()
    finally:
        conn.close()


# Backward-compat shim — old code called init(db_path)
def init() -> None:
    """No-op kept only so old callers that still pass no args don't break."""
    ensure_tables()
