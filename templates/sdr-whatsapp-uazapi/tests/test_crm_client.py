"""Tests for core.crm_client — GHLClient over the GoHighLevel REST API.

External HTTP is faked with httpx.MockTransport (via the make_fake_httpx helper)
so tests never touch the network.
"""
from __future__ import annotations

import httpx
import pytest

from core.crm_client import GHLClient, GHLError
from tests.conftest import make_fake_httpx

PIT = "pit-test-token"
LOCATION = "loc-123"


async def test_upsert_contact_posts_with_auth_and_returns_id():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["method"] = req.method
        captured["auth"] = req.headers.get("Authorization")
        captured["version"] = req.headers.get("Version")
        return httpx.Response(200, json={"contact": {"id": "contact_abc"}})

    client = GHLClient(PIT, LOCATION, http=make_fake_httpx(handler))
    result = await client.upsert_contact(phone="5585999990001", name="Alice")

    assert result["id"] == "contact_abc"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/contacts/upsert")
    assert captured["auth"] == f"Bearer {PIT}"
    assert captured["version"]  # Version header set per GHL API


async def test_create_opportunity_posts_to_pipeline_and_returns_id():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["method"] = req.method
        import json as _json

        captured["body"] = _json.loads(req.content)
        return httpx.Response(201, json={"opportunity": {"id": "opp_xyz"}})

    client = GHLClient(PIT, LOCATION, http=make_fake_httpx(handler))
    result = await client.create_opportunity(
        contact_id="contact_abc",
        pipeline_id="pipe_1",
        stage_id="stage_1",
        name="Alice deal",
    )

    assert result["id"] == "opp_xyz"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/opportunities/")
    assert captured["body"]["pipelineId"] == "pipe_1"
    assert captured["body"]["pipelineStageId"] == "stage_1"
    assert captured["body"]["contactId"] == "contact_abc"


async def test_move_stage_puts_new_pipeline_stage_id():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json as _json

        captured["url"] = str(req.url)
        captured["method"] = req.method
        captured["body"] = _json.loads(req.content)
        return httpx.Response(
            200, json={"opportunity": {"id": "opp_xyz", "pipelineStageId": "stage_2"}}
        )

    client = GHLClient(PIT, LOCATION, http=make_fake_httpx(handler))
    result = await client.move_stage(opp_id="opp_xyz", stage_id="stage_2")

    assert result["pipelineStageId"] == "stage_2"
    assert captured["method"] == "PUT"
    assert captured["url"].endswith("/opportunities/opp_xyz")
    assert captured["body"]["pipelineStageId"] == "stage_2"


async def test_add_note_posts_body_to_contact():
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json as _json

        captured["url"] = str(req.url)
        captured["method"] = req.method
        captured["body"] = _json.loads(req.content)
        return httpx.Response(
            201, json={"note": {"id": "note_1", "body": "follow-up dia 2 enviado"}}
        )

    client = GHLClient(PIT, LOCATION, http=make_fake_httpx(handler))
    result = await client.add_note(
        contact_id="contact_abc", body="follow-up dia 2 enviado"
    )

    assert result["id"] == "note_1"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/contacts/contact_abc/notes")
    assert captured["body"]["body"] == "follow-up dia 2 enviado"


async def test_server_error_raises_typed_ghl_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "internal error"})

    client = GHLClient(PIT, LOCATION, http=make_fake_httpx(handler))

    with pytest.raises(GHLError) as exc_info:
        await client.upsert_contact(phone="5585999990001", name="Alice")

    assert exc_info.value.status_code == 500
    assert "internal error" in exc_info.value.body


async def test_client_error_4xx_also_raises():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "bad input"})

    client = GHLClient(PIT, LOCATION, http=make_fake_httpx(handler))

    with pytest.raises(GHLError) as exc_info:
        await client.create_opportunity(
            contact_id="c", pipeline_id="p", stage_id="s"
        )

    assert exc_info.value.status_code == 422
