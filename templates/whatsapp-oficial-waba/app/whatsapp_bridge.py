import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

from app.config import WABA_APP_SECRET, OPENROUTER_MODEL_ID

logger = logging.getLogger("italo.bridge")


@dataclass(slots=True)
class IncomingWABAMessage:
    wamid: str
    from_number: str       # E.164 without +: "5511999999999"
    sender_name: str | None
    phone_number_id: str
    text: str
    raw_payload: dict[str, Any]
    # Multimodal — set when the user sends audio/image/document.
    # The webhook handler downloads the binary, runs it through the Gemini
    # preprocessor, and replaces `text` with the preprocessor output before
    # the message reaches the agent.
    media_id: str | None = None
    media_mime: str | None = None
    media_kind: str | None = None       # 'audio' | 'image' | 'document'
    media_caption: str | None = None    # user-provided caption (image/document)


def verify_signature(raw_body: bytes, signature_header: str) -> bool:
    """Validate X-Hub-Signature-256 from Meta."""
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        WABA_APP_SECRET.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


def _interactive_to_text(interactive: dict) -> str | None:
    """
    Convert an interactive reply (button_reply / list_reply) to natural-language text
    that the agent can understand.

    Button/row ID conventions:
      info_{activityScheduleId}  → "Quero mais informações sobre o evento {id}"
      sub_{activityScheduleId}   → "Quero me inscrever no evento {id}"
    """
    itype = interactive.get("type")

    if itype == "button_reply":
        bid = interactive.get("button_reply", {}).get("id", "")
        if bid.startswith("info_"):
            aid = bid[len("info_"):]
            return f"Quero mais informações sobre o evento {aid}"
        if bid.startswith("sub_"):
            aid = bid[len("sub_"):]
            return f"Quero me inscrever no evento {aid}"
        if bid.startswith("receipt_"):
            ar_id = bid[len("receipt_"):]
            return f"/recibo {ar_id}"
        # Unknown — fall back to button title
        return interactive.get("button_reply", {}).get("title", bid) or None

    if itype == "list_reply":
        rid = interactive.get("list_reply", {}).get("id", "")
        if rid.startswith("info_"):
            aid = rid[len("info_"):]
            return f"Quero mais informações sobre o evento {aid}"
        if rid.startswith("sub_"):
            aid = rid[len("sub_"):]
            return f"Quero me inscrever no evento {aid}"
        return interactive.get("list_reply", {}).get("title", rid) or None

    return None


