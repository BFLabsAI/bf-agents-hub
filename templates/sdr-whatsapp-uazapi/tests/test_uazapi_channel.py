"""Tests for channels/uazapi.py — webhook registration (append-only) + send_reply re-export."""
from __future__ import annotations

import json

import httpx
import pytest

from channels import uazapi


def test_send_reply_is_reexported():
    # send_reply must be importable from the channel module and be the
    # canonical sender from shared.webhook_core.
    from shared.webhook_core import send_reply as canonical

    assert uazapi.send_reply is canonical


async def test_register_webhook_appends_preserving_existing():
    # Given 2 existing webhooks, after register there must be 3, and the
    # original 2 must remain intact.
    existing = [
        {
            "id": "aaa",
            "enabled": True,
            "url": "https://one.example.com/webhook",
            "events": ["messages"],
            "excludeMessages": ["wasSentByApi"],
        },
        {
            "id": "bbb",
            "enabled": True,
            "url": "https://two.example.com/webhook",
            "events": ["connection"],
            "excludeMessages": [],
        },
    ]
    posted_bodies: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(200, json=existing)
        # POST -> record body, return success
        body = json.loads(req.content.decode() or "{}")
        posted_bodies.append(body)
        return httpx.Response(200, json={"status": "ok"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await uazapi.register_webhook(
        base="https://api.uazapi.test",
        token="tok123",
        instance="inst1",
        webhook_url="https://ours.example.com/sdr/webhook",
        http=client,
    )

    urls = [w["url"] for w in result]
    assert "https://one.example.com/webhook" in urls
    assert "https://two.example.com/webhook" in urls
    assert "https://ours.example.com/sdr/webhook" in urls
    assert len(result) == 3
    # original entries preserved intact
    assert existing[0] in result
    assert existing[1] in result


async def test_register_webhook_is_idempotent():
    # If our url is already registered, do NOT POST a duplicate.
    ours = "https://ours.example.com/sdr/webhook"
    existing = [
        {"id": "aaa", "enabled": True, "url": "https://one.example.com/webhook", "events": ["messages"]},
        {"id": "ccc", "enabled": True, "url": ours, "events": ["messages"]},
    ]
    post_count = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if req.method == "GET":
            return httpx.Response(200, json=existing)
        post_count += 1
        return httpx.Response(200, json={"status": "ok"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    result = await uazapi.register_webhook(
        base="https://api.uazapi.test",
        token="tok123",
        instance="inst1",
        webhook_url=ours,
        http=client,
    )

    assert post_count == 0  # no duplicate POST
    assert len(result) == 2
    assert [w["url"] for w in result].count(ours) == 1


async def test_register_webhook_uses_token_header():
    # The token must be sent in the `token` header on both GET and POST.
    seen_tokens: list[str | None] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_tokens.append(req.headers.get("token"))
        if req.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={"status": "ok"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    await uazapi.register_webhook(
        base="https://api.uazapi.test",
        token="secret-token",
        instance="inst1",
        webhook_url="https://ours.example.com/sdr/webhook",
        http=client,
    )

    assert seen_tokens  # at least the GET happened
    assert all(t == "secret-token" for t in seen_tokens)
