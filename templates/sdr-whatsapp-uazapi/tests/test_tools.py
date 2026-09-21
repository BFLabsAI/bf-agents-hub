"""Behavior tests for tools.py — the agent's @tool action surface.

Each @tool is a thin wrapper that delegates to core.* / config.* collaborators.
Collaborators (DB conn, table_prefix, crm client, model caller, handoff config)
are injected via run_context.dependencies so tests never touch the network and
can fake every sibling. We invoke each tool through its raw `.entrypoint`
(the @tool decorator wraps the callable in an agno Function).
"""
from __future__ import annotations

import logging

import pytest
from agno.run.base import RunContext

import tools


def make_rc(
    *,
    user_id: str = "5599999999",
    session_state: dict | None = None,
    dependencies: dict | None = None,
) -> RunContext:
    return RunContext(
        run_id="run-test",
        session_id=f"sdr-{user_id}",
        user_id=user_id,
        session_state=session_state if session_state is not None else {},
        dependencies=dependencies or {},
    )


# --- update_qualification_fields -------------------------------------------

async def test_update_qualification_fields_persists_and_updates_state():
    calls = {}

    async def fake_update_fields(conn, table_prefix, phone, fields):
        calls["args"] = (conn, table_prefix, phone, fields)
        return {"phone": phone, "fields": fields}

    rc = make_rc(
        user_id="5511888",
        session_state={"qualification": {"budget": "old"}},
        dependencies={
            "conn": object(),
            "table_prefix": "test_tools_qf_",
            "lead_manager": type("LM", (), {"update_fields": staticmethod(fake_update_fields)}),
        },
    )

    result = await tools.update_qualification_fields.entrypoint(
        rc, {"need": "crm", "budget": "5000"}
    )

    # delegated to lead_manager.update_fields with phone + the fields
    conn, prefix, phone, fields = calls["args"]
    assert prefix == "test_tools_qf_"
    assert phone == "5511888"
    assert fields == {"need": "crm", "budget": "5000"}
    # session_state mirrors the saved fields
    assert rc.session_state["qualification"]["need"] == "crm"
    assert rc.session_state["qualification"]["budget"] == "5000"
    # returns a confirmation string
    assert isinstance(result, str)
    assert result


# --- request_handoff -------------------------------------------------------

def _handoff_deps(*, crm_client=None, get_lead=None, build_called=None):
    async def default_get_lead(conn, table_prefix, phone):
        return {"phone": phone, "name": "Maria", "assigned_vendor": None}

    fake_lm = type(
        "LM",
        (),
        {"get_lead": staticmethod(get_lead or default_get_lead)},
    )

    def fake_build(conversation, lead, vendor, crm_error=False):
        if build_called is not None:
            build_called["crm_error"] = crm_error
            build_called["vendor"] = vendor
        return f"REPASSE name={lead['name']} vendor={vendor['name']} err={crm_error}"

    def fake_pick(distribution, vendors, lead, product=None, last_index=None):
        return vendors[0]

    fake_he = type(
        "HE",
        (),
        {
            "build_repasse_message": staticmethod(fake_build),
            "pick_vendor": staticmethod(fake_pick),
        },
    )

    return {
        "conn": object(),
        "table_prefix": "test_tools_ho_",
        "lead_manager": fake_lm,
        "handoff_engine": fake_he,
        "crm_client": crm_client,
        "handoff_config": {
            "distribution": "single",
            "vendors": [{"key": "v1", "name": "Vend 1", "phone": "5511777"}],
            "group_jid": "group@g.us",
        },
    }


async def test_request_handoff_builds_message_sets_state_and_syncs_crm():
    build_called: dict = {}

    class FakeCRM:
        def __init__(self):
            self.notes = []

        async def add_note(self, contact_id, body):
            self.notes.append((contact_id, body))
            return {"id": "note1"}

    async def get_lead(conn, table_prefix, phone):
        return {"phone": phone, "name": "Maria", "ghl_contact_id": "c123",
                "assigned_vendor": None}

    crm = FakeCRM()
    rc = make_rc(
        user_id="5511999",
        dependencies=_handoff_deps(crm_client=crm, get_lead=get_lead,
                                   build_called=build_called),
    )

    result = await tools.request_handoff.entrypoint(rc, "lead qualificado")

    # built without crm_error and picked the configured vendor
    assert build_called["crm_error"] is False
    assert build_called["vendor"]["name"] == "Vend 1"
    # CRM note was logged
    assert crm.notes and crm.notes[0][0] == "c123"
    # state flags
    assert rc.session_state["handoff_done"] is True
    assert rc.session_state["assigned_vendor"] == "v1"
    assert "REPASSE" in result