def parse_waba_payload(payload: dict) -> IncomingWABAMessage | None:
    """
    Extract a message from a WABA webhook payload.
    Handles:
      • text messages
      • interactive button_reply / list_reply (from carousels / list messages)
    Returns None for status updates or unsupported types.
    """
    try:
        value = payload["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError):
        return None

    if "statuses" in value and "messages" not in value:
        return None

    messages = value.get("messages", [])
    if not messages:
        return None

    msg = messages[0]
    msg_type = msg.get("type")

    media_id: str | None = None
    media_mime: str | None = None
    media_kind: str | None = None
    media_caption: str | None = None

    if msg_type == "text":
        text = msg["text"]["body"]
    elif msg_type == "interactive":
        text = _interactive_to_text(msg.get("interactive", {}))
        if text is None:
            return None
    elif msg_type == "button":
        # Carousel button reply on some WhatsApp clients sends type="button"
        btn = msg.get("button", {})
        payload = btn.get("payload", "")
        if payload.startswith("info_"):
            text = f"Quero mais informações sobre o evento {payload[len('info_'):]}"
        elif payload.startswith("sub_"):
            text = f"Quero me inscrever no evento {payload[len('sub_'):]}"
        else:
            text = payload or btn.get("text", "")
        if not text:
            return None
    elif msg_type in ("audio", "voice"):
        audio = msg.get("audio") or msg.get("voice") or {}
        media_id = audio.get("id")
        if not media_id:
            return None
        media_mime = audio.get("mime_type", "audio/ogg")
        media_kind = "audio"
        text = ""  # filled in by media preprocessor before reaching the agent
    elif msg_type == "image":
        img = msg.get("image", {})
        media_id = img.get("id")
        if not media_id:
            return None
        media_mime = img.get("mime_type", "image/jpeg")
        media_kind = "image"
        media_caption = img.get("caption")
        text = ""
    elif msg_type == "document":
        doc = msg.get("document", {})
        media_id = doc.get("id")
        mime = doc.get("mime_type", "")
        if not media_id or "pdf" not in mime.lower():
            # Only PDFs are supported for now.
            return None
        media_mime = mime
        media_kind = "document"
        media_caption = doc.get("caption") or doc.get("filename")
        text = ""
    else:
        return None

    contacts = value.get("contacts", [])
    sender_name = contacts[0]["profile"]["name"] if contacts else None

    return IncomingWABAMessage(
        wamid=msg["id"],
        from_number=msg["from"],
        sender_name=sender_name,
        phone_number_id=value["metadata"]["phone_number_id"],
        text=text,
        raw_payload=payload,
        media_id=media_id,
        media_mime=media_mime,
        media_kind=media_kind,
        media_caption=media_caption,
    )


class AgentRuntime:
    """Thread-safe pool of Agent instances keyed by session_id."""

    def __init__(self, agent_factory: Callable[..., Any]) -> None:
        self._factory = agent_factory
        self._agents: dict[str, Any] = {}
        self._lock = Lock()

    def get_agent(self, session_id: str, from_number: str = "") -> Any:
        with self._lock:
            if session_id not in self._agents:
                self._agents[session_id] = self._factory(
                    session_id=session_id,
                    from_number=from_number,
                )
            return self._agents[session_id]

    def evict(self, phone: str) -> None:
        """Remove all sessions matching this phone from the in-memory pool."""
        with self._lock:
            to_remove = [k for k in self._agents if phone in k]
            for k in to_remove:
                del self._agents[k]
            if to_remove:
                logger.info("evicted %d session(s) for phone %s", len(to_remove), phone)


class WABABridge:
    def __init__(self, agent_factory: Callable[..., Any]) -> None:
        self._runtime = AgentRuntime(agent_factory)

    def session_id_for(self, msg: IncomingWABAMessage) -> str:
        return f"italo-wa-{msg.from_number}"

    async def generate_reply(self, msg: IncomingWABAMessage) -> str:
        from app import pause_registry
        if pause_registry.is_paused(msg.from_number):
            logger.info("AI paused for %s — skipping reply", msg.from_number)
            return ""

        session_id = self.session_id_for(msg)
        agent = self._runtime.get_agent(session_id, from_number=msg.from_number)

        # Strip large cached lists from session_state before the LLM call.
        for _cache_key in ("available_events", "available_categories", "available_payment_methods"):
            agent.session_state.pop(_cache_key, None)

        t0 = time.monotonic()
        result = await agent.arun(msg.text, session_state=agent.session_state)
        latency_ms = int((time.monotonic() - t0) * 1000)

        # Persist LLM usage metrics
        try:
            import app.llm_usage_log as llm_log
            m = getattr(result, "metrics", None)
            if m:
                llm_log.save(
                    session_id=session_id,
                    model=OPENROUTER_MODEL_ID,
                    input_tokens=getattr(m, "input_tokens", 0) or 0,
                    output_tokens=getattr(m, "output_tokens", 0) or 0,
                    cache_read_tokens=getattr(m, "cache_read_tokens", 0) or 0,
                    cache_write_tokens=getattr(m, "cache_write_tokens", 0) or 0,
                    total_tokens=getattr(m, "total_tokens", 0) or 0,
                    cost_usd=getattr(m, "cost", None),
                    latency_ms=latency_ms,
                )
        except Exception:
            pass

        return result.content if hasattr(result, "content") else str(result)
