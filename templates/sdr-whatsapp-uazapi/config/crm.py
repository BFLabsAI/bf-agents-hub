"""GoHighLevel (GHL) CRM configuration.

The GHL Private Integration Token (GHL_PIT) is read from the environment only.
cadence_day in {PREFIX}leads is the source of truth; the `stages` map below
tells the CRM client which pipeline stage id mirrors each cadence day / status.

field_map maps internal qualification field keys -> GHL custom field ids.
"""
from __future__ import annotations

import os

CRM: dict = {
    "enabled": bool(os.getenv("GHL_PIT")),
    "pit": os.getenv("GHL_PIT"),                      # secret — env only
    "location_id": os.getenv("GHL_LOCATION_ID", ""),
    "pipeline_id": os.getenv("GHL_PIPELINE_ID", ""),
    "base_url": os.getenv("GHL_BASE_URL", "https://services.leadconnectorhq.com"),
    # Maps a logical stage key -> GHL pipeline stage id. Keys "1".."5" mirror
    # cadence_day; "new"/"qualified"/"scheduled"/"lost" mirror lead status.
    "stages": {
        "new": "",
        "1": "",
        "2": "",
        "3": "",
        "4": "",
        "5": "",
        "qualified": "",
        "scheduled": "",
        "lost": "",
    },
    # internal_field_key -> GHL custom field id
    "field_map": {},
}
