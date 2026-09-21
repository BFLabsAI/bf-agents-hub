"""Agno tools exposed to the SDR agent.

These @tool functions are the agent's action surface. Each is a THIN wrapper
that delegates to a core.* / config.* collaborator. Every external dependency
(DB connection, table prefix, CRM client, multimodal model caller, handoff
roster) is injected through ``run_context.dependencies`` so the tools never
reach for module-level globals and stay fully testable without network/DB
access in unit tests.

Expected ``run_context.dependencies`` keys (the app layer populates these when
it calls ``agent.arun(..., dependencies={...})``):

  - ``conn``          : psycopg AsyncConnection / pool (lead + product queries)
  - ``table_prefix``  : str table prefix ("" in prod, "test_..._" in tests)
  - ``lead_manager``  : module exposing update_fields/set_status (default
                         core.lead_manager)
  - ``handoff_engine``: module exposing build_repasse_message/pick_vendor
                         (default core.handoff_engine)
  - ``crm_client``    : optional GHLClient-like object (None disables CRM sync)
  - ``handoff_config``: dict like config.handoff.HANDOFF (distribution, vendors,
                         group_jid)
  - ``model_caller``  : optional async callable for loss classification
  - ``business_hours``: dict like config.business_hours.BUSINESS_HOURS

The lead phone is ``run_context.user_id`` (the app uses the phone prefix as the
Agno user_id). Tool docstrings stay crisp: the agent reads them to decide when
to call.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from agno.run.base import RunContext
from agno.tools.decorator import tool

logger = logging.getLogger("sdr.tools")


def _deps(run_context: RunContext) -> dict:
    """Return the dependency container (never None)."""
    return run_context.dependencies or {}


def _phone(run_context: RunContext) -> str:
    """The lead's phone — the app uses it as the Agno user_id."""
    return run_context.user_id or ""


def _lead_manager(deps: dict):
    lm = deps.get("lead_manager")
    if lm is not None:
        return lm
    from core import lead_manager as lm  # lazy import; real default

    return lm


def _handoff_engine(deps: dict):
    he = deps.get("handoff_engine")
    if he is not None:
        return he
    from core import handoff_engine as he  # lazy import; real default

    return he


@tool
async def update_qualification_fields(run_context: RunContext, fields: dict) -> str:
    """Save qualification answers collected from the lead.

    Use whenever the lead reveals qualifying info (need, budget, timeline, etc.).

    Args:
        fields: key->value map of qualification fields to persist.
    """
    deps = _deps(run_context)
    phone = _phone(run_context)
    lead_manager = _lead_manager(deps)

    await lead_manager.update_fields(
        deps.get("conn"), deps.get("table_prefix", ""), phone, fields
    )

    # Mirror into session_state so the agent sees what it already knows.
    state = run_context.session_state
    if state is not None:
        qual = state.setdefault("qualification", {})
        qual.update(fields)

    saved = ", ".join(str(k) for k in fields)
    return f"Qualificação atualizada ({saved})."


def _conversation(run_context: RunContext) -> list[dict]:
    """Render the live run messages into [{role, content}] for handoff context."""
    msgs = run_context.messages or []
    out: list[dict] = []
    for m in msgs:
        role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else None)
        content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else None)
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    return out


@tool
async def request_handoff(run_context: RunContext, reason: str, product: str | None = None) -> str:
    """Hand the conversation to a human vendor (repasse).

    Use when the lead is qualified/ready, asks for a human, or asks whether you
    are a bot (deflect, then hand off).

    Args:
        reason: short reason for the handoff (drives the context message).
        product: optional product slug to route the lead by product.
    """
    deps = _deps(run_context)
    phone = _phone(run_context)
    lead_manager = _lead_manager(deps)
    handoff_engine = _handoff_engine(deps)
    cfg = deps.get("handoff_config") or {}

    lead = await lead_manager.get_lead(
        deps.get("conn"), deps.get("table_prefix", ""), phone
    ) or {"phone": phone}

    vendors = cfg.get("vendors") or []
    vendor = handoff_engine.pick_vendor(
        cfg.get("distribution", "single"), vendors, lead, product=None
    )

    conversation = _conversation(run_context)

    # Attempt CRM sync (log a handoff note). On any failure, still complete the
    # handoff but flag crm_error so the vendor knows to capture the lead manually.
    crm_error = False
    crm = deps.get("crm_client")
    if crm is not None:
        contact_id = lead.get("ghl_contact_id")
        try:
            await crm.add_note(contact_id, f"Repasse: {reason}")
        except Exception:
            crm_error = True
            logger.error("request_handoff: CRM sync failed", exc_info=True)

    message = handoff_engine.build_repasse_message(
        conversation, lead, vendor, crm_error=crm_error
    )

    state = run_context.session_state
    if state is not None:
        state["handoff_done"] = True
        state["assigned_vendor"] = vendor.get("key")
        state["handoff_crm_error"] = crm_error

    logger.info("request_handoff phone=%s vendor=%s crm_error=%s reason=%s",
                phone, vendor.get("key"), crm_error, reason)
    return message


