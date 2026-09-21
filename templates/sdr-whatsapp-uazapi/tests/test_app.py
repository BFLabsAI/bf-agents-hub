"""Integration tests for app.py — the FastAPI webhook + buffer + agent wiring.

The agent, send_reply and DB connection are FAKED so these tests never touch
the network, the live model, or Postgres. We use a tiny buffer window so the
debounced flush fires quickly.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

import app as app_module


class FakeResult:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeAgent:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def arun(self, text, session_id=None, user_id=None, dependencies=None, **kw):
        self.calls.append(
            {
                "text": text,
                "session_id": session_id,
                "user_id": user_id,
                "dependencies": dependencies,
            }
        )
        return FakeResult(f"resposta para: {text}")


def make_client(agent, sent, **overrides):
    async def fake_send(chat_jid, text, reply_to=""):
        sent.append({"jid": chat_jid, "text": text, "reply_to": reply_to})

    async def fake_conn_factory():
        return None  # tools don't run in FakeAgent; conn unused

    overrides.setdefault("buffer_window", 0.02)
    application = app_module.create_app(
        agent=agent,
        send_reply=fake_send,
        conn_factory=fake_conn_factory,
        start_background=False,
        **overrides,
    )
    transport = httpx.ASGITransport(app=application)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    return client


def fmt_a(text="oi", msg_id="m1", sender="5588999@s.whatsapp.net"):
    return {
        "event": "messages",
        "data": {
            "chatId": sender,
            "text": text,
            "senderJid": sender,
            "messageId": msg_id,
            "fromMe": False,
            "isGroup": False,
        },
    }


async def _wait_for(predicate, timeout=2.0):
    loop = asyncio.get_event_loop()
    start = loop.time()
    while loop.time() - start < timeout:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


async def test_webhook_buffers_then_calls_agent_and_send_reply():
    agent = FakeAgent()
    sent: list[dict] = []
    client = make_client(agent, sent)
    async with client:
        resp = await client.post("/sdr/webhook", json=fmt_a(text="ola tudo bem"))
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        ok = await _wait_for(lambda: len(sent) >= 1)
        assert ok, "agent/send_reply never fired after buffer flush"

    assert len(agent.calls) == 1
    call = agent.calls[0]
    assert call["session_id"] == "sdr-5588999"
    assert call["user_id"] == "5588999"
    assert "ola tudo bem" in call["text"]
    assert sent[0]["jid"] == "5588999@s.whatsapp.net"
    assert sent[0]["text"].startswith("resposta para:")


async def test_bot_sent_message_is_skipped():
    agent = FakeAgent()
    sent: list[dict] = []
    client = make_client(agent, sent)
    async with client:
        payload = fmt_a()
        payload["data"]["fromMe"] = True
        resp = await client.post("/sdr/webhook", json=payload)
        assert resp.json()["skipped"] == "bot_sent"
        await asyncio.sleep(0.05)
    assert agent.calls == []
    assert sent == []


async def test_not_allowed_message_is_skipped():
    agent = FakeAgent()
    sent: list[dict] = []
    client = make_client(agent, sent, allowed_senders={"5511000000000"})
    async with client:
        resp = await client.post("/sdr/webhook", json=fmt_a(sender="5588999@s.whatsapp.net"))
        assert resp.json()["skipped"] == "not_allowed"
        await asyncio.sleep(0.05)
    assert agent.calls == []


async def test_duplicate_message_is_skipped():
    agent = FakeAgent()
    sent: list[dict] = []
    client = make_client(agent, sent)
    async with client:
        first = await client.post("/sdr/webhook", json=fmt_a(msg_id="dup1"))
        assert first.json()["ok"] is True
        second = await client.post("/sdr/webhook", json=fmt_a(msg_id="dup1"))
        assert second.json()["skipped"] == "duplicate"
        await _wait_for(lambda: len(sent) >= 1)
    # Only the first message reached the agent.
    assert len(agent.calls) == 1


async def test_empty_message_is_skipped():
    agent = FakeAgent()
    sent: list[dict] = []
    client = make_client(agent, sent)
    async with client:
        resp = await client.post("/sdr/webhook", json=fmt_a(text=""))
        # normalize_payload drops empty-text messages -> not_message
        assert resp.json()["skipped"] in ("empty", "not_message")
        await asyncio.sleep(0.05)
    assert agent.calls == []


async def test_burst_messages_coalesced_into_single_agent_call():
    agent = FakeAgent()
    sent: list[dict] = []
    # Slightly larger window so both pushes land before flush.
    client = make_client(agent, sent, buffer_window=0.15)
    async with client:
        await client.post("/sdr/webhook", json=fmt_a(text="parte um", msg_id="b1"))
        await asyncio.sleep(0.03)
        await client.post("/sdr/webhook", json=fmt_a(text="parte dois", msg_id="b2"))
        ok = await _wait_for(lambda: len(sent) >= 1, timeout=2.0)
        assert ok
        # Give any stray second flush a chance (should not happen).
        await asyncio.sleep(0.1)
    assert len(agent.calls) == 1
    combined = agent.calls[0]["text"]
    assert "parte um" in combined and "parte dois" in combined


async def test_media_message_is_transcribed_and_fed_to_agent():
    agent = FakeAgent()
    sent: list[dict] = []

    async def fake_transcriber(url, mtype, caller):
        return f"[transcrição de {mtype}]"

    client = make_client(agent, sent, media_transcriber=fake_transcriber)
    async with client:
        payload = {
            "event": "messages",
            "data": {
                "chatId": "5511@s.whatsapp.net",
                "text": "",
                "senderJid": "5511@s.whatsapp.net",
                "messageId": "med1",
                "isGroup": False,
                "mediaUrl": "https://x/audio.ogg",
                "mediaType": "audio",
            },
        }
        resp = await client.post("/sdr/webhook", json=payload)
        assert resp.json()["ok"] is True
        ok = await _wait_for(lambda: len(agent.calls) >= 1)
        assert ok
    assert "transcrição de audio" in agent.calls[0]["text"]


async def test_health_endpoint():
    agent = FakeAgent()
    sent: list[dict] = []
    client = make_client(agent, sent)
    async with client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "agent": "sdr"}