async def test_request_handoff_completes_but_flags_on_crm_error():
    build_called: dict = {}

    class FailingCRM:
        async def add_note(self, contact_id, body):
            raise RuntimeError("GHL 500")

    rc = make_rc(
        user_id="5511000",
        dependencies=_handoff_deps(crm_client=FailingCRM(),
                                   build_called=build_called),
    )

    result = await tools.request_handoff.entrypoint(rc, "pediu humano")

    # handoff still completes
    assert rc.session_state["handoff_done"] is True
    # but the CRM failure is flagged in the built message
    assert build_called["crm_error"] is True
    assert "err=True" in result


# --- classify_and_mark_lost ------------------------------------------------

async def test_classify_and_mark_lost_classifies_then_sets_status():
    calls: dict = {}

    async def fake_set_status(conn, table_prefix, phone, status, loss_reason=None):
        calls["set"] = (phone, status, loss_reason)
        return {"phone": phone, "status": status, "loss_reason": loss_reason}

    async def fake_classifier(conversation):
        calls["classified_input"] = conversation
        return "no_budget"

    rc = make_rc(
        user_id="5511222",
        dependencies={
            "conn": object(),
            "table_prefix": "test_tools_lost_",
            "lead_manager": type("LM", (), {"set_status": staticmethod(fake_set_status)}),
            "loss_classifier": fake_classifier,
        },
    )

    result = await tools.classify_and_mark_lost.entrypoint(
        rc, "lead disse que nao tem orcamento"
    )

    phone, status, reason = calls["set"]
    assert phone == "5511222"
    assert status == "lost"
    assert reason == "no_budget"
    assert rc.session_state.get("status") == "lost"
    assert "no_budget" in result


# --- check_business_hours --------------------------------------------------

from datetime import datetime
from zoneinfo import ZoneInfo

_BH = {
    "timezone": "America/Fortaleza",
    "mode": "window",
    "open": "09:00",
    "close": "18:00",
    "workdays": [1, 2, 3, 4, 5],
    "holidays": [],
}


async def test_check_business_hours_open_during_window():
    # Monday 2026-06-08 14:00 local -> open
    now = datetime(2026, 6, 8, 14, 0, tzinfo=ZoneInfo("America/Fortaleza"))
    rc = make_rc(dependencies={"business_hours": _BH, "now": now})
    result = await tools.check_business_hours.entrypoint(rc)
    assert "aberto" in result.lower()


async def test_check_business_hours_closed_reports_next_window():
    # Saturday 2026-06-06 14:00 local -> closed; next open Monday 09:00
    now = datetime(2026, 6, 6, 14, 0, tzinfo=ZoneInfo("America/Fortaleza"))
    rc = make_rc(dependencies={"business_hours": _BH, "now": now})
    result = await tools.check_business_hours.entrypoint(rc)
    assert "fechado" in result.lower()
    # next opening should be Monday the 8th at 09:00
    assert "09:00" in result
    assert "2026-06-08" in result


async def test_check_business_hours_24h_always_open():
    bh = dict(_BH, mode="24h")
    now = datetime(2026, 6, 6, 3, 0, tzinfo=ZoneInfo("America/Fortaleza"))
    rc = make_rc(dependencies={"business_hours": bh, "now": now})
    result = await tools.check_business_hours.entrypoint(rc)
    assert "aberto" in result.lower()


# --- get_product_info ------------------------------------------------------

async def test_get_product_info_reads_from_db(schema_factory, db_conn):
    prefix = "test_tools_prod_1_"
    await schema_factory(prefix)
    await db_conn.execute(
        f'INSERT INTO "{prefix}products" (slug, name, description, vendor_key, active)'
        " VALUES (%s,%s,%s,%s,%s)",
        ("plano-pro", "Plano Pro", "Plano completo com suporte.", "v1", True),
    )

    rc = make_rc(dependencies={"conn": db_conn, "table_prefix": prefix})
    result = await tools.get_product_info.entrypoint(rc, "plano-pro")

    assert "Plano Pro" in result
    assert "Plano completo com suporte." in result


async def test_get_product_info_missing_slug(schema_factory, db_conn):
    prefix = "test_tools_prod_2_"
    await schema_factory(prefix)
    rc = make_rc(dependencies={"conn": db_conn, "table_prefix": prefix})
    result = await tools.get_product_info.entrypoint(rc, "nao-existe")
    assert "nao-existe" in result
    assert "não" in result.lower() or "nao" in result.lower()


# --- log_event -------------------------------------------------------------

async def test_log_event_emits_structured_log(caplog):
    rc = make_rc(user_id="5511444")
    with caplog.at_level(logging.INFO, logger="sdr.tools"):
        result = await tools.log_event.entrypoint(
            rc, "objection_handled", {"objection": "esta caro"}
        )

    records = [r for r in caplog.records if r.name == "sdr.tools"]
    assert records, "expected a log record on sdr.tools"
    msg = records[-1].getMessage()
    assert "objection_handled" in msg
    assert "5511444" in msg
    assert "esta caro" in msg
    assert isinstance(result, str)
