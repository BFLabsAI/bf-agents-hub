"""Tests for core.handoff_engine — pure repasse handoff logic.

All functions are pure (no I/O), so tests are deterministic. Sibling modules
are not imported here; nothing touches the network or DB.
"""
from __future__ import annotations

import pytest

from core import handoff_engine as he


# ---------------------------------------------------------------------------
# build_repasse_message
# ---------------------------------------------------------------------------

def test_build_repasse_message_includes_lead_name_and_phone():
    conversation = [
        {"role": "user", "content": "oi quero saber do plano"},
        {"role": "assistant", "content": "claro, posso ajudar"},
    ]
    lead = {"name": "Maria Silva", "phone": "5585999990000"}
    vendor = {"key": "v1", "name": "Joao", "phone": "5585988887777"}

    msg = he.build_repasse_message(conversation, lead, vendor)

    assert "Maria Silva" in msg
    assert "5585999990000" in msg


def test_build_repasse_message_is_standardized_template():
    """Same structural sections regardless of differing lead content."""
    convo = [{"role": "user", "content": "ola"}]
    msg_a = he.build_repasse_message(
        convo, {"name": "A", "phone": "111"}, {"key": "v", "name": "V", "phone": "9"}
    )
    msg_b = he.build_repasse_message(
        convo, {"name": "B", "phone": "222"}, {"key": "v", "name": "V", "phone": "9"}
    )
    # The fixed section headers must appear identically in both.
    for header in ("Lead:", "Telefone:", "Contexto", "Vendedor"):
        assert header in msg_a
        assert header in msg_b


def test_build_repasse_message_weights_last_messages():
    """The most recent messages must appear; very old ones are dropped."""
    conversation = [{"role": "user", "content": f"msg-antiga-{i}"} for i in range(20)]
    conversation += [
        {"role": "user", "content": "ULTIMA-PERGUNTA-IMPORTANTE"},
        {"role": "assistant", "content": "RESPOSTA-FINAL"},
    ]
    lead = {"name": "Ze", "phone": "5585111112222"}
    vendor = {"key": "v1", "name": "Ana", "phone": "5585333334444"}

    msg = he.build_repasse_message(conversation, lead, vendor)

    assert "ULTIMA-PERGUNTA-IMPORTANTE" in msg
    assert "RESPOSTA-FINAL" in msg
    assert "msg-antiga-0" not in msg


def test_build_repasse_message_includes_vendor_name():
    msg = he.build_repasse_message(
        [{"role": "user", "content": "oi"}],
        {"name": "Lead X", "phone": "123"},
        {"key": "v2", "name": "Carlos", "phone": "555"},
    )
    assert "Carlos" in msg


def test_build_repasse_message_crm_error_flag():
    lead = {"name": "Lead Y", "phone": "999"}
    vendor = {"key": "v1", "name": "Bia", "phone": "888"}
    convo = [{"role": "user", "content": "oi"}]

    ok = he.build_repasse_message(convo, lead, vendor, crm_error=False)
    err = he.build_repasse_message(convo, lead, vendor, crm_error=True)

    # No false alarm when CRM is fine.
    assert "manualmente" not in ok.lower()
    # Clear manual-capture instruction when CRM failed.
    assert "manualmente" in err.lower()
    assert "crm" in err.lower()


# ---------------------------------------------------------------------------
# pick_vendor
# ---------------------------------------------------------------------------

V1 = {"key": "v1", "name": "Ana", "phone": "111", "active": True}
V2 = {"key": "v2", "name": "Bia", "phone": "222", "active": True}
V3 = {"key": "v3", "name": "Caio", "phone": "333", "active": True}


def test_pick_vendor_single_always_returns_first():
    vendors = [V1, V2, V3]
    lead = {"phone": "5585000", "assigned_vendor": None}
    chosen = he.pick_vendor("single", vendors, lead)
    assert chosen["key"] == "v1"


def test_pick_vendor_round_robin_cycles_by_last_index():
    vendors = [V1, V2, V3]
    lead = {"phone": "5585000", "assigned_vendor": None}
    # last_index=0 -> next is index 1 (V2)
    assert he.pick_vendor("round_robin", vendors, lead, last_index=0)["key"] == "v2"
    # last_index=1 -> next is index 2 (V3)
    assert he.pick_vendor("round_robin", vendors, lead, last_index=1)["key"] == "v3"
    # last_index=2 -> wraps to index 0 (V1)
    assert he.pick_vendor("round_robin", vendors, lead, last_index=2)["key"] == "v1"


def test_pick_vendor_round_robin_no_last_index_starts_at_first():
    vendors = [V1, V2, V3]
    lead = {"phone": "5585000", "assigned_vendor": None}
    assert he.pick_vendor("round_robin", vendors, lead, last_index=None)["key"] == "v1"


