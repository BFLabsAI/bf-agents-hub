"""
TEMPLATE EXAMPLE — Adapt endpoints, field names, and business logic to your client's API.

Demonstrates: checking, listing, and registering a user's professional category / tier
within a billing group (cost center), as a prerequisite to pricing and enrollment.

Patterns shown:
  - get_cost_center_person: guard check before expensive pricing call
  - list_categories: present numbered list for user selection
  - register_category: resolve by number or fuzzy name match; fetch fresh list before resolving
  - Always fetch fresh data from API rather than trusting session cache for registration
"""
import json

from agno.tools import tool

from app.context import SessionContext
from app.client_api import ClientAPI
from tools.events import _title_case


def build_category_tools(ctx: SessionContext, api: ClientAPI) -> list:

    @tool
    async def get_cost_center_person() -> str:
        """
        Check if this user already has a professional category registered for the current
        event's cost center. Uses context: cost_center_id (set by event_detail).
        link present → registered → call list_payment_plans.
        link empty → not_registered → call list_categories.
        """
        # PATTERN: Guard check — verify prerequisite (category registration) before calling pricing API.
        if not ctx.cost_center_id:
            return json.dumps({
                "status": "event_required",
                "message": "Selecione um evento antes (event_detail).",
            })
        try:
            data = await api.get_cost_center_person(ctx.cost_center_id)
            registered = data.get("_registered", False)
            return json.dumps({
                "status": "registered" if registered else "not_registered",
                "nextAction": "call_list_payment_plans" if registered else "call_list_categories",
                "nextActionHint": (
                    "Chame list_payment_plans AGORA." if registered
                    else (
                        "Chame list_categories AGORA e apresente a lista numerada ao cliente. "
                        "Não pergunte 'quer escolher?'; apenas liste."
                    )
                ),
            })
        except Exception as exc:
            return json.dumps({"status": "error", "message": str(exc)})

    @tool
    async def list_categories() -> str:
        """
        List professional categories available for the event's cost center.
        Only call if get_cost_center_person returned 'not_registered'.
        Present the numbered list to the user immediately — do not ask permission first.
        """
        # PATTERN: Fetch and present a selection list; cache result in session state for register_category.
        if not ctx.cost_center_id:
            return json.dumps({
                "status": "event_required",
                "message": "Selecione um evento antes (event_detail).",
            })
        try:
            cats = await api.list_categories(ctx.cost_center_id, ctx.activity_schedule_id)
            summarized = [
                {
                    "id": c.get("id"),
                    "description": _title_case(c.get("description") or c.get("name", "")),
                    "amount": c.get("amount"),
                }
                for c in cats
            ]
            ctx.set_available_categories(summarized)
            return json.dumps({
                "status": "success",
                "count": len(summarized),
                "categories": summarized,
            })
        except Exception as exc:
            return json.dumps({"status": "error", "message": str(exc)})

    @tool
    async def register_category(
        category_number: int | None = None,
        category_description: str | None = None,
    ) -> str:
        """
        Register the user in the chosen professional category.
        Provide either category_number (1-based position from list) or category_description for
        fuzzy matching against the last list returned.
        After success, immediately call list_payment_plans.
        """
        # PATTERN: Accept user selection by position number OR partial name (fuzzy match).
        # Always refresh the list from the API before resolving — never trust stale session cache.
        if not ctx.cost_center_id:
            return json.dumps({
                "status": "event_required",
                "message": "Selecione um evento antes.",
            })

        # Always fetch fresh categories — never rely on session cache
        resolved: dict | None = None
        cats: list = []
        try:
            fresh = await api.list_categories(ctx.cost_center_id, ctx.activity_schedule_id)
            cats = [
                {
                    "id": c.get("id"),
                    "description": c.get("description") or c.get("name", ""),
                    "amount": c.get("amount"),
                }
                for c in fresh
            ]
            ctx.set_available_categories(cats)
        except Exception:
            cats = ctx.available_categories or []  # fallback to cache only if API fails

        if category_number is not None and 1 <= category_number <= len(cats):
            resolved = cats[category_number - 1]
        elif category_description:
            needle = category_description.lower().strip()
            resolved = next(
                (c for c in cats if needle in (c.get("description") or "").lower()),
                None,
            )

        if not resolved:
            return json.dumps({
                "status": "not_found",
                "message": "Não identifiquei essa categoria. Informe o número ou o nome exato.",
            })

        try:
            await api.register_category(
                cost_center_id=ctx.cost_center_id,
                cost_center_category_professional_id=int(resolved["id"]),
            )
            return json.dumps({
                "status": "success",
                "category": resolved,
                "message": "Categoria cadastrada com sucesso.",
                "nextAction": "call_list_payment_plans",
                "nextActionHint": "Chame list_payment_plans AGORA e apresente os valores ao cliente.",
            })
        except Exception as exc:
            return json.dumps({"status": "error", "message": str(exc)})

    return [get_cost_center_person, list_categories, register_category]
