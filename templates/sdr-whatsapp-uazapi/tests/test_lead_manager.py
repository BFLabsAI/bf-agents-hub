"""Tests for core.lead_manager — lead persistence over {PREFIX}leads."""
from __future__ import annotations

import pytest

from core import lead_manager


async def test_upsert_creates_new_lead(schema_factory, db_conn):
    prefix = "test_leadmgr_create_"
    await schema_factory(prefix)

    row = await lead_manager.upsert_lead(
        db_conn, prefix, phone="5585999990001", name="Alice"
    )

    assert row["phone"] == "5585999990001"
    assert row["name"] == "Alice"
    assert row["status"] == "active"
    assert row["cadence_day"] == 1


async def test_upsert_is_idempotent_on_phone(schema_factory, db_conn):
    prefix = "test_leadmgr_idem_"
    await schema_factory(prefix)

    await lead_manager.upsert_lead(db_conn, prefix, phone="5585999990002", name="Bob")
    updated = await lead_manager.upsert_lead(
        db_conn, prefix, phone="5585999990002", name="Bobby"
    )

    assert updated["name"] == "Bobby"

    async with db_conn.cursor() as cur:
        await cur.execute(f'SELECT count(*) FROM "{prefix}leads" WHERE phone = %s', ("5585999990002",))
        (count,) = await cur.fetchone()
    assert count == 1


async def test_get_lead_returns_row_or_none(schema_factory, db_conn):
    prefix = "test_leadmgr_get_"
    await schema_factory(prefix)

    assert await lead_manager.get_lead(db_conn, prefix, "5585999990003") is None

    await lead_manager.upsert_lead(db_conn, prefix, phone="5585999990003", name="Cara")
    row = await lead_manager.get_lead(db_conn, prefix, "5585999990003")
    assert row is not None
    assert row["name"] == "Cara"


async def test_update_fields_merges_without_clobbering(schema_factory, db_conn):
    prefix = "test_leadmgr_fields_"
    await schema_factory(prefix)

    await lead_manager.upsert_lead(
        db_conn, prefix, phone="5585999990004", fields={"budget": "10k"}
    )
    await lead_manager.update_fields(
        db_conn, prefix, "5585999990004", {"city": "Fortaleza"}
    )
    row = await lead_manager.update_fields(
        db_conn, prefix, "5585999990004", {"budget": "20k"}
    )

    # prior key preserved, new key added, existing key overwritten
    assert row["fields"] == {"budget": "20k", "city": "Fortaleza"}


async def test_advance_cadence_day_increments_never_resets(schema_factory, db_conn):
    prefix = "test_leadmgr_cadence_"
    await schema_factory(prefix)

    await lead_manager.upsert_lead(db_conn, prefix, phone="5585999990005")
    # day 1 -> 2 -> 3 -> 4
    assert await lead_manager.advance_cadence_day(db_conn, prefix, "5585999990005") == 2
    assert await lead_manager.advance_cadence_day(db_conn, prefix, "5585999990005") == 3
    new_day = await lead_manager.advance_cadence_day(db_conn, prefix, "5585999990005")
    assert new_day == 4

    row = await lead_manager.get_lead(db_conn, prefix, "5585999990005")
    assert row["cadence_day"] == 4


async def test_set_status_sets_status_and_loss_reason(schema_factory, db_conn):
    prefix = "test_leadmgr_status_"
    await schema_factory(prefix)

    created = await lead_manager.upsert_lead(db_conn, prefix, phone="5585999990006")

    row = await lead_manager.set_status(
        db_conn, prefix, "5585999990006", "lost", loss_reason="no budget"
    )
    assert row["status"] == "lost"
    assert row["loss_reason"] == "no budget"
    assert row["updated_at"] >= created["updated_at"]

    # status without loss_reason
    row2 = await lead_manager.set_status(db_conn, prefix, "5585999990006", "qualified")
    assert row2["status"] == "qualified"


async def test_mark_optout_sets_status_optout(schema_factory, db_conn):
    prefix = "test_leadmgr_optout_"
    await schema_factory(prefix)

    await lead_manager.upsert_lead(db_conn, prefix, phone="5585999990007")
    row = await lead_manager.mark_optout(db_conn, prefix, "5585999990007")
    assert row["status"] == "optout"


async def test_find_recurring_detects_returning_lead(schema_factory, db_conn):
    prefix = "test_leadmgr_recurring_"
    await schema_factory(prefix)

    # unknown phone -> None
    assert await lead_manager.find_recurring(db_conn, prefix, "5585999990008") is None

    await lead_manager.upsert_lead(db_conn, prefix, phone="5585999990008", name="Dora")
    found = await lead_manager.find_recurring(db_conn, prefix, "5585999990008")
    assert found is not None
    assert found["name"] == "Dora"
