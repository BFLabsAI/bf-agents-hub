"""Lead persistence layer (CRUD over {PREFIX}leads).

Every function takes an explicit DB handle (`conn`: a psycopg AsyncConnection
or pool) and a `table_prefix` so it is fully testable against an isolated set
of prefixed tables. No module-level connection / global state.

cadence_day is the single source of truth for cadence position; advancing it
here is what the CRM stage later mirrors.

Status values: active | qualified | assigned | scheduled | lost | optout.
"""
from __future__ import annotations

import json
from typing import Any


async def _fetch_one(conn: Any, sql: str, params: tuple) -> dict | None:
    """Run a query and return the single row as a column->value dict, or None."""
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        row = await cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


async def upsert_lead(
    conn: Any,
    table_prefix: str,
    phone: str,
    name: str | None = None,
    fields: dict | None = None,
) -> dict:
    """Insert a new lead or update an existing one (keyed by unique `phone`).

    On insert: status defaults to 'active', cadence_day to 1. On update: merges
    `name`/`fields` (fields JSONB is merged, not replaced) and bumps
    last_activity / updated_at.

    Returns the resulting lead row as a dict.
    """
    fields = fields or {}
    sql = f"""
        INSERT INTO "{table_prefix}leads" (phone, name, fields, last_activity)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (phone) DO UPDATE SET
            name = COALESCE(EXCLUDED.name, "{table_prefix}leads".name),
            fields = "{table_prefix}leads".fields || EXCLUDED.fields,
            last_activity = now(),
            updated_at = now()
        RETURNING *
    """
    return await _fetch_one(conn, sql, (phone, name, json.dumps(fields)))


async def get_lead(conn: Any, table_prefix: str, phone: str) -> dict | None:
    """Return the lead row for `phone` as a dict, or None if not found."""
    sql = f'SELECT * FROM "{table_prefix}leads" WHERE phone = %s'
    return await _fetch_one(conn, sql, (phone,))


async def update_fields(
    conn: Any, table_prefix: str, phone: str, fields: dict
) -> dict:
    """Merge `fields` into the lead's JSONB `fields` column.

    Existing keys are overwritten; unspecified keys are preserved. Returns the
    updated lead row as a dict.
    """
    sql = f"""
        UPDATE "{table_prefix}leads"
        SET fields = fields || %s::jsonb,
            updated_at = now()
        WHERE phone = %s
        RETURNING *
    """
    return await _fetch_one(conn, sql, (json.dumps(fields), phone))


async def set_status(
    conn: Any,
    table_prefix: str,
    phone: str,
    status: str,
    loss_reason: str | None = None,
) -> dict:
    """Set the lead's status (and loss_reason when status == 'lost').

    Returns the updated lead row as a dict.
    """
    sql = f"""
        UPDATE "{table_prefix}leads"
        SET status = %s,
            loss_reason = COALESCE(%s, loss_reason),
            updated_at = now()
        WHERE phone = %s
        RETURNING *
    """
    return await _fetch_one(conn, sql, (status, loss_reason, phone))


async def advance_cadence_day(conn: Any, table_prefix: str, phone: str) -> int:
    """Increment the lead's cadence_day by one and return the new value.

    cadence_day is the single source of truth; callers mirror this into the GHL
    stage separately. Should clamp / no-op past the last configured day per the
    cadence schedule (decision left to implementer).
    """
    sql = f"""
        UPDATE "{table_prefix}leads"
        SET cadence_day = cadence_day + 1,
            updated_at = now()
        WHERE phone = %s
        RETURNING cadence_day
    """
    row = await _fetch_one(conn, sql, (phone,))
    return row["cadence_day"]


async def mark_optout(conn: Any, table_prefix: str, phone: str) -> dict:
    """Mark the lead as opted out: status='optout', pause cadence.

    Returns the updated lead row as a dict.
    """
    sql = f"""
        UPDATE "{table_prefix}leads"
        SET status = 'optout',
            cadence_paused = TRUE,
            updated_at = now()
        WHERE phone = %s
        RETURNING *
    """
    return await _fetch_one(conn, sql, (phone,))


async def find_recurring(
    conn: Any, table_prefix: str, phone: str
) -> dict | None:
    """Detect a returning lead.

    Returns the existing lead dict if this phone has prior history (e.g. a lead
    that was previously lost/optout and is messaging again), else None. Used to
    re-engage / reset cadence appropriately.
    """
    return await get_lead(conn, table_prefix, phone)
