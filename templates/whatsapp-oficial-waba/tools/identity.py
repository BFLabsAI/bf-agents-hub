"""
TEMPLATE EXAMPLE — Adapt endpoints, field names, and business logic to your client's API.

Demonstrates: user authentication by phone number (primary) and by document/CPF (fallback)
using an external REST API, with session state persistence via SessionContext.

Patterns shown:
  - Two-step auth: try phone first, fall back to document on 404
  - Phone normalization for Brazilian E.164 format (adapt or remove for other regions)
  - Token + profile extraction stored in SessionContext for persistence across restarts
  - _extract_and_store: generic helper to pull user fields from any auth response shape
"""
import json
import logging

import httpx
from agno.tools import tool

import app.user_log as ulog
from app.context import SessionContext
from app.client_api import ClientAPI

logger = logging.getLogger(__name__)


def _normalize_phone(number: str) -> str:
    """
    WhatsApp sends E.164 without '+'.
    iTarget stores Brazilian mobile numbers WITH the 9th-digit prefix:
      55 + DDD(2) + 9 + local(8) = 13 digits  e.g. '5585997481913'

    WhatsApp sometimes sends 12-digit numbers (without the 9):
      55 + DDD(2) + local(8)  e.g. '558597481913'
    In that case we insert the '9' after the DDD.

    13-digit WhatsApp numbers already have the 9 and are returned as-is.
    """
    n = number.strip().lstrip("+")
    if n.startswith("55"):
        if len(n) == 12:
            # 55 + DDD(2) + 8-digit local → insert 9 after DDD
            ddd = n[2:4]
            local = n[4:]
            return f"55{ddd}9{local}"
        if len(n) == 13:
            # Already in correct format
            return n
    return n


def _extract_and_store(resp_data: dict, ctx: SessionContext, api: ClientAPI) -> dict:
    """
    Extract person info + token from auth response, store in ctx (persisted to DB).
    Works for login-document (full data inline) and login-phone (needs /me).
    """
    # PATTERN: Extract token + user profile from auth response and persist in session state.
    # TODO: replace field names (accessToken, personId, hashLinkPassword, etc.) with your API's response schema.
    access_token = resp_data.get("accessToken") or resp_data.get("access_token", "")
    api.set_access_token(access_token)
    ctx.set_access_token(access_token)  # persist so it survives restarts

    person_id = resp_data.get("personId") or resp_data.get("person_id")  # TODO: replace with your API's user ID field
    name = resp_data.get("name", "")
    email = resp_data.get("email", "")
    hash_link = (
        resp_data.get("hashLinkPassword")
        or resp_data.get("hashLink")
        or resp_data.get("hash_link", "")
    )  # TODO: replace with your API's pre-authenticated link field, or remove if not applicable

    if person_id:
        ctx.set_person_id(int(person_id))
    if hash_link:
        ctx.set_person_hash_link(hash_link)

    first_name = name.split()[0].capitalize() if name else ""
    if first_name:
        ctx.set_first_name(first_name)

    # Membership status — present in /me response, may be absent in login response
    # TODO: replace persona/association fields with your API's membership/tier/role concept.
    # TODO: remove these blocks if your domain has no membership status.
    persona_data = resp_data.get("persona") or {}
    persona_id = persona_data.get("id")
    assoc = resp_data.get("association") or {}
    member_status_desc = assoc.get("financialStatusDescription") or ""
    if persona_id is not None:
        ctx.set_persona_id(int(persona_id))
    if member_status_desc:
        ctx.set_member_status(member_status_desc)

    photo_url = resp_data.get("photoUrl") or None

    return {"personId": person_id, "name": name, "firstName": first_name, "email": email, "photoUrl": photo_url}


async def _restore_or_get_me(api: ClientAPI, ctx: SessionContext) -> dict:
    """Call /auth/persons/me when the auth response didn't include person data."""
    me = await api.get_me()
    data = me.get("data", me) if isinstance(me, dict) else me
    person = _extract_and_store(data, ctx, api)
    ulog.upsert(
        person_id=ctx.person_id,
        name=person.get("name", ""),
        first_name=ctx.first_name,
        phone=ctx.from_number,
        session_id=ctx.session_id,
        financial_status=ctx.member_status,
        persona_id=ctx.persona_id,
        photo_url=person.get("photoUrl"),
        full_payload=data if isinstance(data, dict) else None,
        auth_user=True,
    )
    return person


