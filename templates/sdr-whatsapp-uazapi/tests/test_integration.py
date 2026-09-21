"""End-to-end integration tests against the real test Postgres DB.

Covers the two background flows:
  - follow-up dispatcher: a due follow-up runs through the (mocked) agent, is
    sent via a fake send_reply, marked 'sent', and logged as a GHL note.
  - daily cadence advance: an active lead moves day 1 -> 2, day-2 follow-ups are
    enqueued, and the GHL stage is mirrored (mocked GHL).

The agent, send_reply and GHL are FAKED — no network, no live model.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from core import cadence_engine
from cron import daily_advance
from cron.followup_dispatcher import dispatch_due_followups


class FakeResult:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeAgent:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def arun(self, text, session_id=None, user_id=None, **kw):
        self.calls.append({"text": text, "session_id": session_id, "user_id": user_id})
        return FakeResult(f"follow-up gerado: {text[:20]}")


class FakeCRM:
    def __init__(self) -> None:
        self.notes: list[tuple] = []
        self.moves: list[tuple] = []

    async def add_note(self, contact_id, body):
        self.notes.append((contact_id, body))
        return {"id": "note1"}

    async def move_stage(self, opp_id, stage_id):
        self.moves.append((opp_id, stage_id))
        return {"id": opp_id, "pipelineStageId": stage_id}


async def _content(day, idx):
    return {"type": "text", "text": f"toque dia {day} #{idx}", "media_url": None}


async def test_followup_dispatcher_dispatches_due_followup(schema_factory, db_conn):
    prefix = "test_intg_fu_"
    await schema_factory(prefix)
    phone = "5588111"

    await db_conn.execute(
        f'INSERT INTO "{prefix}leads" (phone, status, cadence_day, ghl_contact_id) '
        "VALUES (%s, 'active', 1, %s)",
        (phone, "ghl_c1"),
    )
    # A follow-up due in the past.
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    await cadence_engine.enqueue_followups(
        db_conn, prefix, phone, 1, [past], _content
    )

    agent = FakeAgent()
    sent: list[dict] = []

    async def fake_send(jid, text, reply_to=""):
        sent.append({"jid": jid, "text": text})

    crm = FakeCRM()
    n = await dispatch_due_followups(db_conn, prefix, agent, fake_send, crm=crm)

    assert n == 1
    # ran through the agent (so it lands in Agno history)
    assert len(agent.calls) == 1
    assert agent.calls[0]["session_id"] == f"sdr-{phone}"
    # sent via send_reply
    assert sent and sent[0]["jid"] == f"{phone}@s.whatsapp.net"
    # logged as a GHL note
    assert crm.notes and crm.notes[0][0] == "ghl_c1"
    # row marked sent + note_logged
    cur = await db_conn.execute(
        f'SELECT status, ghl_note_logged FROM "{prefix}followup_queue" WHERE phone=%s',
        (phone,),
    )
    row = await cur.fetchone()
    assert row[0] == "sent"
    assert row[1] is True


async def test_followup_dispatcher_skips_future_followups(schema_factory, db_conn):
    prefix = "test_intg_fut_"
    await schema_factory(prefix)
    phone = "5588222"
    await db_conn.execute(
        f'INSERT INTO "{prefix}leads" (phone, status, cadence_day) VALUES (%s, \'active\', 1)',
        (phone,),
    )
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    await cadence_engine.enqueue_followups(db_conn, prefix, phone, 1, [future], _content)

    agent = FakeAgent()
    n = await dispatch_due_followups(db_conn, prefix, agent, lambda *a, **k: None)
    assert n == 0
    assert agent.calls == []


async def test_daily_advance_moves_day_1_to_2_and_enqueues(schema_factory, db_conn):
    prefix = "test_intg_adv_"
    await schema_factory(prefix)
    phone = "5588333"

    tz = ZoneInfo("America/Fortaleza")
    entered = datetime(2026, 6, 1, 10, 0, tzinfo=tz)
    await db_conn.execute(
        f'INSERT INTO "{prefix}leads" '
        "(phone, status, cadence_day, cadence_paused, created_at, ghl_opp_id) "
        "VALUES (%s, 'active', 1, FALSE, %s, %s)",
        (phone, entered, "opp1"),
    )

    schedule = {
        1: [{"offset_min": 0}],
        2: [{"time": "09:00"}, {"time": "15:00"}],
    }
    crm = FakeCRM()
    stages = {"2": "stage_day2_id"}

    async def conn_factory():
        return db_conn  # reuse the open test connection (don't close it)

    # daily_advance closes the conn via close(); guard by wrapping so the
    # shared fixture connection stays open for assertions.
    class _NoCloseConn:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):  # no-op so the fixture conn survives
            return None

    async def cf():
        return _NoCloseConn(db_conn)

    advanced = await daily_advance.run_daily_advance(
        cf, prefix, schedule, crm=crm, crm_stages=stages, content_provider=_content
    )

    assert advanced == 1
    cur = await db_conn.execute(
        f'SELECT cadence_day FROM "{prefix}leads" WHERE phone=%s', (phone,)
    )
    assert (await cur.fetchone())[0] == 2

    # Day-2 touches enqueued (2 touches).
    cur = await db_conn.execute(
        f'SELECT count(*) FROM "{prefix}followup_queue" '
        "WHERE phone=%s AND cadence_day=2 AND status='pending'",
        (phone,),
    )
    assert (await cur.fetchone())[0] == 2

    # GHL stage mirrored.
    assert crm.moves == [("opp1", "stage_day2_id")]


async def test_daily_advance_marks_lost_after_last_day(schema_factory, db_conn):
    prefix = "test_intg_lost_"
    await schema_factory(prefix)
    phone = "5588444"
    await db_conn.execute(
        f'INSERT INTO "{prefix}leads" (phone, status, cadence_day, cadence_paused) '
        "VALUES (%s, 'active', 2, FALSE)",
        (phone,),
    )
    schedule = {1: [{"offset_min": 0}], 2: [{"time": "09:00"}]}  # last day = 2

    async def classifier(p):
        return "no_response"

    async def cf():
        class _NC:
            def __getattr__(self, n):
                return getattr(db_conn, n)

            def close(self):
                return None

        return _NC()

    advanced = await daily_advance.run_daily_advance(
        cf, prefix, schedule, loss_classifier=classifier
    )
    assert advanced == 1
    cur = await db_conn.execute(
        f'SELECT status, loss_reason FROM "{prefix}leads" WHERE phone=%s', (phone,)
    )
    row = await cur.fetchone()
    assert row[0] == "lost"
    assert row[1] == "no_response"


async def test_daily_advance_skips_paused_leads(schema_factory, db_conn):
    prefix = "test_intg_paused_"
    await schema_factory(prefix)
    await db_conn.execute(
        f'INSERT INTO "{prefix}leads" (phone, status, cadence_day, cadence_paused) '
        "VALUES ('5588555', 'active', 1, TRUE)"
    )
    await db_conn.execute(
        f'INSERT INTO "{prefix}leads" (phone, status, cadence_day, cadence_paused) '
        "VALUES ('5588556', 'lost', 1, FALSE)"
    )
    schedule = {1: [{"offset_min": 0}], 2: [{"time": "09:00"}]}

    async def cf():
        class _NC:
            def __getattr__(self, n):
                return getattr(db_conn, n)

            def close(self):
                return None

        return _NC()

    advanced = await daily_advance.run_daily_advance(cf, prefix, schedule)
    assert advanced == 0
