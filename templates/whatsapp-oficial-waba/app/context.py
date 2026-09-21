"""
TEMPLATE EXAMPLE — SessionContext wraps agent.session_state (persisted by Agno in SQLite).
Add, rename, or remove fields to match your agent's business domain.

Demonstrates:
  - Typed property/setter pairs over a plain dict (agent.session_state)
  - Automatic persistence: every mutation to self._s is saved by Agno after each agent run
  - Token restoration after service restarts (access_token survives SQLite round-trips)
  - Derived helper methods (is_member, checkout_url, card_payment_url) built from state

What to customize:
  - Keep session_id, from_number, access_token — these are infrastructure fields
  - Replace entity_id, language_id, client, env, persona_id, member_status and any
    domain-specific fields (activity_schedule_id, cost_center_id, etc.) with your domain's concepts
  - Remove or rename checkout_url / card_payment_url if your API uses a different checkout flow
"""
from __future__ import annotations

import base64
from typing import Any


class SessionContext:
    """
    Typed wrapper over Agno's agent.session_state dict.
    Passed to each tool builder at agent creation time.
    Tools read/write state here — Agno persists it to SQLite/Postgres automatically.
    """

    def __init__(self, state: dict[str, Any]) -> None:
        self._s = state  # Reference to agent.session_state — mutations persist

    # --- Identifiers ---
    @property
    def session_id(self) -> str:
        return self._s.get("session_id", "")

    def set_session_id(self, v: str) -> None:
        self._s["session_id"] = v

    @property
    def from_number(self) -> str:
        """WhatsApp sender number in E.164 without +, e.g. '5511999999999'."""
        return self._s.get("from_number", "")

    @property
    def access_token(self) -> str | None:
        return self._s.get("access_token")

    def set_access_token(self, v: str) -> None:
        self._s["access_token"] = v

    @property
    def entity_id(self) -> int:
        # TODO: adapt to your domain — this may be a tenant ID, org ID, etc.
        return self._s.get("entity_id", 1)

    @property
    def language_id(self) -> int:
        # TODO: adapt to your domain — remove if not needed
        return self._s.get("language_id", 1)

    @property
    def client(self) -> str:
        # TODO: adapt to your domain — this is the client/tenant slug used in checkout URLs
        from app.config import CLIENT_NAME
        return self._s.get("client") or CLIENT_NAME

    @property
    def env(self) -> str:
        # TODO: adapt to your domain — staging/production environment suffix for URLs
        from app.config import CLIENT_ENV
        return self._s.get("env") or CLIENT_ENV

    @property
    def person_id(self) -> int | None:
        return self._s.get("person_id")

    def set_person_id(self, v: int) -> None:
        self._s["person_id"] = v

    @property
    def first_name(self) -> str:
        return self._s.get("first_name", "")

    def set_first_name(self, v: str) -> None:
        self._s["first_name"] = v

    @property
    def person_hash_link(self) -> str | None:
        return self._s.get("person_hash_link")

    def set_person_hash_link(self, v: str) -> None:
        self._s["person_hash_link"] = v

    @property
    def persona_id(self) -> int | None:
        """
        TODO: adapt to your domain — persona_id encodes membership tier.
        Example: 1=non-member, 4=active member; other values=member with pending fees.
        Replace with your user type/tier/role concept.
        """
        return self._s.get("persona_id")

    def set_persona_id(self, v: int) -> None:
        self._s["persona_id"] = v

    @property
    def member_status(self) -> str:
        """
        TODO: adapt to your domain — human-readable membership status string.
        Example: 'Active Member', 'Pending Payment', 'Non-Member'.
        Replace with your user status concept.
        """
        return self._s.get("member_status", "")

    def set_member_status(self, v: str) -> None:
        self._s["member_status"] = v

    @property
    def is_member(self) -> bool:
        """
        TODO: adapt to your domain — derived bool from persona_id.
        Example: False only for non-member (persona_id=1). True for all member types.
        Replace with your membership check logic.
        """
        pid = self._s.get("persona_id")
        if pid is None:
            return True  # unknown → assume member to avoid blocking real members
        return pid != 1

    @property
    def activity_schedule_id(self) -> int | None:
        # TODO: adapt to your domain — this is the ID of the currently selected event/item
        return self._s.get("activity_schedule_id")

    def set_activity_schedule_id(self, v: int) -> None:
        self._s["activity_schedule_id"] = v

    @property
    def cost_center_id(self) -> int | None:
        # TODO: adapt to your domain — billing group / cost center associated with the selected item
        return self._s.get("cost_center_id")

    def set_cost_center_id(self, v: int) -> None:
        self._s["cost_center_id"] = v

    @property
    def payment_plan_id(self) -> int | None:
        # TODO: adapt to your domain — the selected pricing plan ID for enrollment
        return self._s.get("payment_plan_id")

    def set_payment_plan_id(self, v: int) -> None:
        self._s["payment_plan_id"] = v

    @property
    def account_receive_ids(self) -> list[int]:
        # TODO: adapt to your domain — IDs of pending charge/invoice records for payment processing
        return self._s.get("account_receive_ids", [])

    def set_account_receive_ids(self, ids: list[int]) -> None:
        self._s["account_receive_ids"] = ids

    @property
    def print_token(self) -> str | None:
        # TODO: adapt to your domain — token used to retrieve the bank slip PDF URL
        return self._s.get("print_token")

    def set_print_token(self, v: str) -> None:
        self._s["print_token"] = v

    @property
    def annuity_subscription_ids(self) -> list[int]:
        # TODO: adapt to your domain — subscription IDs of pending membership/annual fees
        # Remove if your domain does not have annual membership fees
        return self._s.get("annuity_subscription_ids", [])

    def set_annuity_subscription_ids(self, ids: list[int]) -> None:
        self._s["annuity_subscription_ids"] = ids

    @property
    def annuity_account_receive_ids(self) -> list[int]:
        """
        Charge IDs for pending membership fees — needed to include them in PIX/boleto payments.
        TODO: adapt to your domain — remove if your domain does not have annual membership fees.
        """
        return self._s.get("annuity_account_receive_ids", [])

    def set_annuity_account_receive_ids(self, ids: list[int]) -> None:
        self._s["annuity_account_receive_ids"] = ids

    @property
    def event_title(self) -> str:
        return self._s.get("event_title")

    def set_event_title(self, v: str) -> None:
        self._s["event_title"] = v

    @property
    def event_amount(self) -> float:
        return float(self._s.get("event_amount", 0) or 0)

    def set_event_amount(self, v: float) -> None:
        self._s["event_amount"] = v

    @property
    def pending_membership_details(self) -> list[dict]:
        return self._s.get("pending_membership_details", [])

    def set_pending_membership_details(self, items: list[dict]) -> None:
        self._s["pending_membership_details"] = items

    # --- Subscribed events (to exclude from listings) ---
    @property
    def subscribed_activity_ids(self) -> set[int]:
        return set(self._s.get("subscribed_activity_ids", []))

    def add_subscribed_activity_id(self, activity_schedule_id: int) -> None:
        ids = list(self.subscribed_activity_ids)
        if activity_schedule_id not in ids:
            ids.append(activity_schedule_id)
        self._s["subscribed_activity_ids"] = ids

    # --- Caches ---
    @property
    def available_events(self) -> list[dict] | None:
        return self._s.get("available_events")

    def set_available_events(self, events: list[dict]) -> None:
        self._s["available_events"] = events

    @property
    def available_categories(self) -> list[dict] | None:
        return self._s.get("available_categories")

    def set_available_categories(self, cats: list[dict]) -> None:
        self._s["available_categories"] = cats

    @property
    def available_payment_methods(self) -> list[dict] | None:
        return self._s.get("available_payment_methods")

    def set_available_payment_methods(self, methods: list[dict]) -> None:
        self._s["available_payment_methods"] = methods

    # --- Cart (valor final com desconto) ---
    @property
    def cart_total(self) -> float | None:
        return self._s.get("cart_total")

    @property
    def cart_total_with_discount(self) -> float | None:
        return self._s.get("cart_total_with_discount")

    @property
    def cart_discount(self) -> float:
        return float(self._s.get("cart_discount") or 0)

    def set_cart_amounts(self, total: float, total_with_discount: float, discount: float) -> None:
        self._s["cart_total"] = total
        self._s["cart_total_with_discount"] = total_with_discount
        self._s["cart_discount"] = discount

    # --- Web widgets (buffered HTML output for webchat) ---
    def add_widget(self, html: str) -> None:
        self._s.setdefault("pending_widgets", []).append(html)

    def flush_widgets(self) -> list[str]:
        widgets = self._s.get("pending_widgets", [])
        self._s["pending_widgets"] = []
        return widgets

    # --- Helpers ---
    def checkout_url(self, path: str = "/offer") -> str:
        """
        PATTERN: Build a checkout/portal URL from client slug + environment suffix.
        TODO: replace with your platform's URL construction logic.
        Example: env="" → "myclient.hub.yourdomain.com"
                 env=".stg" → "myclient.hub.stg.yourdomain.com"
        """
        # TODO: replace "hub" subdomain and "yourdomain.com" with your platform's URL pattern
        env_suffix = f".{self.env.strip('.')}" if self.env else ""
        return f"https://{self.client}.hub{env_suffix}.yourdomain.com{path}"

    def card_payment_url(self) -> str | None:
        """
        PATTERN: Build a pre-authenticated deep-link to card checkout using hashLink + base64-encoded charge IDs.
        TODO: replace this entire method with your platform's authenticated checkout URL scheme.
        TODO: hashLink, cartCheckout, and redirect encoding are domain-specific — adapt or remove.
        """
        ids = self.account_receive_ids
        hash_link = self.person_hash_link
        if not ids or not hash_link:
            return None
        items_raw = ",".join(str(i) for i in ids)
        items_b64 = base64.b64encode(items_raw.encode()).decode()
        redirect_raw = f"/cartCheckout?items={items_b64}"  # TODO: replace with your platform's checkout path
        redirect_b64 = base64.b64encode(redirect_raw.encode()).decode()
        return self.checkout_url(f"/offer/login?hash={hash_link}&redirect={redirect_b64}")  # TODO: replace with your auth redirect scheme
