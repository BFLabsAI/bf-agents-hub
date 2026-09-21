"""GoHighLevel (GHL) CRM client.

Thin async wrapper over the GHL REST API using httpx.AsyncClient. The client is
injectable so tests can pass an httpx.AsyncClient backed by a MockTransport and
never hit the network. The PIT (Private Integration Token) is passed in by the
caller (read from config/crm.py which reads env) — never hardcoded here.
"""
from __future__ import annotations

from typing import Any

import httpx

# GHL LeadConnector API version header (required on every request).
GHL_API_VERSION = "2021-07-28"


class GHLError(RuntimeError):
    """Raised when the GHL API returns a non-2xx response.

    Carries the HTTP status code and response body so callers can flag
    `crm_error` in the handoff flow.
    """

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"GHL API error {status_code}: {body}")


class GHLClient:
    """Async GoHighLevel API client.

    Args:
        pit: GHL Private Integration Token (Bearer).
        location_id: GHL location/sub-account id.
        base_url: GHL API base (default services.leadconnectorhq.com).
        http: optional injected httpx.AsyncClient (for tests). If None, the
            implementation creates its own.
    """

    def __init__(
        self,
        pit: str,
        location_id: str,
        base_url: str = "https://services.leadconnectorhq.com",
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.pit = pit
        self.location_id = location_id
        self.base_url = base_url.rstrip("/")
        self._http = http

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.pit}",
            "Version": GHL_API_VERSION,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _client(self) -> httpx.AsyncClient:
        if self._http is not None:
            return self._http
        return httpx.AsyncClient()

    async def _request(self, method: str, path: str, json: dict | None = None) -> dict:
        """Send a request and return the parsed JSON body.

        Raises GHLError on any non-2xx response so callers can flag crm_error.
        """
        url = f"{self.base_url}{path}"
        owns_client = self._http is None
        client = self._client()
        try:
            resp = await client.request(
                method, url, json=json, headers=self._headers
            )
        finally:
            if owns_client:
                await client.aclose()
        if resp.status_code >= 400:
            raise GHLError(resp.status_code, resp.text)
        return resp.json()

    async def upsert_contact(
        self, phone: str, name: str | None = None, fields: dict | None = None
    ) -> dict:
        """Create or update a GHL contact by phone. Returns the contact dict
        (must include the contact id)."""
        payload: dict[str, Any] = {
            "locationId": self.location_id,
            "phone": phone,
        }
        if name is not None:
            payload["name"] = name
        if fields:
            payload.update(fields)
        data = await self._request("POST", "/contacts/upsert", json=payload)
        return data.get("contact", data)

    async def create_opportunity(
        self,
        contact_id: str,
        pipeline_id: str,
        stage_id: str,
        name: str | None = None,
    ) -> dict:
        """Create an opportunity for `contact_id` in the given pipeline/stage.
        Returns the opportunity dict (must include the opportunity id)."""
        payload: dict[str, Any] = {
            "locationId": self.location_id,
            "contactId": contact_id,
            "pipelineId": pipeline_id,
            "pipelineStageId": stage_id,
            "status": "open",
        }
        if name is not None:
            payload["name"] = name
        data = await self._request("POST", "/opportunities/", json=payload)
        return data.get("opportunity", data)

    async def move_stage(self, opp_id: str, stage_id: str) -> dict:
        """Move an opportunity to `stage_id` (mirrors cadence_day). Returns the
        updated opportunity dict."""
        payload = {"pipelineStageId": stage_id}
        data = await self._request("PUT", f"/opportunities/{opp_id}", json=payload)
        return data.get("opportunity", data)

    async def add_note(self, contact_id: str, body: str) -> dict:
        """Attach a note to a contact (used to log follow-ups / handoffs).
        Returns the created note dict."""
        payload = {"body": body}
        data = await self._request(
            "POST", f"/contacts/{contact_id}/notes", json=payload
        )
        return data.get("note", data)
