"""Business hours configuration.

Default timezone is America/Fortaleza (no DST). In "window" mode the agent /
cron treat messages and follow-ups outside open..close as off-hours. In "24h"
mode there is no restriction.

workdays: ISO weekday numbers (Mon=1 .. Sun=7) the business operates.
holidays: list of "YYYY-MM-DD" strings to treat as closed.
"""
from __future__ import annotations

# +1 Passo: the agent ALWAYS responds (24h) — someone in crisis writes at any
# hour and can never be left without a reply. The human repasse still respects
# the team's business hours (handled at handoff time), but the agent itself
# never holds a message.
BUSINESS_HOURS: dict = {
    "timezone": "America/Fortaleza",
    "mode": "24h",               # window | 24h
    "open": "09:00",             # used only when mode == "window"
    "close": "18:00",
    "workdays": [1, 2, 3, 4, 5],  # Mon..Fri (used only in window mode)
    "holidays": [],               # e.g. ["2026-12-25"]
}
