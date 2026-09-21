"""SDR template agent — single Agno Agent instance (built at import).

Mirrors the proven pattern in sdr-plus-um-passo/agent.py:
  omniroute() model, AsyncPostgresDb (driver postgresql+psycopg_async),
  tools list, system_message from build_system_prompt(IDENTITY),
  add_history_to_context=True, num_history_runs=15,
  add_session_state_to_context=True,
  LearningMachine(UserProfileConfig(ALWAYS), UserMemoryConfig(AGENTIC)),
  retries=2, exponential_backoff=True, telemetry=False, markdown=False.

Pair with agent.arun() ONLY — AsyncPostgresDb is async-only. The FastAPI app
must run uvicorn --workers 1 (the async engine is not fork-safe).
"""
from __future__ import annotations

import os
import sys

# Make the bf-agents repo root importable so `shared` resolves, regardless of
# how deep this agent folder is nested (blueprints/sdr-template vs sdr-plus-um-passo).
_d = os.path.dirname(os.path.abspath(__file__))
while _d != "/" and not os.path.isdir(os.path.join(_d, "shared")):
    _d = os.path.dirname(_d)
if _d not in sys.path:
    sys.path.insert(0, _d)

try:  # load .env when present (no hard dependency in tests)
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv optional
    pass

from agno.agent import Agent
from agno.db.postgres import AsyncPostgresDb
from agno.learn import (
    LearningMachine,
    LearningMode,
    UserMemoryConfig,
    UserProfileConfig,
)

from shared.model_factory import omniroute

from config import IDENTITY
from system_prompt import build_system_prompt
from tools import (
    check_business_hours,
    classify_and_mark_lost,
    get_product_info,
    log_event,
    request_handoff,
    update_qualification_fields,
)

# Initial SDR session_state keys (mirrors {PREFIX}leads columns the agent reads).
DEFAULT_SESSION_STATE: dict = {
    "lead_id": None,
    "cadence_day": 1,
    "cadence_paused": False,
    "last_activity": None,
    "qualification_fields": {},
    "handoff_done": False,
    "assigned_vendor": None,
    "opt_out": False,
}

TOOLS = [
    update_qualification_fields,
    request_handoff,
    classify_and_mark_lost,
    check_business_hours,
    get_product_info,
    log_event,
]


def build_agent() -> Agent:
    """Construct and return the configured Agno Agent.

    Wires omniroute() model + AsyncPostgresDb(DATABASE_URL) + the @tool list +
    system_message=build_system_prompt(IDENTITY) + history/session-state context
    + LearningMachine + retries/backoff. Pair with agent.arun() only.
    """
    db = AsyncPostgresDb(
        db_url=os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg_async://user:password@localhost:5432/sdr_template",
        )
    )

    return Agent(
        id="sdr-template",
        name=IDENTITY.get("agent_name", "SDR"),
        description=f"SDR de qualificacao para {IDENTITY.get('company_name', '')}.",
        model=omniroute(),
        db=db,
        tools=TOOLS,
        system_message=build_system_prompt(IDENTITY),
        add_history_to_context=True,
        num_history_runs=15,
        add_session_state_to_context=True,
        session_state=dict(DEFAULT_SESSION_STATE),
        learning=LearningMachine(
            user_profile=UserProfileConfig(mode=LearningMode.ALWAYS),
            user_memory=UserMemoryConfig(mode=LearningMode.AGENTIC),
        ),
        retries=2,
        exponential_backoff=True,
        debug_mode=os.getenv("DEBUG_MODE", "false") == "true",
        telemetry=False,
        markdown=False,
    )


# Module-level singleton (built at import, mirrors sdr-plus-um-passo).
agent = build_agent()
