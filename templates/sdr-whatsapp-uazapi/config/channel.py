"""WhatsApp channel (uazapi) configuration.

Secrets/instance values come from the environment only — never hardcode them
(see .env.example). Reads happen at import time so the running process picks up
whatever the loaded .env provides.
"""
from __future__ import annotations

import os

CHANNEL: dict = {
    "provider": "uazapi",
    "instance": os.getenv("UAZAPI_INSTANCE", ""),
    "base": os.getenv("UAZAPI_BASE", ""),
    "token": os.getenv("UAZAPI_TOKEN", ""),
}
