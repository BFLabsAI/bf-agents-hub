"""Handoff configuration.

For this build ONLY the "repasse" mode is supported. A repasse sends a
standardized message to BOTH:
  - the notification group JID, AND
  - directly to the single assigned vendor's phone number.

calendar / custom_api modes are stubs (see core.handoff_engine) and raise
NotImplementedError when invoked.

group_jid and the vendor phone come from the environment (placeholders here).
"""
from __future__ import annotations

import os

# Configuração de repasse de leads qualificados para atendentes/consultores humanos.
# O repasse pode enviar notificação para um grupo de WhatsApp e/ou diretamente
# para o número do consultor responsável.
HANDOFF: dict = {
    "mode": "repasse",            # repasse | calendar (stub) | custom_api (stub)
    "distribution": "single",    # single | round_robin | by_product
    "group_jid": os.getenv("NOTIFICATION_GROUP_JID", ""),
    # Vendor roster. Each: {"key", "name", "phone"}.
    "vendors": [
        {
            "key": "consultor_padrao",
            "name": "Consultor Comercial",
            "phone": os.getenv("VENDOR_PHONE", ""),
            "active": True,
        },
    ],
    # Which vendor receives leads in 'single' distribution mode.
    "single_vendor": "consultor_padrao",
    "calendar_id": None,
    "booking_api": None,
}
