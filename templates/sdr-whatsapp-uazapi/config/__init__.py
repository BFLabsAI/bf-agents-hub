"""Config package — exposes all configuration objects.

Usage:
    from config import IDENTITY, CHANNEL, HANDOFF, CADENCE_SCHEDULE, ...
"""
from __future__ import annotations

from .business_hours import BUSINESS_HOURS
from .cadence import (
    CADENCE_SCHEDULE,
    FOLLOWUP_COPY,
    SILENCE_WINDOW_MINUTES,
    make_followup_content_provider,
)
from .channel import CHANNEL
from .crm import CRM
from .handoff import HANDOFF
from .identity import IDENTITY

__all__ = [
    "IDENTITY",
    "CHANNEL",
    "HANDOFF",
    "CADENCE_SCHEDULE",
    "FOLLOWUP_COPY",
    "make_followup_content_provider",
    "SILENCE_WINDOW_MINUTES",
    "BUSINESS_HOURS",
    "CRM",
]