def test_pick_vendor_by_product_maps_via_vendor_key():
    vendors = [V1, V2, V3]
    lead = {"phone": "5585000", "assigned_vendor": None}
    product = {"slug": "plano-pro", "vendor_key": "v2"}
    chosen = he.pick_vendor("by_product", vendors, lead, product=product)
    assert chosen["key"] == "v2"


def test_pick_vendor_recurring_lead_keeps_active_assigned_vendor():
    vendors = [V1, V2, V3]
    lead = {"phone": "5585000", "assigned_vendor": "v3"}
    # Even though distribution is "single" (would pick v1), sticky ownership wins.
    chosen = he.pick_vendor("single", vendors, lead)
    assert chosen["key"] == "v3"
    # Same for round_robin: ownership overrides the cycle.
    chosen2 = he.pick_vendor("round_robin", vendors, lead, last_index=0)
    assert chosen2["key"] == "v3"


def test_pick_vendor_inactive_assigned_vendor_falls_back_to_distribution():
    inactive_v3 = {"key": "v3", "name": "Caio", "phone": "333", "active": False}
    vendors = [V1, V2, inactive_v3]
    lead = {"phone": "5585000", "assigned_vendor": "v3"}
    # v3 is inactive -> fall back to "single" rule -> v1.
    chosen = he.pick_vendor("single", vendors, lead)
    assert chosen["key"] == "v1"


# ---------------------------------------------------------------------------
# targets_for_repasse
# ---------------------------------------------------------------------------

def test_targets_for_repasse_returns_group_and_vendor():
    vendor = {"key": "v1", "name": "Ana", "phone": "5585111112222"}
    group = "120363000000000000@g.us"
    targets = he.targets_for_repasse(vendor, group)
    assert group in targets
    assert any("5585111112222" in t for t in targets)
    assert len(targets) == 2


def test_targets_for_repasse_drops_empty_and_dedups():
    vendor = {"key": "v1", "name": "Ana", "phone": ""}
    # Empty group + empty vendor phone -> nothing to send.
    assert he.targets_for_repasse(vendor, "") == []
    # Group equal to vendor jid -> deduplicated to a single entry.
    same = "5585111@s.whatsapp.net"
    vendor2 = {"key": "v1", "name": "Ana", "phone": same}
    targets = he.targets_for_repasse(vendor2, same)
    assert targets == [same]


# ---------------------------------------------------------------------------
# build_contextual_handoff
# ---------------------------------------------------------------------------

def test_build_contextual_handoff_includes_reason_and_context():
    conversation = [
        {"role": "user", "content": "voces tem vaga pra curriculo?"},
        {"role": "assistant", "content": "vou te transferir"},
    ]
    reason = "contato nao-comercial: envio de curriculo"
    note = he.build_contextual_handoff(conversation, reason)

    assert reason in note
    # Carries the recent conversation context for the human taking over.
    assert "curriculo" in note.lower()


def test_build_contextual_handoff_handles_empty_conversation():
    note = he.build_contextual_handoff([], "pergunta financeira")
    assert "pergunta financeira" in note
    # Still produces a usable, non-empty note.
    assert note.strip()


# ---------------------------------------------------------------------------
# Out-of-scope stub modes
# ---------------------------------------------------------------------------

def test_calendar_handoff_is_not_implemented_with_clear_message():
    with pytest.raises(NotImplementedError) as exc:
        he.build_calendar_handoff()
    assert "calendar" in str(exc.value).lower()


def test_custom_api_handoff_is_not_implemented_with_clear_message():
    with pytest.raises(NotImplementedError) as exc:
        he.build_custom_api_handoff()
    assert "custom_api" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Identity rule: nothing reveals the agent is AI/bot/automation.
# ---------------------------------------------------------------------------

def test_messages_never_reveal_ai_identity():
    convo = [
        {"role": "user", "content": "quero saber mais sobre o plano"},
        {"role": "assistant", "content": "vou te conectar com a equipe"},
    ]
    lead = {"name": "Teste", "phone": "5585000"}
    vendor = {"key": "v1", "name": "Ana", "phone": "5585111"}
    forbidden = ("sou uma ia", "sou um bot", "robô", "robo", "automação",
                 "automacao", "inteligência artificial", "inteligencia artificial",
                 "chatbot", "assistente virtual")

    outputs = [
        he.build_repasse_message(convo, lead, vendor),
        he.build_repasse_message(convo, lead, vendor, crm_error=True),
        he.build_contextual_handoff(convo, "lead perguntou se e um bot"),
    ]
    for out in outputs:
        low = out.lower()
        for term in forbidden:
            assert term not in low, f"identity leak: {term!r} in {out!r}"
