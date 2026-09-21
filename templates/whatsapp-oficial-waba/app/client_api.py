"""
TEMPLATE EXAMPLE — This is a complete example of an async HTTP client for a REST API.
Replace endpoints, authentication headers, and response field names with your API's.
All methods show patterns: auth by phone, auth by document, listing with filters, detail, create, update.

Demonstrates:
  - Bearer-token auth with automatic restore from persisted session state
  - Retry transport for transient keep-alive errors on idempotent requests
  - Request/response hooks for full logging + curl command generation
  - Domain error handling (422 → structured error vs. raise)
  - Response envelope unwrapping ({"data": ...})

What to customize:
  - Replace every endpoint path (e.g. /api/auth/persons/automation) with your API's paths
  - Replace field names in request bodies and response parsing with your API's schema
  - Replace auth flow (login_by_phone / login_by_document) with your auth mechanism
  - See each method for inline TODO comments on domain-specific fields
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import time
from typing import TYPE_CHECKING

import httpx

# TODO: rename these imports to match your config.py variable names
from app.config import CLIENT_API_BASE_URL, CLIENT_SSL_VERIFY

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.context import SessionContext


class _RetryingTransport(httpx.AsyncBaseTransport):
    """
    Wraps an inner transport and retries idempotent requests when the external
    API drops a stale keep-alive connection mid-request (RemoteProtocolError /
    ReadError before headers arrive). POST/PUT/DELETE are NEVER retried — they
    are not idempotent and could cause duplicate side effects.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport, max_retries: int = 2) -> None:
        self._inner = inner
        self._max_retries = max_retries

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        retryable = request.method in ("GET", "HEAD", "OPTIONS")
        attempts = self._max_retries + 1 if retryable else 1
        last_exc: BaseException | None = None
        for attempt in range(attempts):
            try:
                return await self._inner.handle_async_request(request)
            except (httpx.RemoteProtocolError, httpx.ReadError) as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    _log.warning(
                        "transient error on %s %s — retry %d/%d (%s)",
                        request.method, request.url, attempt + 1, self._max_retries, exc,
                    )
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("unreachable")

    async def aclose(self) -> None:
        await self._inner.aclose()