def build_identity_tools(ctx: SessionContext, api: ClientAPI) -> list:

    @tool
    async def identify_by_phone() -> str:
        """
        Identify the user automatically using their WhatsApp phone number.
        ALWAYS call this as the very first action in every conversation.
        If the user is not found (status 'not_found'), ask them for their CPF and call identify_by_document.
        """
        # PATTERN: Skip re-auth if session already has a token — restore it to the API client.
        # Already identified in this session — restore token and skip re-auth
        if ctx.person_id and ctx.access_token:
            api.set_access_token(ctx.access_token)
            logger.info("identify_by_phone | already authenticated person_id=%s", ctx.person_id)
            return json.dumps({
                "status": "already_identified",
                "person": {"personId": ctx.person_id, "firstName": ""},
                "nextAction": "proceed_with_request",
            })

        raw = ctx.from_number
        phone = _normalize_phone(raw)
        logger.info("identify_by_phone | raw=%s normalized=%s", raw, phone)

        if not phone:
            return json.dumps({
                "status": "not_found",
                "message": "Número de telefone não disponível.",
                "nextAction": "ask_cpf_then_call_identify_by_document",
            })
        try:
            resp = await api.login_by_phone(phone)
            data = resp.get("data", resp) if isinstance(resp, dict) else resp

            if data.get("personId"):
                person = _extract_and_store(data, ctx, api)
                # If login response didn't include hashLink, fetch it from /me
                if not ctx.person_hash_link:
                    person = await _restore_or_get_me(api, ctx)
            else:
                api.set_access_token(data.get("accessToken") or data.get("access_token", ""))
                person = await _restore_or_get_me(api, ctx)

            logger.info("identify_by_phone | success person_id=%s", ctx.person_id)
            return json.dumps({
                "status": "success",
                "person": person,
                "nameRule": "Use ONLY firstName in all subsequent messages",
                "nextAction": "greet_user_once_then_ask_what_they_need",
            })
        except httpx.HTTPStatusError as e:
            logger.warning("identify_by_phone | HTTP %s", e.response.status_code)
            if e.response.status_code == 404:
                return json.dumps({
                    "status": "not_found",
                    "message": "Telefone não cadastrado na plataforma.",
                    "nextAction": "ask_cpf_then_call_identify_by_document",
                })
            return json.dumps({"status": "error", "message": str(e)})
        except Exception as exc:
            logger.error("identify_by_phone | %s", exc)
            return json.dumps({"status": "error", "message": str(exc)})

    @tool
    async def identify_by_document(document: str) -> str:
        """
        Identify the user by CPF, e-mail, or phone when identify_by_phone returned 'not_found'.
        Call this after the user provides their CPF (or other document).
        """
        clean_doc = document.replace(".", "").replace("-", "").replace(" ", "")
        logger.info("identify_by_document | doc=%s", clean_doc)
        try:
            resp = await api.login_by_document(clean_doc)
            data = resp.get("data", resp) if isinstance(resp, dict) else resp
            person = _extract_and_store(data, ctx, api)
            ulog.upsert(
                person_id=ctx.person_id,
                name=person.get("name", ""),
                first_name=ctx.first_name,
                phone=ctx.from_number,
                session_id=ctx.session_id,
                financial_status=ctx.member_status,
                persona_id=ctx.persona_id,
                photo_url=person.get("photoUrl"),
                full_payload=data if isinstance(data, dict) else None,
                auth_user=True,
            )
            logger.info("identify_by_document | success person_id=%s", ctx.person_id)
            return json.dumps({
                "status": "success",
                "person": person,
                "nameRule": "Use ONLY firstName in all subsequent messages",
                "nextAction": "greet_user_once_then_ask_what_they_need",
            })
        except httpx.HTTPStatusError as e:
            logger.warning("identify_by_document | HTTP %s", e.response.status_code)
            if e.response.status_code == 404:
                return json.dumps({
                    "status": "not_found",
                    "message": "Documento não encontrado. Verifique o CPF e tente novamente.",
                })
            return json.dumps({"status": "error", "message": str(e)})
        except Exception as exc:
            logger.error("identify_by_document | %s", exc)
            return json.dumps({"status": "error", "message": str(exc)})

    return [identify_by_phone, identify_by_document]