@tool
async def classify_and_mark_lost(run_context: RunContext, conversation: str) -> str:
    """Classify why a lead was lost and mark it lost.

    Use when the lead clearly disqualifies or refuses to continue. Pass the
    relevant conversation text; a classifier derives a categorized loss reason
    (e.g. 'no_budget', 'not_interested') which is persisted.

    Args:
        conversation: the conversation text / summary to classify.
    """
    deps = _deps(run_context)
    phone = _phone(run_context)
    lead_manager = _lead_manager(deps)

    classifier = deps.get("loss_classifier")
    if classifier is not None:
        loss_reason = await classifier(conversation)
    else:
        loss_reason = "unspecified"

    await lead_manager.set_status(
        deps.get("conn"), deps.get("table_prefix", ""), phone,
        "lost", loss_reason=loss_reason,
    )

    state = run_context.session_state
    if state is not None:
        state["status"] = "lost"
        state["loss_reason"] = loss_reason

    logger.info("classify_and_mark_lost phone=%s reason=%s", phone, loss_reason)
    return f"Lead marcado como perdido (motivo: {loss_reason})."


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def _next_open(now: datetime, open_t: time, workdays: list[int], holidays: set[str]) -> datetime:
    """Find the next datetime the business opens at/after `now`."""
    for offset in range(0, 14):  # search up to 2 weeks ahead
        cand = now if offset == 0 else (now + _days(offset))
        day = cand.date()
        if (day.isoweekday() in workdays) and (day.isoformat() not in holidays):
            opens_at = datetime.combine(day, open_t, tzinfo=now.tzinfo)
            if opens_at >= now:
                return opens_at
    return now  # fallback (should not happen with sane config)


def _days(n: int):
    from datetime import timedelta

    return timedelta(days=n)


@tool
async def check_business_hours(run_context: RunContext) -> str:
    """Return whether the business is currently open and, if closed, when it
    reopens. Use before promising immediate human contact."""
    deps = _deps(run_context)
    bh = deps.get("business_hours")
    if bh is None:
        from config.business_hours import BUSINESS_HOURS as bh

    tz = ZoneInfo(bh.get("timezone", "America/Fortaleza"))
    now = deps.get("now") or datetime.now(tz)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)

    if bh.get("mode") == "24h":
        return "Estamos aberto agora (atendimento 24h)."

    open_t = _parse_hhmm(bh.get("open", "09:00"))
    close_t = _parse_hhmm(bh.get("close", "18:00"))
    workdays = bh.get("workdays", [1, 2, 3, 4, 5])
    holidays = set(bh.get("holidays", []))

    today_str = now.date().isoformat()
    is_workday = (now.date().isoweekday() in workdays) and (today_str not in holidays)
    is_open = is_workday and (open_t <= now.time() < close_t)

    if is_open:
        return f"Estamos aberto agora (até {close_t.strftime('%H:%M')})."

    nxt = _next_open(now, open_t, workdays, holidays)
    return (
        f"Estamos fechado agora. Reabrimos em "
        f"{nxt.date().isoformat()} às {nxt.strftime('%H:%M')}."
    )


@tool
async def get_product_info(run_context: RunContext, slug: str) -> str:
    """Fetch details about a product to answer the lead accurately.

    Args:
        slug: the product slug to look up.
    """
    deps = _deps(run_context)
    conn = deps.get("conn")
    prefix = deps.get("table_prefix", "")

    sql = (
        f'SELECT slug, name, description, active FROM "{prefix}products" '
        "WHERE slug = %s"
    )
    async with conn.cursor() as cur:
        await cur.execute(sql, (slug,))
        row = await cur.fetchone()
        cols = [d[0] for d in cur.description] if cur.description else []

    if row is None:
        return f"Produto '{slug}' não encontrado."

    product = dict(zip(cols, row))
    name = product.get("name") or slug
    desc = product.get("description") or "(sem descrição)"
    return f"{name}: {desc}"


@tool
async def log_event(run_context: RunContext, event: str, detail: dict | None = None) -> str:
    """Log a structured business event for observability/analytics.

    Args:
        event: event name (e.g. 'qualified', 'objection_handled').
        detail: optional structured payload.
    """
    phone = _phone(run_context)
    payload = json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)
    logger.info("event=%s phone=%s session=%s detail=%s",
                event, phone, run_context.session_id, payload)
    return f"Evento '{event}' registrado."
