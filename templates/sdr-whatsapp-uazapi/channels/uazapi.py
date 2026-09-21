"""uazapi WhatsApp channel helpers.

Re-exports `send_reply` from shared.webhook_core (the canonical sender) and
adds webhook registration. The repo root is added to sys.path so
`shared.webhook_core` is importable when running from this blueprint dir.
"""
from __future__ import annotations

import os
import sys
from typing import Any

# Make the repo root (.../bf-agents) importable so `shared` resolves, regardless
# of how deep this agent folder is nested. Walk up until we find `shared/`.
_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
while _REPO_ROOT != "/" and not os.path.isdir(os.path.join(_REPO_ROOT, "shared")):
    _REPO_ROOT = os.path.dirname(_REPO_ROOT)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.webhook_core import send_reply  # noqa: E402  re-exported

__all__ = ["send_reply", "register_webhook"]


import httpx  # noqa: E402

# Events our SDR agent cares about.
# Exclude: wasSentByApi (loop prevention) + isGroupYes (SDR only handles direct/personal chats).
_DEFAULT_EVENTS = ["messages"]
_DEFAULT_EXCLUDE = ["wasSentByApi", "isGroupYes"]


async def register_webhook(
    base: str,
    token: str,
    instance: str,
    webhook_url: str,
    http: httpx.AsyncClient | None = None,
) -> None:
    """Register our webhook once. If already registered, does nothing."""
    headers = {"token": token, "Content-Type": "application/json"}
    url = f"{base}/webhook"

    owns_client = http is None
    client = http or httpx.AsyncClient(timeout=20)
    try:
        existing = (await client.get(url, headers=headers)).json() or []
        if not isinstance(existing, list):
            existing = [existing]
        if any(w.get("url") == webhook_url for w in existing):
            return
        await client.post(url, headers=headers, json={
            "action": "add",
            "url": webhook_url,
            "events": list(_DEFAULT_EVENTS),
            "excludeMessages": list(_DEFAULT_EXCLUDE),
            "enabled": True,
        })
    finally:
        if owns_client:
            await client.aclose()