class ClientAPI:
    """
    Async HTTP client for your external REST API.

    PATTERN: Bearer-token auth with two-step login:
      1. login_by_phone(phone)     → POST /api/auth/persons/automation
         If 404 → ask user for document (CPF, email, etc.)
      2. login_by_document(doc)    → POST /api/auth/persons/login-document
      3. get_me()                  → GET  /api/auth/persons/me  (userId, name, etc.)
    All other endpoints require the Bearer token set by set_access_token().

    Pass ctx so that after a service restart the token is auto-restored from
    persisted session state without requiring a new identify_by_phone call.

    TODO: Replace the auth flow above with your API's authentication mechanism.
    """

    def __init__(
        self,
        ctx: SessionContext | None = None,
        base_url: str | None = None,
    ) -> None:
        self._access_token: str | None = None
        self._ctx = ctx
        inner_transport = httpx.AsyncHTTPTransport(verify=CLIENT_SSL_VERIFY, retries=1)
        self._client = httpx.AsyncClient(
            base_url=base_url or CLIENT_API_BASE_URL,  # TODO: replace with your API's base URL
            transport=_RetryingTransport(inner_transport, max_retries=2),
            timeout=30.0,
            headers={"Accept": "application/json"},
            event_hooks={
                "request": [self._on_request],
                "response": [self._on_response],
            },
        )

    async def _on_request(self, request: httpx.Request) -> None:
        request.extensions["_t0"] = time.monotonic()
        from app.log_store import log_store

        # Parse request body
        req_body = None
        body_raw: str | None = None
        if request.content:
            body_raw = request.content.decode("utf-8", errors="replace")
            try:
                req_body = _json.loads(body_raw)
            except Exception:
                req_body = body_raw

        # Build curl command
        _SKIP_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}
        curl_lines = [f'curl -X {request.method} "{request.url}"']
        for k, v in request.headers.items():
            if k.lower() in _SKIP_HEADERS:
                continue
            curl_lines.append(f'  -H "{k}: {v}"')
        if body_raw:
            escaped = body_raw.replace("'", "'\\''")
            curl_lines.append(f"  -d '{escaped}'")
        curl_cmd = " \\\n".join(curl_lines)

        qs = str(request.url.params) if request.url.params else None

        log_store.log_event("api_request", {
            "method": request.method,
            "url": str(request.url),
            "path": request.url.path,
            "qs": qs,
            "body": req_body,
            "curl": curl_cmd,
        })

    async def _on_response(self, response: httpx.Response) -> None:
        from app.log_store import log_store
        t0 = response.request.extensions.get("_t0")
        duration_ms = int((time.monotonic() - t0) * 1000) if t0 is not None else 0

        # Read body so it's available for caller too (httpx caches it)
        resp_body = None
        try:
            await response.aread()
            text = response.text
            if text:
                try:
                    resp_body = _json.loads(text)
                except Exception:
                    resp_body = text
        except Exception:
            pass

        log_store.log_event("api_response", {
            "method": response.request.method,
            "url": str(response.request.url),
            "path": response.request.url.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
            "body": resp_body,
        })

    def set_access_token(self, token: str) -> None:
        self._access_token = token

    def _auth_headers(self) -> dict[str, str]:
        token = self._access_token
        # Auto-restore from persisted session state after a service restart
        if not token and self._ctx:
            token = self._ctx.access_token
            if token:
                self._access_token = token  # cache so we only do this once
        if not token:
            raise RuntimeError("Not authenticated — call login_by_phone or login_by_document first")
        return {"Authorization": f"Bearer {token}"}

    # ── Auth ──────────────────────────────────────────────────────────────────

    async def login_by_phone(self, phone: str) -> dict:
        """
        PATTERN: Look up a user by phone number and return an access token.
        POST /api/auth/persons/automation
        Returns { accessToken, tokenType, expiresIn } on success.
        Raises httpx.HTTPStatusError with status 404 if phone not found.

        TODO: replace endpoint and request body with your API's auth-by-phone mechanism.
        """
        resp = await self._client.post(
            "/api/auth/persons/automation",  # TODO: replace with your API's endpoint
            json={"phone": phone},  # TODO: replace with your API's request field names
        )
        resp.raise_for_status()
        return resp.json()

    async def login_by_document(self, document: str) -> dict:
        """
        PATTERN: Fallback auth when phone lookup fails — identify by document/CPF/email.
        POST /api/auth/persons/login-document
        document: CPF, e-mail, or phone.
        Returns { accessToken, tokenType, expiresIn } on success.

        TODO: replace endpoint and field name with your API's document auth mechanism.
        """
        resp = await self._client.post(
            "/api/auth/persons/login-document",  # TODO: replace with your API's endpoint
            json={"document": document},  # TODO: replace with your API's request field names
        )
        resp.raise_for_status()
        return resp.json()

    async def get_me(self) -> dict:
        """
        PATTERN: Fetch the authenticated user's profile after login.
        GET /api/auth/persons/me
        Returns user data: personId, name, email, hashLink, etc.
        Requires Bearer token.

        TODO: replace endpoint and expected response field names with your API's user profile endpoint.
        """
        resp = await self._client.get(
            "/api/auth/persons/me",  # TODO: replace with your API's profile endpoint
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    # ── Events ────────────────────────────────────────────────────────────────

    async def list_events(self) -> dict:
        """
        PATTERN: Public catalog endpoint — does NOT require authentication.
        GET /api/store/offers — PUBLIC endpoint.
        Returns the tenant's event/product catalog. MUST be called WITHOUT the
        Authorization header; any Bearer (valid or not) triggers 403.
        Filtering by what the user can actually subscribe to is done downstream
        via event_detail + list_payment_plans (which do require auth).

        TODO: replace endpoint with your API's public catalog endpoint.
        TODO: replace activityScheduleId, typeActivity field names with your API's schema.
        """
        resp = await self._client.get("/api/store/offers")  # TODO: replace with your API's endpoint
        resp.raise_for_status()
        return resp.json()

    async def event_detail(self, activity_schedule_id: int) -> dict:
        """
        PATTERN: Fetch full details for a single catalog item (requires auth).
        TODO: replace activityScheduleId and endpoint path with your API's item detail endpoint.
        TODO: replace with your API's field names.
        """
        resp = await self._client.get(
            f"/api/subscription/activities-schedule/{activity_schedule_id}/detailing",  # TODO: replace with your API's endpoint
            headers=self._auth_headers(),
        )
        resp.raise_for_status()
        return resp.json()

    # ── Categories ────────────────────────────────────────────────────────────
    # PATTERN: Check, list, and register a user's professional/membership category
    # within a cost center (billing group). Adapt if your API uses a different
    # concept for user segmentation (tiers, roles, memberships, etc.).

    async def get_cost_center_person(self, cost_center_id: int) -> dict:
        """
        PATTERN: Check whether the user already has a category registered in a billing group.
        GET /api/register/persons/cost-center/{id}
        200 with non-empty link → person has a category registered
        200 with link: [] → NOT registered (API quirk — not a 500)
        500 → person NOT registered in this cost center (expected, not a bug)

        TODO: replace costCenterId and endpoint with your API's concept of segmentation/category.
        TODO: replace field names (link, _registered) with your API's response schema.
        """
        resp = await self._client.get(
            f"/api/register/persons/cost-center/{cost_center_id}",  # TODO: replace with your API's endpoint
            headers=self._auth_headers(),
        )
        if resp.status_code == 500:
            return {"_registered": False}
        resp.raise_for_status()
        body = resp.json()
        data = body.get("data", body) if isinstance(body, dict) else body
        link = data.get("link") if isinstance(data, dict) else None  # TODO: replace "link" with your API's field name
        registered = bool(link)
        return {**body, "_registered": registered}

    async def list_categories(self, cost_center_id: int, activity_schedule_id: int | None = None) -> list:
        """
        PATTERN: List available categories (professional roles, tiers, etc.) for a billing group.
        TODO: replace costCenterId, activityScheduleId, and endpoint with your API's schema.
        TODO: replace field names with your API's category/tier/role response fields.
        """
        params: dict = {}
        if activity_schedule_id:
            params["activityScheduleId"] = activity_schedule_id  # TODO: replace with your API's field name
        resp = await self._client.get(
            f"/api/register/center-costs/{cost_center_id}/categories",  # TODO: replace with your API's endpoint
            headers=self._auth_headers(),
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("data", [])

    async def register_category(self, cost_center_id: int, cost_center_category_professional_id: int) -> dict:
        """
        PATTERN: Register the user under a specific category in a billing group.
        POST /api/register/persons/categories
        Body: {costCenterId, costCenterCategoryProfessionalId}
        Person identified via Bearer JWT — no personId needed.

        TODO: replace costCenterId, costCenterCategoryProfessionalId, and endpoint
              with your API's category assignment mechanism.
        """
        resp = await self._client.post(
            "/api/register/persons/categories",  # TODO: replace with your API's endpoint
            headers=self._auth_headers(),
            json={
                "costCenterId": cost_center_id,                                      # TODO: replace with your API's field name
                "costCenterCategoryProfessionalId": cost_center_category_professional_id,  # TODO: replace with your API's field name
            },
        )
        resp.raise_for_status()
        return resp.json()

    # ── Subscriptions ─────────────────────────────────────────────────────────
    # PATTERN: Full subscription lifecycle — check, list plans, create, cancel, list all.
    # Adapt field names (activityScheduleId, paymentPlanId, accountReceive, etc.)
    # to match your API's order/enrollment/registration concepts.

    async def check_existing_subscription(self, activity_schedule_id: int) -> list | None:
        """
        PATTERN: Check if the user is already enrolled/registered for a specific item.
        GET /api/subscription/persons/subscribe?activityScheduleId=X
        200 with data → subscription exists; returns list of subscription objects
        200 with null/empty data or 422/500 → not subscribed → returns None
        Person identified via Bearer JWT — no personId param needed.

        TODO: replace activityScheduleId and endpoint with your API's enrollment check.
        TODO: replace response field names (accountReceive, id) with your API's schema.
        """
        resp = await self._client.get(
            "/api/subscription/persons/subscribe",  # TODO: replace with your API's endpoint
            headers=self._auth_headers(),
            params={"activityScheduleId": activity_schedule_id},  # TODO: replace with your API's query param
        )
        if resp.status_code in (422, 500):
            return None
        resp.raise_for_status()
        body = resp.json()
        # API wraps in {"data": ...} — unwrap
        data = body.get("data", body) if isinstance(body, dict) else body
        if not data:
            return None
        # Normalise to list
        return data if isinstance(data, list) else [data]

    async def list_payment_plans(self, activity_schedule_id: int) -> dict:
        """
        PATTERN: Fetch the pricing plan(s) applicable to this user for a specific item.
        GET /api/subscription/activities-schedule/{id}/persons-payment-plans
        200 → array of plans
        422 → domain error (e.g. no category registered yet) — returns {_domain_error: True, message: "..."}

        TODO: replace activityScheduleId and endpoint with your API's pricing/plan endpoint.
        TODO: replace response field names (amount, associatedAmount, pendingMembershipFee, etc.)
              with your API's pricing schema.
        """
        resp = await self._client.get(
            f"/api/subscription/activities-schedule/{activity_schedule_id}/persons-payment-plans",  # TODO: replace with your API's endpoint
            headers=self._auth_headers(),
        )
        if resp.status_code == 422:
            body = resp.json() if resp.content else {}
            msg = body.get("message") or (body.get("data") or {}).get("message", "Erro de validação")
            return {"_domain_error": True, "message": msg}
        resp.raise_for_status()
        body = resp.json()
        # Unwrap {"data": ...} envelope
        data = body.get("data", body) if isinstance(body, dict) and "data" in body else body
        # Normalise to list
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return []

    async def create_subscription(
        self,
        payment_plan_id: int,
        annuity_ids: list[int] | None = None,
    ) -> list:
        """
        PATTERN: Create an enrollment/subscription for the user using a pricing plan.
        POST /api/subscription/persons/subscribe
        Field is 'paymentPlanId' (singular, array) — 'paymentPlanIds' returns 500.
        Returns list of subscription objects each with accountReceive[].

        TODO: replace paymentPlanId, originInscription, registrationAnnuityIds, and endpoint
              with your API's enrollment creation request schema.
        TODO: replace accountReceive response field name with your API's payment reference field.
        TODO: annuity_ids is domain-specific — remove or replace with your membership/fee concept.
        """
        if not payment_plan_id:
            raise ValueError("payment_plan_id is required to create a subscription.")
        body: dict = {
            "paymentPlanId": [payment_plan_id],          # TODO: replace with your API's field name
            "originInscription": 5 if annuity_ids else 2,  # TODO: replace with your API's origin/source field
        }
        if annuity_ids:
            body["registrationAnnuityIds"] = annuity_ids  # TODO: replace with your API's field name
        resp = await self._client.post(
            "/api/subscription/persons/subscribe",  # TODO: replace with your API's endpoint
            headers=self._auth_headers(),
            json=body,
        )
        resp.raise_for_status()
        raw = resp.json()
        # Unwrap {"data": [...]} envelope
        data = raw.get("data", raw) if isinstance(raw, dict) else raw
        return data if isinstance(data, list) else [data]

    async def cancel_subscription(self, subscription_ids: list[int]) -> dict:
        """
        PATTERN: Cancel one or more existing subscriptions.
        POST /api/subscription/persons/unsubscribe
        subscriptionIds: list of subscription IDs (the 'id' field from my_subscriptions items).
        Returns {"message": "..."} on 200.
        422 → business error (cannot cancel).

        TODO: replace subscriptionIds and endpoint with your API's cancellation schema.
        """
        resp = await self._client.post(
            "/api/subscription/persons/unsubscribe",  # TODO: replace with your API's endpoint
            headers=self._auth_headers(),
            json={"subscriptionIds": subscription_ids},  # TODO: replace with your API's field name
        )
        if resp.status_code == 422:
            body = resp.json() if resp.content else {}
            msg = body.get("message", "Não é possível cancelar a inscrição.")
            return {"_domain_error": True, "message": msg}
        resp.raise_for_status()
        body = resp.json() if resp.content else {}
        return body

    async def my_subscriptions(self) -> list:
        """
        PATTERN: List all subscriptions/enrollments for the current user across all statuses.
        GET /api/subscription/persons/my-subscription
        Response: {"tabs": {"descriptions": [...], "data": {"0": [...], "1": [...], "2": [...], "3": [...]}}}
        key "0" = all subscriptions (flat list, each item has accountReceiveId singular).

        TODO: replace endpoint with your API's user subscription list endpoint.
        TODO: replace the "tabs" response structure with your API's list schema.
        TODO: replace accountReceiveId with your API's payment reference field name.
        """
        resp = await self._client.get(
            "/api/subscription/persons/my-subscription",  # TODO: replace with your API's endpoint
            headers=self._auth_headers(),
            params={"origin": "agent"},  # TODO: replace or remove this query param
        )
        resp.raise_for_status()
        body = resp.json()
        # Unwrap outer {"data": {"tabs": {...}}} envelope
        if isinstance(body, dict) and "data" in body and not isinstance(body["data"], list):
            body = body["data"]
        if isinstance(body, dict) and "tabs" in body:
            tabs_data = body["tabs"].get("data", {})
            if isinstance(tabs_data, dict):
                # Flatten all non-empty tab lists; dedup by accountReceiveId
                seen: set[int] = set()
                result: list[dict] = []
                for items in tabs_data.values():
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        ar_id = item.get("accountReceiveId")
                        key = ar_id if ar_id else id(item)
                        if key not in seen:
                            seen.add(key)
                            result.append(item)
                if result:
                    _log.info("my_subscriptions | first_item_keys=%s", list(result[0].keys()))
                else:
                    _log.warning("my_subscriptions | empty result | tabs_keys=%s tabs_data=%s",
                                 list(tabs_data.keys()), str(tabs_data)[:500])
                return result
            if isinstance(tabs_data, list):
                # tabs_data is a list of tab arrays — flatten all of them
                result: list[dict] = []
                seen: set[int] = set()
                for tab in tabs_data:
                    if not isinstance(tab, list):
                        continue
                    for item in tab:
                        if not isinstance(item, dict):
                            continue
                        ar_id = item.get("accountReceiveId")
                        key = ar_id if ar_id else id(item)
                        if key not in seen:
                            seen.add(key)
                            result.append(item)
                if not result:
                    _log.warning("my_subscriptions | list format, all tabs empty | len=%d", len(tabs_data))
                return result
        _log.warning("my_subscriptions | unexpected structure | body_keys=%s body=%s",
                     list(body.keys()) if isinstance(body, dict) else type(body).__name__,
                     str(body)[:500])
        if isinstance(body, list):
            return body
        return body.get("data", []) if isinstance(body, dict) else []

    async def my_annuities(self) -> list[dict]:
        """
        PATTERN: Fetch only the pending membership/annual-fee items (tab "1") for the user.
        Used by pay_all_annuities to enforce paying all fees together as a group.

        GET /api/subscription/persons/my-subscription (same endpoint as my_subscriptions)
        Returns ONLY the items from tab key "1" (membership fees) — pending fees.

        TODO: this method is domain-specific to annual membership fees.
              Remove or replace if your domain does not have this concept.
        TODO: replace endpoint and tab key "1" with your API's fee/membership list.
        """
        resp = await self._client.get(
            "/api/subscription/persons/my-subscription",  # TODO: replace with your API's endpoint
            headers=self._auth_headers(),
            params={"origin": "agent"},  # TODO: replace or remove
        )
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict) and "data" in body and not isinstance(body["data"], list):
            body = body["data"]
        if isinstance(body, dict) and "tabs" in body:
            tabs_data = body["tabs"].get("data", {})
            raw = tabs_data.get("1") or []
            if isinstance(raw, list):
                _log.info("my_annuities | tab_1_count=%d", len(raw))
                return [item for item in raw if isinstance(item, dict)]
        _log.warning("my_annuities | tab_1 not found | body_keys=%s",
                     list(body.keys()) if isinstance(body, dict) else type(body).__name__)
        return []

    # ── Payment ───────────────────────────────────────────────────────────────
    # PATTERN: Payment flow — list available methods, process PIX, process bank slip (boleto),
    # retrieve PDF URL, and fetch cart totals with discount.
    # Adapt field names and endpoints to your payment gateway integration.

    async def list_payment_methods(
        self, account_receive_ids: list[int], cost_center_id: int
    ) -> list:
        """
        PATTERN: Discover which payment methods are enabled for the user's pending charges.
        GET /api/subscription/payments/methods
        Required: costCenterId (int) + accountReceiveIds[] (array notation).

        TODO: replace costCenterId, accountReceiveIds[], and endpoint with your API's schema.
        TODO: replace response field names (type: "pix"/"bankPayment"/"card", gatewayId) with yours.
        """
        params = [("costCenterId", cost_center_id)]  # TODO: replace with your API's billing group param
        for aid in account_receive_ids:
            params.append(("accountReceiveIds[]", aid))  # TODO: replace with your API's charge ID param
        resp = await self._client.get(
            "/api/subscription/payments/methods",  # TODO: replace with your API's endpoint
            headers=self._auth_headers(),
            params=params,
        )
        resp.raise_for_status()
        raw = resp.json()
        data = raw.get("data", raw) if isinstance(raw, dict) and "data" in raw else raw
        return data if isinstance(data, list) else [data]

    @staticmethod
    def _extract_payment_error(resp) -> str:
        """Extract a clean error message from a 400 payment response."""
        try:
            body = resp.json()
            data = body.get("data", body) if isinstance(body, dict) else {}
            msg = data.get("message") if isinstance(data, dict) else None
            if msg:
                # Strip noisy HTTP client trace — keep only the human-readable part
                # e.g. "Erro ao gerar boleto: Client error: `PUT ...` resulted in ..."
                # → return just "Erro ao gerar boleto: <vindi message>"
                if "errors" in msg:
                    import re
                    # Extract first Vindi error message field
                    match = re.search(r'"message"\s*:\s*"([^"]+)"', msg)
                    if match:
                        human = match.group(1)
                        # Keep any prefix before "Client error:"
                        prefix = msg.split("Client error:")[0].strip().rstrip(":").strip()
                        return f"{prefix}: {human}" if prefix else human
                return msg
        except Exception:
            pass
        return resp.text[:200]

    async def process_pix_payment(
        self, account_receive_ids: list[int], gateway_id: int, expires_in: int = 3600
    ) -> dict:
        """
        PATTERN: Generate a PIX instant payment QR code + copy-paste code.
        POST /api/payments/pix/process
        expiresIn = seconds until expiry (not an ISO string).

        TODO: replace accountReceiveIds, gatewayId, expiresIn, and endpoint with your gateway's schema.
        TODO: replace response field names (pixCopyPaste, qrCode) with your gateway's response.
        """
        resp = await self._client.post(
            "/api/payments/pix/process",  # TODO: replace with your payment gateway's PIX endpoint
            headers=self._auth_headers(),
            json={
                "accountReceiveIds": account_receive_ids,  # TODO: replace with your API's charge ID field
                "gatewayId": gateway_id,                   # TODO: replace with your API's gateway field
                "expiresIn": expires_in,                   # TODO: replace with your API's expiry field
            },
        )
        if resp.status_code == 400:
            raise ValueError(self._extract_payment_error(resp))
        resp.raise_for_status()
        raw = resp.json()
        return raw.get("data", raw) if isinstance(raw, dict) and "data" in raw else raw

    async def process_bank_payment(
        self, account_receive_ids: list[int], gateway_id: int
    ) -> dict:
        """
        PATTERN: Generate a bank slip (boleto bancário) for the pending charges.
        Returns payment data including a typeable barcode and a print URL.

        TODO: replace accountReceiveIds, gatewayId, typeOrigin, and endpoint with your gateway's schema.
        TODO: replace gatewayResponse/charges/last_transaction path with your gateway's response structure.
        TODO: typeOrigin=13 is domain-specific — remove or replace with your API's origin/source concept.
        """
        resp = await self._client.post(
            "/api/payments/bank/process",  # TODO: replace with your payment gateway's bank slip endpoint
            headers=self._auth_headers(),
            json={
                "accountReceiveIds": account_receive_ids,  # TODO: replace with your API's charge ID field
                "gatewayId": gateway_id,                   # TODO: replace with your API's gateway field
                "typeOrigin": 13,                          # TODO: replace with your API's origin/source field
            },
        )
        if resp.status_code == 400:
            raise ValueError(self._extract_payment_error(resp))
        resp.raise_for_status()
        raw = resp.json()
        data = raw.get("data", raw) if isinstance(raw, dict) and "data" in raw else raw

        # Check for Vindi-level rejection inside a 201 response
        charges = (data.get("gatewayResponse") or {}).get("charges") or []
        if charges:
            last_tx = (charges[0].get("last_transaction") or {})
            if last_tx.get("status") == "rejected":
                gateway_msg = last_tx.get("gateway_message") or "Boleto rejeitado pelo gateway."
                raise ValueError(gateway_msg)
            # Extract typeable barcode and print URL when transaction succeeded
            grf = last_tx.get("gateway_response_fields") or {}
            data["_typeable_barcode"] = grf.get("typeable_barcode")
            data["_charges_print_url"] = charges[0].get("print_url")

        return data

    async def print_bank_payment(self, print_token: str) -> dict:
        """
        PATTERN: Retrieve the PDF URL for a generated bank slip using a token.
        TODO: replace endpoint and token param with your gateway's print/PDF endpoint.
        TODO: replace response field names (url, pdfUrl) with your gateway's response.
        """
        resp = await self._client.get(
            "/api/payments/bank/print",  # TODO: replace with your payment gateway's print endpoint
            headers=self._auth_headers(),
            params={"token": print_token},  # TODO: replace with your gateway's token param
        )
        resp.raise_for_status()
        return resp.json()

    async def get_subscription_cart(
        self,
        account_receive_ids: list[int],
        origin_inscription: int = 2,
    ) -> dict:
        """
        PATTERN: Fetch the cart/checkout summary with discounts applied for pending charges.
        GET /api/subscription/persons/subscribe/cart
        Returns {items: [...], summary: {total, totalWithDiscount, discount}}

        TODO: replace accountReceiveIds, originInscription, and endpoint with your API's cart/checkout schema.
        TODO: replace summary field names (total, totalWithDiscount, discount) with your API's response.
        TODO: originInscription is domain-specific — remove or replace with your API's origin concept.
        """
        params: list[tuple] = [
            ("changePaymentMethod", "false"),              # TODO: adapt or remove this param
            ("originInscription", origin_inscription),     # TODO: replace with your API's origin/source param
        ]
        for ar_id in account_receive_ids:
            params.append(("accountReceiveIds[]", ar_id))  # TODO: replace with your API's charge ID param
        resp = await self._client.get(
            "/api/subscription/persons/subscribe/cart",  # TODO: replace with your API's cart/checkout endpoint
            headers=self._auth_headers(),
            params=params,
        )
        resp.raise_for_status()
        raw = resp.json()
        data = raw.get("data", raw) if isinstance(raw, dict) and "data" in raw else raw
        if isinstance(data, dict) and "summary" in data:
            return {"items": data.get("list", []), "summary": data["summary"]}
        return {"items": [], "summary": data}

    async def aclose(self) -> None:
        await self._client.aclose()
