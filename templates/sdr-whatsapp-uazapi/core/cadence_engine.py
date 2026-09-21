"""Cadence engine — computes follow-up touch times and manages the queue.

Works hand-in-hand with config/cadence.py (CADENCE_SCHEDULE) and the
{PREFIX}followup_queue table. Pure scheduling helpers are time-injectable;
DB helpers take an explicit `conn` + `prefix`.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from psycopg.types.json import Json


def compute_touches_for_day(
    day: int,
    entered_at: datetime,
    schedule: dict[int, list[dict]],
) -> list[datetime]:
    """Resolve the concrete touch datetimes for cadence `day`.

    `schedule` is CADENCE_SCHEDULE. Each spec is either {"offset_min": N}
    (relative to `entered_at`) or {"time": "HH:MM"} (wall-clock on the day's
    date, in `entered_at`'s tzinfo). Returns an ordered list of timezone-aware
    datetimes. Returns [] for days not present in the schedule.
    """
    specs = schedule.get(day)
    if not specs:
        return []

    # The wall-clock date for this cadence day: day 1 lands on entered_at's
    # local date, each subsequent day advances one calendar day.
    day_date = (entered_at + timedelta(days=day - 1)).date()

    touches: list[datetime] = []
    for spec in specs:
        if "offset_min" in spec:
            touches.append(entered_at + timedelta(minutes=spec["offset_min"]))
        elif "time" in spec:
            hh, mm = (int(part) for part in spec["time"].split(":"))
            touches.append(
                datetime(
                    day_date.year,
                    day_date.month,
                    day_date.day,
                    hh,
                    mm,
                    tzinfo=entered_at.tzinfo,
                )
            )
        else:
            raise ValueError(f"Unknown touch spec: {spec!r}")
    return touches


def should_unpause(
    last_activity: datetime | None,
    silence_since: datetime | None,
    now: datetime,
    window_min: int,
) -> bool:
    """Decide whether a paused cadence should resume.

    Returns True when the lead has been silent for at least `window_min`
    minutes (now - silence_since >= window). All datetimes tz-aware.
    """
    if silence_since is None:
        return False
    return now - silence_since >= timedelta(minutes=window_min)


async def enqueue_followups(
    conn: Any,
    prefix: str,
    phone: str,
    day: int,
    touches: list[datetime],
    content_provider: Callable[[int, int], dict | Awaitable[dict]],
) -> int:
    """Insert pending follow-up rows for `phone` for the given `touches`.

    `content_provider(day, touch_index)` returns the content JSONB
    ({"type","text","media_url"}) for each touch (may be sync or async).
    Skips touches that already have a queued row (idempotent). Returns the
    number of rows enqueued.
    """
    table = f'"{prefix}followup_queue"'
    enqueued = 0
    for idx, scheduled_at in enumerate(touches):
        # Idempotency: skip if a row for this phone+scheduled_at already exists.
        cur = await conn.execute(
            f"SELECT 1 FROM {table} WHERE phone = %s AND scheduled_at = %s",
            (phone, scheduled_at),
        )
        if await cur.fetchone() is not None:
            continue

        content = content_provider(day, idx)
        if inspect.isawaitable(content):
            content = await content

        await conn.execute(
            f"INSERT INTO {table} (phone, scheduled_at, content, cadence_day, status) "
            f"VALUES (%s, %s, %s, %s, 'pending')",
            (phone, scheduled_at, Json(content), day),
        )
        enqueued += 1
    return enqueued


async def due_followups(
    conn: Any, prefix: str, now: datetime
) -> list[dict]:
    """Return pending follow-up rows whose scheduled_at <= `now`.

    Each row returned as a dict (id, phone, content, cadence_day, ...). Does
    not mutate status — the dispatcher marks rows sent/cancelled afterwards.
    """
    table = f'"{prefix}followup_queue"'
    cur = await conn.execute(
        f"SELECT id, phone, scheduled_at, content, status, cadence_day, "
        f"ghl_note_logged, created_at "
        f"FROM {table} "
        f"WHERE status = 'pending' AND scheduled_at <= %s "
        f"ORDER BY scheduled_at ASC, id ASC",
        (now,),
    )
    rows = await cur.fetchall()
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, row)) for row in rows]
