"""Tests for core.cadence_engine — cadence scheduling + pause logic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from config.cadence import CADENCE_SCHEDULE
from core import cadence_engine

FORTALEZA = ZoneInfo("America/Fortaleza")


# --- compute_touches_for_day ----------------------------------------------

def test_day1_uses_offsets_from_entered_at():
    entered = datetime(2026, 6, 6, 10, 30, tzinfo=FORTALEZA)
    touches = cadence_engine.compute_touches_for_day(1, entered, CADENCE_SCHEDULE)
    assert touches == [
        entered,
        entered + timedelta(minutes=60),
    ]


def test_per_day_touch_counts_are_2_2_2_3_4():
    entered = datetime(2026, 6, 6, 10, 30, tzinfo=FORTALEZA)
    counts = [
        len(cadence_engine.compute_touches_for_day(d, entered, CADENCE_SCHEDULE))
        for d in range(1, 6)
    ]
    assert counts == [2, 2, 2, 3, 4]


def test_days_2_to_5_use_fixed_wall_clock_times_on_local_date():
    entered = datetime(2026, 6, 6, 10, 30, tzinfo=FORTALEZA)

    # Day 2 -> next calendar day (2026-06-07) at 09:00 and 15:00 local.
    day2 = cadence_engine.compute_touches_for_day(2, entered, CADENCE_SCHEDULE)
    assert day2 == [
        datetime(2026, 6, 7, 9, 0, tzinfo=FORTALEZA),
        datetime(2026, 6, 7, 15, 0, tzinfo=FORTALEZA),
    ]

    # Day 5 -> 2026-06-10 at the four configured times.
    day5 = cadence_engine.compute_touches_for_day(5, entered, CADENCE_SCHEDULE)
    assert day5 == [
        datetime(2026, 6, 10, 9, 0, tzinfo=FORTALEZA),
        datetime(2026, 6, 10, 13, 0, tzinfo=FORTALEZA),
        datetime(2026, 6, 10, 16, 0, tzinfo=FORTALEZA),
        datetime(2026, 6, 10, 19, 0, tzinfo=FORTALEZA),
    ]


def test_unknown_day_returns_empty():
    entered = datetime(2026, 6, 6, 10, 30, tzinfo=FORTALEZA)
    assert cadence_engine.compute_touches_for_day(9, entered, CADENCE_SCHEDULE) == []


# --- should_unpause --------------------------------------------------------

def test_unpause_true_when_silence_exceeds_window():
    silence_since = datetime(2026, 6, 6, 10, 0, tzinfo=FORTALEZA)
    now = silence_since + timedelta(minutes=120)
    assert cadence_engine.should_unpause(None, silence_since, now, 120) is True


def test_unpause_false_when_within_window():
    silence_since = datetime(2026, 6, 6, 10, 0, tzinfo=FORTALEZA)
    now = silence_since + timedelta(minutes=119)
    assert cadence_engine.should_unpause(None, silence_since, now, 120) is False


def test_unpause_false_when_no_silence_marker():
    now = datetime(2026, 6, 6, 12, 0, tzinfo=FORTALEZA)
    assert cadence_engine.should_unpause(None, None, now, 120) is False


# --- enqueue_followups -----------------------------------------------------

PREFIX = "test_cadeng_1_"


def _content_provider(day: int, idx: int) -> dict:
    return {"type": "text", "text": f"d{day}t{idx}", "media_url": None}


async def _fetch_queue(db_conn, prefix):
    cur = await db_conn.execute(
        f'SELECT phone, scheduled_at, content, cadence_day, status '
        f'FROM "{prefix}followup_queue" ORDER BY scheduled_at'
    )
    rows = await cur.fetchall()
    return rows


async def test_enqueue_inserts_pending_rows(schema_factory, db_conn):
    await schema_factory(PREFIX)
    entered = datetime(2026, 6, 6, 10, 30, tzinfo=FORTALEZA)
    touches = cadence_engine.compute_touches_for_day(1, entered, CADENCE_SCHEDULE)

    n = await cadence_engine.enqueue_followups(
        db_conn, PREFIX, "5511999", 1, touches, _content_provider
    )
    assert n == 2

    rows = await _fetch_queue(db_conn, PREFIX)
    assert len(rows) == 2
    phones = {r[0] for r in rows}
    assert phones == {"5511999"}
    # scheduled_at matches the computed touches (compare as aware datetimes).
    assert [r[1] for r in rows] == touches
    # content + cadence_day + status correct.
    assert rows[0][2] == {"type": "text", "text": "d1t0", "media_url": None}
    assert rows[1][2] == {"type": "text", "text": "d1t1", "media_url": None}
    assert all(r[3] == 1 for r in rows)
    assert all(r[4] == "pending" for r in rows)


async def test_enqueue_is_idempotent(schema_factory, db_conn):
    prefix = "test_cadeng_2_"
    await schema_factory(prefix)
    entered = datetime(2026, 6, 6, 10, 30, tzinfo=FORTALEZA)
    touches = cadence_engine.compute_touches_for_day(1, entered, CADENCE_SCHEDULE)

    first = await cadence_engine.enqueue_followups(
        db_conn, prefix, "5511888", 1, touches, _content_provider
    )
    second = await cadence_engine.enqueue_followups(
        db_conn, prefix, "5511888", 1, touches, _content_provider
    )
    assert first == 2
    assert second == 0

    rows = await _fetch_queue(db_conn, prefix)
    assert len(rows) == 2  # no duplicates


# --- due_followups ---------------------------------------------------------

async def test_due_followups_returns_pending_due_oldest_first(schema_factory, db_conn):
    prefix = "test_cadeng_3_"
    await schema_factory(prefix)
    base = datetime(2026, 6, 6, 9, 0, tzinfo=FORTALEZA)

    # Three touches: two in the past (due), one in the future (not due).
    past_early = base
    past_late = base + timedelta(minutes=30)
    future = base + timedelta(minutes=120)

    await cadence_engine.enqueue_followups(
        db_conn, prefix, "5511777", 2,
        [past_late, past_early, future], _content_provider,
    )

    now = base + timedelta(minutes=60)
    due = await cadence_engine.due_followups(db_conn, prefix, now)

    # Only the two past touches; oldest first.
    assert len(due) == 2
    assert due[0]["scheduled_at"] == past_early
    assert due[1]["scheduled_at"] == past_late
    assert due[0]["phone"] == "5511777"
    assert due[0]["cadence_day"] == 2
    assert due[0]["status"] == "pending"
    assert "id" in due[0]
    assert isinstance(due[0]["content"], dict)


async def test_due_followups_excludes_non_pending(schema_factory, db_conn):
    prefix = "test_cadeng_4_"
    await schema_factory(prefix)
    base = datetime(2026, 6, 6, 9, 0, tzinfo=FORTALEZA)

    await cadence_engine.enqueue_followups(
        db_conn, prefix, "5511666", 1, [base], _content_provider
    )
    # Mark it sent.
    await db_conn.execute(
        f'UPDATE "{prefix}followup_queue" SET status = %s WHERE phone = %s',
        ("sent", "5511666"),
    )

    now = base + timedelta(minutes=60)
    due = await cadence_engine.due_followups(db_conn, prefix, now)
    assert due == []
