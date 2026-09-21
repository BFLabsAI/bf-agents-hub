"""WABA (WhatsApp Cloud API / Meta Graph API v23.0) webhook helpers.

Sibling module to shared/webhook_core.py (uazapi). Normalizes Cloud API
webhook payloads into the same dict shape as webhook_core.normalize_payload
so app.py's existing filters (is_bot_sent, is_allowed) keep working unchanged.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os

import httpx

logger = logging.getLogger(__name__)

GRAPH_API_URL = "https://graph.facebook.com/v23.0"


def verify_signature(
    raw_body: bytes, signature_header: str, app_secret: str | None = None
) -> bool:
    """Validate X-Hub-Signature-256 from Meta.

    Computes HMAC-SHA256 of raw_body using WABA_APP_SECRET (read from env
    inside the function, unless app_secret is passed explicitly — used by
    the backup-number route, which has its own app secret) and compares
    against the header, which must be in the form "sha256=<hex>".
    """
    if app_secret is None:
        app_secret = os.environ.get("WABA_APP_SECRET", "")

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(
        app_secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


# WABA message types -> our media_type vocabulary (matches uazapi's, so
# app.py's transcription/vision-cascade helpers stay shared across channels).
_WABA_MEDIA_TYPE_MAP = {
    "audio": "audio",
    "image": "image",
    "sticker": "image",
    "video": "video",
    "document": "document",
}


def parse_waba_payload(payload: dict) -> dict | None:
    """Normalize a WhatsApp Cloud API webhook payload.

    Returns the same key shape as shared.webhook_core.normalize_payload, plus
    a "media" key (None for text messages):
        chat_jid, sender_jid, text, sender_name, msg_id, is_group,
        from_me, was_sent_by_api, group_jid, media

    For audio/image/sticker/video/document messages, "text" is None and
    "media" is {"id", "mime_type", "type"} (type is one of
    audio|image|video|document) — the caller downloads and
    transcribes/analyzes it, then feeds the result as text.

    Returns None for: malformed/missing entry->changes->value structure,
    delivery status webhooks (statuses present, no messages), empty
    messages list, or unsupported message types (location, contacts,
    reaction, interactive, etc.).
    """
    try:
        value = payload["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError, TypeError):
        return None

    if "statuses" in value and "messages" not in value:
        return None

    messages = value.get("messages") or []
    if not messages:
        return None

    msg = messages[0]
    msg_type = msg.get("type")

    media = None
    if msg_type == "text":
        text = msg.get("text", {}).get("body")
        if text is None:
            return None
    elif msg_type in _WABA_MEDIA_TYPE_MAP:
        media_obj = msg.get(msg_type) or {}
        media_id = media_obj.get("id")
        if not media_id:
            return None
        text = None
        media = {
            "id": media_id,
            "mime_type": media_obj.get("mime_type", ""),
            "type": _WABA_MEDIA_TYPE_MAP[msg_type],
        }
    else:
        return None

    from_number = msg.get("from", "")

    contacts = value.get("contacts") or []
    sender_name = None
    if contacts:
        sender_name = contacts[0].get("profile", {}).get("name")

    return {
        "chat_jid": from_number,
        "sender_jid": from_number,
        "text": text,
        "sender_name": sender_name,
        "msg_id": msg.get("id", ""),
        "is_group": False,
        "from_me": False,
        "was_sent_by_api": False,
        "group_jid": from_number,
        "media": media,
    }


async def send_reply(
    chat_jid: str,
    text: str,
    reply_to_id: str = "",
    phone_number_id: str | None = None,
    access_token: str | None = None,
) -> None:
    """Send a text reply via the WhatsApp Cloud API (Meta Graph API).

    POSTs to /{phone_number_id}/messages using access_token as the bearer
    token. Both default to the WABA_PHONE_NUMBER_ID / WABA_ACCESS_TOKEN env
    vars when not passed explicitly — the backup-number route passes its
    own credentials instead. Never lets an exception propagate — logs and
    swallows.
    """
    if phone_number_id is None:
        phone_number_id = os.environ.get("WABA_PHONE_NUMBER_ID", "")
    if access_token is None:
        access_token = os.environ.get("WABA_ACCESS_TOKEN", "")

    url = f"{GRAPH_API_URL}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    body: dict = {
        "messaging_product": "whatsapp",
        "to": chat_jid,
        "type": "text",
        "text": {"body": text, "preview_url": False},
    }
    if reply_to_id:
        body["context"] = {"message_id": reply_to_id}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=body, headers=headers)
            response.raise_for_status()
    except Exception as exc:
        logger.error("send_reply failed for %s: %s", chat_jid, exc)


async def mark_as_read(
    message_id: str,
    phone_number_id: str | None = None,
    access_token: str | None = None,
) -> None:
    """Mark an inbound message as read and show the typing indicator.

    Meta's Cloud API piggybacks the native typing indicator on the same
    read-receipt call (no separate presence endpoint): POSTing
    status="read" with a typing_indicator shows the blue checks AND the
    "digitando..." bubble to the lead for ~25s or until the next message is
    sent. Same credential-override pattern as send_reply — call again every
    ~20s while the agent is still working to keep the indicator alive.
    """
    if phone_number_id is None:
        phone_number_id = os.environ.get("WABA_PHONE_NUMBER_ID", "")
    if access_token is None:
        access_token = os.environ.get("WABA_ACCESS_TOKEN", "")

    url = f"{GRAPH_API_URL}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    body = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
        "typing_indicator": {"type": "text"},
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=body, headers=headers)
            response.raise_for_status()
    except Exception as exc:
        logger.error("mark_as_read failed for %s: %s", message_id, exc)


async def send_template(
    to: str,
    template_name: str,
    language: str = "pt_BR",
    components: list | None = None,
    phone_number_id: str | None = None,
    access_token: str | None = None,
) -> dict:
    """Send a pre-approved HSM template via the WhatsApp Cloud API.

    Same credential-resolution pattern as send_reply (env defaults, override
    via explicit params for the backup number). UNLIKE send_reply, HTTP
    errors are NOT swallowed — they propagate (raise_for_status) so the P2
    admin API can report the failure to the operator instead of silently
    doing nothing. Returns the parsed JSON response body on success.
    """
    if phone_number_id is None:
        phone_number_id = os.environ.get("WABA_PHONE_NUMBER_ID", "")
    if access_token is None:
        access_token = os.environ.get("WABA_ACCESS_TOKEN", "")

    url = f"{GRAPH_API_URL}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    template: dict = {
        "name": template_name,
        "language": {"code": language},
    }
    if components:
        template["components"] = components

    body: dict = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": template,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(url, json=body, headers=headers)
        response.raise_for_status()
        return response.json()


def verify_challenge(
    mode: str, token: str, challenge: str, verify_token: str | None = None
) -> str | None:
    """Handle the Meta webhook verification GET request.

    Returns challenge if mode == "subscribe" and token matches verify_token
    (defaults to the WABA_WEBHOOK_VERIFY_TOKEN env var when not passed
    explicitly — the backup-number route passes its own token instead).
    """
    if verify_token is None:
        verify_token = os.environ.get("WABA_WEBHOOK_VERIFY_TOKEN", "")

    if mode == "subscribe" and token == verify_token:
        return challenge
    return None
