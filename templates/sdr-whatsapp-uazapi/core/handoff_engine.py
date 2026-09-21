"""Handoff engine — builds repasse messages and selects vendors.

Only "repasse" mode is implemented for this build. A repasse targets BOTH the
notification group JID and the assigned vendor's direct number. calendar /
custom_api modes are stubs that raise NotImplementedError with a clear message.

Pure functions only (no I/O) — the app layer sends the messages via send_reply.
"""
from __future__ import annotations

from typing import Any

# How many of the most recent messages to include in the handoff context.
_CONTEXT_TAIL = 6


def build_repasse_message(
    conversation: list[dict],
    lead: dict,
    vendor: dict,
    crm_error: bool = False,
) -> str:
    """Build the standardized repasse handoff message.

    Summarizes the lead and weights the most recent messages of `conversation`
    (list of {"role","content"} dicts). If `crm_error` is True, prominently
    flags that CRM sync failed so the vendor knows to capture the lead manually.
    Returns the message text to send to the group + vendor.
    """
    name = (lead.get("name") or "").strip() or "(sem nome)"
    phone = (lead.get("phone") or "").strip() or "(sem telefone)"
    vendor_name = (vendor.get("name") or "").strip() or "(nao definido)"

    context = _format_context(conversation)

    lines = [
        "*Repasse de lead*",
        "",
        f"Lead: {name}",
        f"Telefone: {phone}",
        f"Vendedor responsavel: {vendor_name}",
        "",
        "Contexto da conversa (mensagens mais recentes):",
        context,
    ]
    if crm_error:
        lines += [
            "",
            "ATENCAO: falha ao registrar o lead no CRM. "
            "Cadastre este lead manualmente.",
        ]
    return "\n".join(lines)


def _format_context(conversation: list[dict]) -> str:
    """Render only the last `_CONTEXT_TAIL` messages, weighting recency."""
    if not conversation:
        return "(sem historico de conversa)"
    tail = conversation[-_CONTEXT_TAIL:]
    rendered = []
    for msg in tail:
        role = (msg.get("role") or "?").strip()
        speaker = "Lead" if role == "user" else "Agente"
        content = (msg.get("content") or "").strip()
        rendered.append(f"- {speaker}: {content}")
    return "\n".join(rendered)


def pick_vendor(
    distribution: str,
    vendors: list[dict],
    lead: dict,
    product: dict | None = None,
    last_index: int | None = None,
) -> dict:
    """Select the vendor to receive a lead.

    distribution:
      - "single"      -> always the configured single_vendor.
      - "round_robin" -> next after `last_index`.
      - "by_product"  -> vendor whose key matches `product["vendor_key"]`.
    Returns the chosen vendor dict ({"key","name","phone"}).

    A recurring lead that already has an *active* assigned_vendor is always
    routed back to that same vendor (sticky ownership); only if that vendor is
    missing/inactive do we fall back to the `distribution` rule.
    """
    if not vendors:
        raise ValueError("pick_vendor requires at least one vendor")

    # Sticky ownership: recurring lead keeps its active assigned vendor.
    assigned_key = lead.get("assigned_vendor")
    if assigned_key:
        for v in vendors:
            if v.get("key") == assigned_key and _is_active(v):
                return v

    if distribution == "single":
        return vendors[0]

    if distribution == "round_robin":
        if last_index is None:
            return vendors[0]
        return vendors[(last_index + 1) % len(vendors)]

    if distribution == "by_product":
        vendor_key = (product or {}).get("vendor_key")
        for v in vendors:
            if v.get("key") == vendor_key:
                return v
        raise ValueError(
            f"by_product: no vendor matches product vendor_key {vendor_key!r}"
        )

    raise ValueError(f"unknown distribution rule: {distribution!r}")


def _is_active(vendor: dict) -> bool:
    """A vendor is active unless explicitly marked active=False."""
    return vendor.get("active", True) is not False


def targets_for_repasse(vendor: dict, group_jid: str) -> list[str]:
    """Return the list of WhatsApp targets for a repasse.

    For repasse mode this is [group_jid, vendor_phone_jid] (deduplicated,
    empties dropped). These are the chat ids send_reply is called against.
    """
    vendor_jid = _phone_to_jid(vendor.get("phone"))
    targets: list[str] = []
    for dest in (group_jid, vendor_jid):
        dest = (dest or "").strip()
        if dest and dest not in targets:
            targets.append(dest)
    return targets


def _phone_to_jid(phone: str | None) -> str:
    """Normalize a vendor phone into a WhatsApp JID.

    Already-formatted JIDs (containing '@') pass through unchanged; bare
    numbers get the personal '@s.whatsapp.net' suffix. Empty -> ''.
    """
    phone = (phone or "").strip()
    if not phone:
        return ""
    if "@" in phone:
        return phone
    return f"{phone}@s.whatsapp.net"


def build_contextual_handoff(conversation: list[dict], reason: str) -> str:
    """Build a contextual handoff note (e.g. triggered by the identity rule).

    `reason` explains why handoff fired (e.g. 'lead asked if agent is a bot').
    Returns a short context message for the human taking over.
    """
    context = _format_context(conversation)
    return "\n".join(
        [
            "*Atendimento para um humano*",
            "",
            f"Motivo: {reason}",
            "",
            "Contexto da conversa (mensagens mais recentes):",
            context,
        ]
    )


# --- Stub modes (explicitly out of scope for this build) -------------------

def build_calendar_handoff(*args: Any, **kwargs: Any) -> str:
    """STUB: calendar booking handoff mode is not part of this build."""
    raise NotImplementedError(
        "calendar handoff mode is not supported in this build (repasse only)"
    )


def build_custom_api_handoff(*args: Any, **kwargs: Any) -> str:
    """STUB: custom_api handoff mode is not part of this build."""
    raise NotImplementedError(
        "custom_api handoff mode is not supported in this build (repasse only)"
    )
