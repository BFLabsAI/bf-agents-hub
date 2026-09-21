"""Tests for the +1 Passo follow-up copy + content provider.

The provider feeds cadence_engine.enqueue_followups: provider(day, idx) -> a
content dict. Every scheduled touch must yield non-empty copy, and the copy
roster must cover every touch in CADENCE_SCHEDULE (no silent gaps).
"""
from __future__ import annotations

from config.cadence import (
    CADENCE_SCHEDULE,
    FOLLOWUP_COPY,
    make_followup_content_provider,
)


def test_copy_covers_every_scheduled_touch():
    for day, touches in CADENCE_SCHEDULE.items():
        assert day in FOLLOWUP_COPY, f"no copy for day {day}"
        assert len(FOLLOWUP_COPY[day]) >= len(touches), (
            f"day {day}: {len(FOLLOWUP_COPY[day])} copies for "
            f"{len(touches)} touches"
        )


def test_provider_returns_nonempty_text_for_each_touch():
    provider = make_followup_content_provider()
    for day, touches in CADENCE_SCHEDULE.items():
        for idx in range(len(touches)):
            content = provider(day, idx)
            assert content["type"] == "text"
            assert content["text"].strip(), f"empty copy at day {day} idx {idx}"
            assert content["day"] == day
            assert content["idx"] == idx


def test_provider_is_safe_for_out_of_range_idx():
    """An idx beyond the roster falls back to the last copy (never crashes)."""
    provider = make_followup_content_provider()
    content = provider(5, 99)
    assert content["text"].strip()


def test_copy_speaks_in_brand_voice():
    """Spot-check that the copy carries the +1 Passo acolhimento, not generic
    sales lines."""
    joined = " ".join(t for texts in FOLLOWUP_COPY.values() for t in texts).lower()
    assert "passo" in joined
    # warmth / not-alone messaging is core to the brand
    assert "sozinh" in joined or "aqui com você" in joined or "aqui com voce" in joined
