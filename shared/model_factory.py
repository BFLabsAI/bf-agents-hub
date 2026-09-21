from __future__ import annotations

import os

from agno.models.openai import OpenAIChat

OMNIROUTE_BASE = "https://proxy.bflabs.com.br/v1"


def omniroute(model_id: str | None = None) -> OpenAIChat:
    """Return an OpenAIChat instance pointing at the Omniroute proxy."""
    return OpenAIChat(
        id=model_id or os.getenv("AGNO_DEFAULT_MODEL", "cx/gpt-5.6-luna"),
        base_url=OMNIROUTE_BASE,
        api_key=os.getenv("OMNIROUTE_API_KEY", "omniroute"),
        max_tokens=None,
    )


def omniroute_fast() -> OpenAIChat:
    """Return an OpenAIChat instance using the fast model (mimo-v2.5-pro)."""
    return OpenAIChat(
        id="mimo/mimo-v2.5-pro",
        base_url=OMNIROUTE_BASE,
        api_key=os.getenv("OMNIROUTE_API_KEY", "omniroute"),
        max_tokens=None,
    )
