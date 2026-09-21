from __future__ import annotations

import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)


def normalize_payload(payload: dict) -> dict | None:
    """
    Normalize uazapi webhook payloads.

    Actual uazapi Message schema fields (from OpenAPI spec):
      chatid (lowercase), sender (not senderJid), messageid (lowercase),
      isGroup, fromMe, text, wasSentByApi (boolean)

    WebhookEvent wrapper: {"event": "messages", "instance": "...", "data": {Message}}

    Returns dict with keys:
        chat_jid   — chatid: correct reply-to for both direct and group messages
        sender_jid — sender: individual who sent (same as chat_jid for direct)
        text, sender_name, msg_id, is_group, from_me, was_sent_by_api

    Returns None if event != "messages", no text, or no chat_jid.
    """
    event = payload.get("event") or payload.get("EventType") or ""
    if event.lower() != "messages":
        return None

    data = payload.get("data") or payload.get("message") or {}

    # uazapi uses lowercase field names per OpenAPI spec
    chat_jid = data.get("chatid") or data.get("chatId") or ""
    # content can be a dict (media payload) — only use it as text if it's a string
    _text = data.get("text") or ""
    if not _text:
        _content = data.get("content")
        _text = _content if isinstance(_content, str) else ""
    text = _text
    is_group = data.get("isGroup", False)
    # actual field is "sender", not "senderJid"
    # prefer sender_pn (real phone JID) over sender which may be a LID (@lid)
    sender_jid = data.get("sender_pn") or data.get("sender") or data.get("senderJid") or chat_jid
    msg_id = data.get("messageid") or data.get("messageId") or ""
    from_me = data.get("fromMe", False)
    was_sent_by_api = data.get("wasSentByApi", False)

    if not text:
        return None
    if not chat_jid:
        return None

    sender_name = re.sub(r"@.*$", "", sender_jid) if sender_jid else ""

    return {
        "chat_jid": chat_jid,
        "sender_jid": sender_jid,
        "text": text,
        "sender_name": sender_name,
        "msg_id": msg_id,
        "is_group": is_group,
        "from_me": from_me,
        "was_sent_by_api": was_sent_by_api,
        # keep legacy key so existing code that reads "group_jid" still works
        "group_jid": chat_jid,
    }


def is_bot_sent(msg: dict) -> bool:
    """Return True if the message was sent by the bot itself or via API."""
    if msg.get("from_me") is True:
        return True
    # wasSentByApi is a boolean field per OpenAPI spec
    if msg.get("was_sent_by_api") is True:
        return True
    return False


def is_allowed(sender_jid: str, allowed: set[str] | None) -> bool:
    """
    Check if sender_jid is in the allowed set.

    - If allowed is None: open agent, always return True.
    - If allowed is a set: extract digits from sender_jid, check if any
      allowed number ends with or starts with those digits.
    """
    if allowed is None:
        return True

    digits = re.sub(r"\D", "", sender_jid)
    for number in allowed:
        num_digits = re.sub(r"\D", "", number)
        if not num_digits:
            continue
        if digits.endswith(num_digits) or digits.startswith(num_digits):
            return True
        if num_digits.endswith(digits) or num_digits.startswith(digits):
            return True
    return False


async def send_reply(chat_jid: str, text: str, reply_to_id: str = "") -> None:
    """Send a text reply via uazapi /send/text.

    Field names per OpenAPI spec: number, text, replyid (lowercase), delay.
    delay=1500 shows "Digitando..." for 1.5s before the message appears.
    """
    base = os.environ.get("UAZAPI_BASE", "")
    token = os.environ.get("UAZAPI_TOKEN", "")

    url = f"{base}/send/text"
    headers = {"token": token, "Content-Type": "application/json"}
    body: dict = {"number": chat_jid, "text": text, "delay": 1500}
    if reply_to_id:
        body["replyid"] = reply_to_id

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=body, headers=headers)
            response.raise_for_status()
    except Exception as exc:
        logger.error("send_reply failed for %s: %s", chat_jid, exc)


class MessageDedup:
    """Simple in-memory deduplication tracker for message IDs."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._cap: int = 1000

    def seen(self, msg_id: str) -> bool:
        """Return True if msg_id was already processed."""
        return msg_id in self._seen

    def mark(self, msg_id: str) -> None:
        """Mark msg_id as processed. Clears the set when cap is exceeded."""
        self._seen.add(msg_id)
        if len(self._seen) > self._cap:
            self._seen.clear()
