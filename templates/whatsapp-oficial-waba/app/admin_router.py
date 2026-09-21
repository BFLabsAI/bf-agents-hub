from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.db import get_dict_conn, get_conn
from app.log_store import log_store

logger = logging.getLogger("agent.admin")


async def require_auth():
    # TODO: implement authentication for the admin panel.
    # Options: API key header, Bearer token, IP allowlist, or OAuth.
    # Example (API key): check request header "X-Admin-Key" against a secret env var.
    # For now, admin routes are unprotected — restrict access via reverse proxy in production.
    pass


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_auth)])

_HTML = Path(__file__).parent.parent / "static" / "admin.html"


# ── UI ────────────────────────────────────────────────────────────────────────

@router.get("", include_in_schema=False)
@router.get("/", include_in_schema=False)
async def admin_ui():
    return FileResponse(_HTML)


# ── REST ──────────────────────────────────────────────────────────────────────

@router.get("/sessions")
async def list_sessions():
    return _query_sessions()


@router.get("/sessions/paused")
async def list_paused_sessions():
    """
    Returns all sessions currently paused (AI takeover), enriched with
    user name/photo and paused duration in minutes.
    """
    import app.pause_registry as pause_registry

    paused = pause_registry.get_paused_with_timestamps()
    if not paused:
        return []

    phones = [p["phone"] for p in paused]
    paused_at_map = {p["phone"]: p["paused_at"] for p in paused}

    # Enrich with user info from users_sbot
    user_by_phone: dict[str, dict] = {}
    conn = _db_connect()
    if conn:
        try:
            placeholders = ",".join("?" * len(phones))
            user_rows = conn.execute(
                f"SELECT phone, name, first_name, photo_url FROM users_sbot WHERE phone IN ({placeholders})",
                phones,
            ).fetchall()
            for ur in user_rows:
                user_by_phone[ur[0]] = {"name": ur[1], "first_name": ur[2], "photo_url": ur[3]}

            # Try to resolve real session_id from agno_sessions
            session_id_by_phone: dict[str, str] = {}
            for phone in phones:
                row = conn.execute(
                    "SELECT session_id FROM agno_sessions "
                    "WHERE session_id LIKE ? ORDER BY updated_at DESC LIMIT 1",
                    (f"%-wa-{phone}",),
                ).fetchone()
                if row:
                    session_id_by_phone[phone] = row[0]
        except Exception as exc:
            logger.error("list_paused_sessions DB: %s", exc)
            session_id_by_phone = {}
        finally:
            conn.close()
    else:
        session_id_by_phone = {}

    now = datetime.now(timezone.utc)
    result = []
    for phone in phones:
        paused_at_raw = paused_at_map.get(phone)
        paused_since_iso: str | None = paused_at_raw
        paused_duration_minutes: int | None = None
        if paused_at_raw:
            try:
                paused_dt = datetime.fromisoformat(paused_at_raw.replace("Z", "+00:00"))
                if paused_dt.tzinfo is None:
                    paused_dt = paused_dt.replace(tzinfo=timezone.utc)
                paused_duration_minutes = int((now - paused_dt).total_seconds() / 60)
                paused_since_iso = paused_dt.isoformat()
            except Exception:
                pass

        info = user_by_phone.get(phone, {})
        sid = session_id_by_phone.get(phone) or f"sbot-wa-{phone}"
        result.append({
            "session_id": sid,
            "phone": phone,
            "name": info.get("name") or info.get("first_name") or "",
            "photo_url": info.get("photo_url") or None,
            "paused_since_iso": paused_since_iso,
            "paused_duration_minutes": paused_duration_minutes,
        })
    return result


@router.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    return _query_messages(session_id)


@router.get("/transactions")
async def list_transactions(session_id: Optional[str] = Query(default=None)):
    return _query_transactions(session_id)


@router.get("/users")
async def list_users(
    limit: int = Query(default=500, le=2000),
    since: Optional[str] = Query(default=None, description="ISO date, ex: 2026-05-01"),
    until: Optional[str] = Query(default=None, description="ISO date, ex: 2026-05-31"),
):
    import app.user_log as ulog
    return ulog.query(limit=limit, since_iso=since, until_iso=until)


@router.get("/active-chat-sessions")
async def active_chat_sessions():
    """
    Returns the list of session_ids (WhatsApp phone numbers) that have chat history,
    enriched with user name and photo_url from users_sbot.
    """
    conn = _db_connect()
    if not conn:
        return []
    try:
        rows = conn.execute("""
            SELECT session_id FROM agno_sessions
            WHERE agent_id IN ('italo-itarget-agent', 'sbot-waba-agent')
            ORDER BY updated_at DESC
        """).fetchall()

        phones = []
        phone_map: dict[str, str] = {}
        for r in rows:
            sid = r[0]
            phone = sid.split("-wa-")[-1] if "-wa-" in sid else sid
            phones.append(phone)
            phone_map[phone] = sid

        # Batch-fetch user info for enrichment
        user_by_phone: dict[str, dict] = {}
        if phones:
            placeholders = ",".join("?" * len(phones))
            user_rows = conn.execute(
                f"SELECT phone, name, first_name, photo_url FROM users_sbot WHERE phone IN ({placeholders})",
                phones,
            ).fetchall()
            for ur in user_rows:
                user_by_phone[ur[0]] = {"name": ur[1], "first_name": ur[2], "photo_url": ur[3]}

        sessions = []
        for phone in phones:
            info = user_by_phone.get(phone, {})
            sessions.append({
                "session_id": phone,
                "name": info.get("name") or info.get("first_name") or "",
                "photo_url": info.get("photo_url") or None,
            })
        return sessions
    except Exception as exc:
        logger.error("active_chat_sessions: %s", exc)
        return []
    finally:
        conn.close()


@router.get("/report-stats")
async def report_stats(
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
):
    """
    Replaces the Supabase RPC get_report_stats(p_start_date, p_end_date).
    Returns: new_users, contacted, cadence_breakdown.
    """
    import app.user_log as ulog
    since_iso = f"{start}T00:00:00+00:00"
    until_iso = f"{end}T23:59:59+00:00"

    # new_users: users created in the period
    users = ulog.query(since_iso=since_iso, until_iso=until_iso, limit=5000)
    new_users = len(users)

    conn = _db_connect()
    contacted = 0
    cadence_breakdown: dict[str, int] = {}
    if conn:
        try:
            # contacted: sessions with activity in the period (updated_at in BIGINT epoch)
            start_epoch = int(datetime.fromisoformat(since_iso).timestamp())
            end_epoch = int(datetime.fromisoformat(until_iso).timestamp())
            row = conn.execute("""
                SELECT COUNT(DISTINCT session_id) FROM agno_sessions
                WHERE updated_at BETWEEN ? AND ?
            """, (start_epoch, end_epoch)).fetchone()
            contacted = row[0] if row else 0

            # cadence_breakdown: sessions per day
            rows = conn.execute("""
                SELECT date(datetime(updated_at, 'unixepoch')), COUNT(DISTINCT session_id)
                FROM agno_sessions
                WHERE updated_at BETWEEN ? AND ?
                GROUP BY 1 ORDER BY 1
            """, (start_epoch, end_epoch)).fetchall()
            cadence_breakdown = {r[0]: r[1] for r in rows}
        except Exception as exc:
            logger.error("report_stats DB: %s", exc)
        finally:
            conn.close()

    return [{
        "new_leads": new_users,
        "contacted": contacted,
        "repassado": 0,
        "cadence_breakdown": cadence_breakdown,
    }]


@router.post("/generate-summary")
async def generate_summary(request: Request):
    """
    Generate an AI summary of a chat session.
    Body: { "session_id": "...", "messages": [...] }
    Replaces the Supabase Edge Function generate-summary.
    """
    body = await request.json()
    session_id = body.get("session_id", "")
    messages = body.get("messages", [])

    # Build conversation text — handle both list-of-dicts and pre-formatted string.
    conversation = ""
    if isinstance(messages, str) and messages.strip():
        conversation = messages.strip()
    elif isinstance(messages, list) and messages:
        conversation = "\n".join(
            f"{'Usuário' if m.get('role') == 'user' else 'Agente'}: {m.get('content', '')}"
            for m in messages
            if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")
        )

    if not conversation:
        db_msgs = _query_messages(session_id)
        if not db_msgs:
            return {"summary": "Nenhuma mensagem encontrada para esta sessão."}
        conversation = "\n".join(
            f"{'Usuário' if m['role'] == 'user' else 'Agente'}: {m['content']}"
            for m in db_msgs
            if m.get("role") in ("user", "assistant") and m.get("content")
        )

    try:
        from app.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL_ID as WEBCHAT_MODEL_ID
        import httpx

        prompt = (
            "Faça um resumo conciso da conversa abaixo em português, destacando: "
            "o que o usuário queria, o que o agente fez, e o resultado final. "
            "Máximo 3 parágrafos curtos.\n\n"
            f"{conversation[:6000]}"
        )

        async with httpx.AsyncClient(base_url=OPENROUTER_BASE_URL, timeout=30.0) as client:
            resp = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": WEBCHAT_MODEL_ID,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 400,
                }
            )
            resp.raise_for_status()
            summary_text = resp.json()["choices"][0]["message"]["content"]

        return {"summary": summary_text, "session_id": session_id}
    except Exception as exc:
        logger.error("generate_summary: %s", exc)
        return {"summary": f"Erro ao gerar resumo: {exc}", "session_id": session_id}


@router.get("/chat-sessions")
async def list_chat_sessions(
    channel: Optional[str] = Query(default=None, description="whatsapp | web"),
    limit: int = Query(default=100, le=500),
):
    """
    List chat sessions in the flat format expected by the dashboard.
    channel=whatsapp → italo-*-agent sessions
    channel=web      → sbot-auth-agent / sbot-rag-agent sessions
    """
    conn = _db_connect()
    if not conn:
        return []
    try:
        if channel == "whatsapp":
            agent_filter = "AND agent_id IN ('italo-itarget-agent', 'sbot-waba-agent')"
        elif channel == "web":
            agent_filter = "AND agent_id IN ('sbot-auth-agent', 'sbot-rag-agent')"
        else:
            agent_filter = ""

        rows = conn.execute(f"""
            SELECT session_id, agent_id, runs, created_at, updated_at
            FROM agno_sessions
            WHERE 1=1 {agent_filter}
            ORDER BY updated_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

        # Build person_id list from web sessions for name/photo enrichment
        person_ids_to_fetch: list[int] = []
        for row in rows:
            sid = dict(row)["session_id"]
            if "person-" in sid:
                try:
                    person_ids_to_fetch.append(int(sid.split("person-")[-1]))
                except ValueError:
                    pass

        user_by_pid: dict[int, dict] = {}
        if person_ids_to_fetch:
            placeholders = ",".join("?" * len(person_ids_to_fetch))
            user_rows = conn.execute(
                f"SELECT person_id, name, first_name, photo_url FROM users_sbot WHERE person_id IN ({placeholders})",
                person_ids_to_fetch,
            ).fetchall()
            for ur in user_rows:
                user_by_pid[ur[0]] = {"name": ur[1], "first_name": ur[2], "photo_url": ur[3]}

        result = []
        for row in rows:
            row_d = dict(row)
            sid = row_d["session_id"]
            msgs = _parse_messages(row_d.get("runs"))
            last_user = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
            last_ai = next((m["content"] for m in reversed(msgs) if m["role"] == "assistant"), "")

            # Try to resolve display name and photo from users_sbot
            user_name = ""
            photo_url = None
            if "person-" in sid:
                try:
                    pid = int(sid.split("person-")[-1])
                    info = user_by_pid.get(pid, {})
                    user_name = info.get("name") or info.get("first_name") or ""
                    photo_url = info.get("photo_url")
                except ValueError:
                    pass

            result.append({
                "session_id": sid,
                "agent_id": row_d["agent_id"],
                "channel": "whatsapp" if "wa-" in sid else "web",
                "message_count": sum(1 for m in msgs if m["role"] == "user"),
                "last_user_message": last_user[:120],
                "last_agent_message": last_ai[:120],
                "user_name": user_name,
                "photo_url": photo_url,
                "created_at": _ts_to_iso(row_d.get("created_at")),
                "updated_at": _ts_to_iso(row_d.get("updated_at")),
            })
        return result
    except Exception as exc:
        logger.error("list_chat_sessions: %s", exc)
        return []
    finally:
        conn.close()


@router.get("/chat-sessions/{session_id}/messages")
async def get_chat_session_messages(session_id: str):
    """Flat message list for a session — format: {role, content, created_at}."""
    return _query_messages(session_id)




@router.get("/payment-stats")
async def payment_stats(
    start: str = Query(..., description="YYYY-MM-DD"),
    end: str = Query(..., description="YYYY-MM-DD"),
):
    """
    Payment generation and confirmation metrics split by channel.
    Returns { whatsapp: {...}, web: {...} } each with:
      generated_count, generated_amount, confirmed_count, confirmed_amount, conversion_rate.
    Channel detection: session_id containing 'sbot-web' → web, else → whatsapp.
    """
    def _empty():
        return {
            "generated_count": 0,
            "generated_amount": 0.0,
            "confirmed_count": 0,
            "confirmed_amount": 0.0,
            "conversion_rate": 0.0,
        }

    since_iso = f"{start}T00:00:00"
    until_iso = f"{end}T23:59:59"

    conn = _db_connect()
    if not conn:
        return {"whatsapp": _empty(), "web": _empty()}

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'payment_generated'"
        )
        if not cur.fetchone():
            return {"whatsapp": _empty(), "web": _empty()}

        cur.execute("""
            SELECT session_id, amount, status
            FROM payment_generated
            WHERE generated_at >= %s AND generated_at <= %s
        """, (since_iso, until_iso))
        rows = cur.fetchall()

        stats: dict[str, dict] = {"whatsapp": _empty(), "web": _empty()}
        for row in rows:
            sid = row[0] or ""
            channel = "web" if "sbot-web" in sid else "whatsapp"
            amount = float(row[1] or 0)
            confirmed = row[2] == "confirmed"
            s = stats[channel]
            s["generated_count"] += 1
            s["generated_amount"] += amount
            if confirmed:
                s["confirmed_count"] += 1
                s["confirmed_amount"] += amount

        for s in stats.values():
            gen = s["generated_count"]
            s["conversion_rate"] = round(s["confirmed_count"] / gen * 100, 1) if gen > 0 else 0.0

        return stats
    except Exception as exc:
        logger.error("payment_stats: %s", exc)
        return {"whatsapp": _empty(), "web": _empty()}
    finally:
        conn.close()


@router.get("/users/{phone}/window-status")
async def window_status(phone: str):
    """
    Check whether phone is within the 24h WhatsApp messaging window.
    Looks at the latest user-role message timestamp in agno_sessions.
    """
    conn = _db_connect()
    if not conn:
        return {"phone": phone, "in_window": False, "error": "DB unavailable",
                "can_send_free_form": False, "must_use_template": True}
    try:
        row = conn.execute("""
            SELECT runs, updated_at FROM agno_sessions
            WHERE session_id LIKE ? OR session_id LIKE ?
            ORDER BY updated_at DESC LIMIT 1
        """, (f"%-wa-{phone}", f"%-wa-{phone}%")).fetchone()

        if not row:
            return {
                "phone": phone, "in_window": False,
                "last_user_message_at": None, "window_expires_at": None,
                "can_send_free_form": False, "must_use_template": True,
            }

        msgs = _parse_messages(row["runs"])
        last_user_ts: str | None = None
        for m in reversed(msgs):
            if m["role"] == "user" and m.get("created_at"):
                last_user_ts = m["created_at"]
                break

        if not last_user_ts:
            last_user_ts = _ts_to_iso(row["updated_at"])

        now = datetime.now(timezone.utc)

        if not last_user_ts:
            return {
                "phone": phone, "in_window": False,
                "last_user_message_at": None, "window_expires_at": None,
                "can_send_free_form": False, "must_use_template": True,
            }

        try:
            last_dt = datetime.fromisoformat(last_user_ts.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
        except Exception:
            last_dt = now - timedelta(hours=25)

        window_expires = last_dt + timedelta(hours=24)
        in_window = now < window_expires

        return {
            "phone": phone,
            "in_window": in_window,
            "last_user_message_at": last_dt.isoformat(),
            "window_expires_at": window_expires.isoformat(),
            "can_send_free_form": in_window,
            "must_use_template": not in_window,
        }
    except Exception as exc:
        logger.error("window_status: %s", exc)
        return {"phone": phone, "in_window": False, "error": str(exc),
                "can_send_free_form": False, "must_use_template": True}
    finally:
        conn.close()


@router.post("/send-message")
async def send_message_endpoint(request: Request):
    """Send a free-form text message to a WhatsApp user (requires active 24h window)."""
    body = await request.json()
    phone = (body.get("phone") or "").strip()
    message = (body.get("message") or "").strip()
    reply_to_wamid = body.get("reply_to_wamid") or None

    if not phone or not message:
        return {"success": False, "error": "phone and message are required"}

    try:
        from app.waba_client import WABAClient
        from app import admin_message_log, pause_registry
        client = WABAClient()
        result = await client.send_text(to=phone, body=message, reply_to_wamid=reply_to_wamid)
        await client.aclose()
        wamid = (result.get("messages") or [{}])[0].get("id")
        admin_message_log.insert(phone=phone, content=message, wamid=wamid)
        # Auto-pause AI and inject message into agent memory
        pause_registry.pause(phone)
        pause_registry.inject_assistant_message(phone, message, SESSION_DB_PATH)
        logger.info("send_message admin | to=%s wamid=%s (AI paused)", phone, wamid)
        return {"success": True, "wamid": wamid, "timestamp": datetime.now(timezone.utc).isoformat(), "ai_paused": True}
    except Exception as exc:
        logger.error("send_message admin: %s", exc)
        return {"success": False, "error": str(exc)}


@router.post("/send-template")
async def send_template_endpoint(request: Request):
    """Send a pre-approved HSM template to a WhatsApp user."""
    body = await request.json()
    phone = (body.get("phone") or "").strip()
    template_name = (body.get("template_name") or "").strip()
    language = (body.get("language") or "pt_BR").strip()
    components = body.get("components") or []

    if not phone or not template_name:
        return {"success": False, "error": "phone and template_name are required"}

    try:
        from app.waba_client import WABAClient
        from app import admin_message_log, pause_registry
        client = WABAClient()
        result = await client.send_template(
            to=phone, template_name=template_name,
            language=language, components=components or None,
        )
        await client.aclose()
        wamid = (result.get("messages") or [{}])[0].get("id")
        template_label = f"[Template: {template_name}]"
        admin_message_log.insert(
            phone=phone, content=template_label,
            template_name=template_name, wamid=wamid,
        )
        pause_registry.pause(phone)
        pause_registry.inject_assistant_message(phone, template_label, SESSION_DB_PATH)
        logger.info("send_template admin | to=%s template=%s wamid=%s (AI paused)", phone, template_name, wamid)
        return {"success": True, "wamid": wamid, "timestamp": datetime.now(timezone.utc).isoformat(), "ai_paused": True}
    except Exception as exc:
        logger.error("send_template admin: %s", exc)
        return {"success": False, "error": str(exc)}


@router.get("/sessions/{phone}/ai-status")
async def get_ai_status(phone: str):
    """Return whether AI is paused for this phone."""
    from app import pause_registry
    return {"phone": phone, "ai_paused": pause_registry.is_paused(phone)}


@router.post("/sessions/{phone}/pause-ai")
async def pause_ai(phone: str):
    """Pause AI for a phone (human takeover)."""
    from app import pause_registry
    pause_registry.pause(phone)
    return {"phone": phone, "ai_paused": True}


@router.post("/sessions/{phone}/resume-ai")
async def resume_ai(phone: str):
    """Resume AI for a phone (hand back to bot)."""
    from app import pause_registry
    pause_registry.resume(phone)
    return {"phone": phone, "ai_paused": False}


@router.get("/templates")
async def list_waba_templates(
    status: Optional[str] = Query(default=None, description="Filter by template status, e.g. APPROVED"),
    name: Optional[str] = Query(default=None, description="Filter by template name"),
    limit: int = Query(default=100, le=500),
):
    """List message templates from Meta Business Manager."""
    from app.config import WABA_ACCESS_TOKEN, WABA_BUSINESS_ACCOUNT_ID
    import httpx

    if not WABA_BUSINESS_ACCOUNT_ID:
        return []

    try:
        url = f"https://graph.facebook.com/v21.0/{WABA_BUSINESS_ACCOUNT_ID}/message_templates"
        params: dict = {"fields": "id,name,language,status,category,components", "limit": str(limit)}
        if status:
            params["status"] = status
        if name:
            params["name"] = name
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {WABA_ACCESS_TOKEN}"},
                params=params,
            )
        if resp.status_code != 200:
            logger.warning("list_waba_templates: %s %s", resp.status_code, resp.text[:200])
            return []
        data = resp.json().get("data", [])
        return [
            {
                "id": t.get("id"),
                "name": t.get("name"),
                "language": t.get("language"),
                "status": t.get("status"),
                "category": t.get("category"),
                "components": t.get("components", []),
            }
            for t in data
        ]
    except Exception as exc:
        logger.error("list_waba_templates: %s", exc)
        return []


@router.post("/templates")
async def create_waba_template(request: Request):
    """Create a WhatsApp message template via Meta Business Management API."""
    from app.config import WABA_ACCESS_TOKEN, WABA_BUSINESS_ACCOUNT_ID
    from fastapi import HTTPException
    import httpx

    if not WABA_BUSINESS_ACCOUNT_ID or not WABA_ACCESS_TOKEN:
        raise HTTPException(status_code=500, detail="WABA_BUSINESS_ACCOUNT_ID and WABA_ACCESS_TOKEN env vars required")

    payload = await request.json()
    url = f"https://graph.facebook.com/v21.0/{WABA_BUSINESS_ACCOUNT_ID}/message_templates"
    headers = {"Authorization": f"Bearer {WABA_ACCESS_TOKEN}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, json=payload, headers=headers)
        if not r.is_success:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return r.json()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("create_waba_template: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/templates/{template_id}")
async def edit_waba_template(template_id: str, request: Request):
    """Edit a WhatsApp message template (only REJECTED or PAUSED templates)."""
    from app.config import WABA_ACCESS_TOKEN
    from fastapi import HTTPException
    import httpx

    payload = await request.json()
    url = f"https://graph.facebook.com/v21.0/{template_id}"
    headers = {"Authorization": f"Bearer {WABA_ACCESS_TOKEN}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, json=payload, headers=headers)
        if not r.is_success:
            logger.warning("edit_waba_template %s: %s %s", template_id, r.status_code, r.text[:500])
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return r.json()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("edit_waba_template: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/templates")
async def delete_waba_template(
    name: str = Query(..., description="Template name to delete"),
    hsm_id: Optional[str] = Query(default=None, description="Specific template ID (hsm_id) to delete"),
):
    """Delete a WhatsApp message template via Meta Business Management API."""
    from app.config import WABA_ACCESS_TOKEN, WABA_BUSINESS_ACCOUNT_ID
    from fastapi import HTTPException
    import httpx

    if not WABA_BUSINESS_ACCOUNT_ID or not WABA_ACCESS_TOKEN:
        raise HTTPException(status_code=500, detail="WABA_BUSINESS_ACCOUNT_ID and WABA_ACCESS_TOKEN env vars required")

    params: dict = {"name": name}
    if hsm_id:
        params["hsm_id"] = hsm_id

    url = f"https://graph.facebook.com/v21.0/{WABA_BUSINESS_ACCOUNT_ID}/message_templates"
    headers = {"Authorization": f"Bearer {WABA_ACCESS_TOKEN}"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.delete(url, params=params, headers=headers)
        if not r.is_success:
            raise HTTPException(status_code=r.status_code, detail=r.text)
        return r.json()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("delete_waba_template: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/payment-webhooks")
async def list_payment_webhooks(
    limit: int = Query(default=100, le=500),
    status: Optional[str] = Query(default=None, description="sent | error | auth_failed | invalid_phone"),
):
    from app.payment_webhook_log import query
    return query(limit=limit, status=status)


@router.get("/llm-usage")
async def list_llm_usage(
    session_id: Optional[str] = Query(default=None),
    limit: int = Query(default=200, le=1000),
):
    import app.llm_usage_log as llm_log
    rows = llm_log.query(session_id=session_id, limit=limit)
    totals = llm_log.totals()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
    today_totals = llm_log.totals(since_iso=today)
    return {"rows": rows, "totals": totals, "today": today_totals}


# ── Quick Replies ─────────────────────────────────────────────────────────────

@router.get("/quick-replies")
async def list_quick_replies():
    """List all quick-reply snippets ordered by title."""
    import app.quick_replies_store as qr_store
    return qr_store.list_all()


@router.post("/quick-replies")
async def create_quick_reply(request: Request):
    """Create a new quick-reply snippet. Body: {title: str, body: str}."""
    import app.quick_replies_store as qr_store
    body = await request.json()
    title = (body.get("title") or "").strip()
    text_body = (body.get("body") or "").strip()
    if not title or not text_body:
        return {"error": "title and body are required"}
    try:
        return qr_store.create(title=title, body=text_body)
    except Exception as exc:
        logger.error("create_quick_reply: %s", exc)
        return {"error": str(exc)}


@router.delete("/quick-replies/{id}")
async def delete_quick_reply(id: int):
    """Delete a quick-reply snippet by id."""
    import app.quick_replies_store as qr_store
    deleted = qr_store.delete(id)
    return {"success": deleted}


# ── Operator Notes ────────────────────────────────────────────────────────────

@router.get("/sessions/{session_id}/notes")
async def list_operator_notes(session_id: str):
    """List operator notes for a session (phone number)."""
    import app.operator_notes_store as notes_store
    return notes_store.get_for_session(session_id)


@router.post("/sessions/{session_id}/notes")
async def create_operator_note(session_id: str, request: Request):
    """Add an operator note to a session. Body: {note: str}."""
    import app.operator_notes_store as notes_store
    body = await request.json()
    note = (body.get("note") or "").strip()
    if not note:
        return {"error": "note is required"}
    try:
        return notes_store.create(session_id=session_id, note=note)
    except Exception as exc:
        logger.error("create_operator_note: %s", exc)
        return {"error": str(exc)}


@router.delete("/notes/{id}")
async def delete_operator_note(id: int):
    """Delete an operator note by id."""
    import app.operator_notes_store as notes_store
    deleted = notes_store.delete(id)
    return {"success": deleted}


# ── iTarget Profile ───────────────────────────────────────────────────────────

@router.get("/sessions/{phone}/itarget-profile")
async def get_itarget_profile(phone: str):
    """
    Returns the iTarget profile and subscriptions for a session.
    Supports both WhatsApp (phone number) and web (sbot-web-person-{id}) sessions.
    Reads session_state from agno_sessions, then calls the iTarget API.
    """
    import httpx
    from app.config import SBOT_API_BASE_URL, SBOT_ENV

    _not_found = {
        "person_id": None,
        "first_name": None,
        "member_status": None,
        "access_token_available": False,
        "subscriptions": None,
        "error": "Sessão não encontrada",
    }

    conn = _db_connect()
    if not conn:
        return _not_found

    try:
        # Web sessions pass the full session_id (e.g. "sbot-web-person-587")
        # WhatsApp sessions pass just the phone number
        is_web = phone.startswith("sbot-")
        if is_web:
            row = conn.execute(
                """
                SELECT runs FROM agno_sessions
                WHERE session_id = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (phone,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT runs FROM agno_sessions
                WHERE session_id LIKE '%' || '-wa-' || ?
                  AND agent_id IN ('italo-itarget-agent', 'sbot-waba-agent')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (phone,),
            ).fetchone()
    except Exception as exc:
        logger.error("get_itarget_profile DB: %s", exc)
        conn.close()
        return _not_found
    finally:
        conn.close()

    if not row:
        return _not_found

    # Decode runs — double-encoded JSON (same pattern as inject_assistant_message)
    raw = row[0]
    try:
        val = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(val, str):
            val = json.loads(val)
        runs: list = val if isinstance(val, list) else []
    except Exception:
        runs = []

    # Extract session_state from the last run
    session_state: dict = {}
    for item in reversed(runs):
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except Exception:
                continue
        if isinstance(item, dict) and item.get("session_state"):
            session_state = item["session_state"]
            break

    access_token = session_state.get("access_token")
    person_id = session_state.get("person_id")
    first_name = session_state.get("first_name")
    member_status = session_state.get("member_status")

    if not access_token:
        return {
            "person_id": person_id,
            "first_name": first_name,
            "member_status": member_status,
            "access_token_available": False,
            "subscriptions": None,
            "error": "Token de acesso não disponível na sessão",
        }

    # Call iTarget API for subscriptions
    url = f"{SBOT_API_BASE_URL}/api{SBOT_ENV}/subscription/persons/my-subscription"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            body = resp.json()

        # Unwrap outer envelope (same logic as itarget_client.my_subscriptions)
        if isinstance(body, dict) and "data" in body and not isinstance(body["data"], list):
            body = body["data"]

        items: list[dict] = []
        if isinstance(body, dict) and "tabs" in body:
            tabs_data = body["tabs"].get("data", {})
            if isinstance(tabs_data, dict):
                seen: set = set()
                for tab_items in tabs_data.values():
                    if not isinstance(tab_items, list):
                        continue
                    for item in tab_items:
                        if not isinstance(item, dict):
                            continue
                        key = item.get("accountReceiveId") or id(item)
                        if key not in seen:
                            seen.add(key)
                            items.append(item)
            elif isinstance(tabs_data, list):
                seen = set()
                for tab in tabs_data:
                    if not isinstance(tab, list):
                        continue
                    for item in tab:
                        if not isinstance(item, dict):
                            continue
                        key = item.get("accountReceiveId") or id(item)
                        if key not in seen:
                            seen.add(key)
                            items.append(item)
        elif isinstance(body, list):
            items = body
        elif isinstance(body, dict):
            items = body.get("data", [])

        subscriptions = [
            {
                "event_title": (
                    item.get("activityScheduleTitle")
                    or item.get("eventTitle")
                    or item.get("title")
                    or ""
                ),
                "status_label": (
                    item.get("statusSubscriptionLabel")
                    or item.get("statusLabel")
                    or item.get("status")
                    or ""
                ),
                "amount": item.get("amount"),
                "subscription_date": item.get("subscriptionDate"),
                "payment_due": item.get("dueDate"),
            }
            for item in items
        ]

        return {
            "person_id": person_id,
            "first_name": first_name,
            "member_status": member_status,
            "access_token_available": True,
            "subscriptions": subscriptions,
            "error": None,
        }

    except httpx.HTTPStatusError as exc:
        logger.error("get_itarget_profile API HTTP error: %s", exc)
        return {
            "person_id": person_id,
            "first_name": first_name,
            "member_status": member_status,
            "access_token_available": True,
            "subscriptions": None,
            "error": f"Erro HTTP {exc.response.status_code}: {exc.response.text[:200]}",
        }
    except Exception as exc:
        logger.error("get_itarget_profile API: %s", exc)
        return {
            "person_id": person_id,
            "first_name": first_name,
            "member_status": member_status,
            "access_token_available": True,
            "subscriptions": None,
            "error": str(exc),
        }


# ── SSE stream ────────────────────────────────────────────────────────────────

@router.get("/stream")
async def sse_stream(request: Request):
    queue = log_store.subscribe()

    async def generator():
        try:
            # Send current in-memory snapshot on connect
            init = json.dumps({"type": "init", "transactions": log_store.get_all()})
            yield f"data: {init}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {json.dumps(payload)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            log_store.unsubscribe(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # disable nginx buffering
        },
    )


# ── Postgres helpers ──────────────────────────────────────────────────────────

def _db_connect():
    """Return a Postgres dict-cursor connection, or None on error."""
    try:
        return get_dict_conn()
    except Exception as exc:
        logger.warning("DB connect failed: %s", exc)
        return None


def _session_table(conn) -> str:
    return "agno_sessions"

def _ts_to_iso(ts) -> str | None:
    """Normalise Agno timestamps: Unix float → ISO string, or pass through."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
        except Exception:
            return None
    return str(ts)


def _parse_runs(runs_raw) -> list[dict]:
    """
    Parse Agno's `runs` column into a list of run dicts.
    Agno serialises the column as a JSON string whose value is itself a JSON
    array (triple-encoded: DB text → str → list[dict]), so we decode twice.
    """
    if not runs_raw:
        return []
    try:
        val = json.loads(runs_raw) if isinstance(runs_raw, str) else runs_raw
        if isinstance(val, str):
            val = json.loads(val)
        if not isinstance(val, list):
            return []
        runs = []
        for item in val:
            if isinstance(item, str):
                try:
                    item = json.loads(item)
                except Exception:
                    continue
            if isinstance(item, dict):
                runs.append(item)
        return runs
    except Exception:
        return []


def _parse_messages(runs_raw) -> list[dict]:
    """
    Extract user/assistant messages from Agno's session `runs` column.
    Each run has messages[]; we surface user text and final assistant text.
    """
    runs = _parse_runs(runs_raw)
    result = []
    for run in runs:
        run_ts = _ts_to_iso(run.get("created_at"))
        for msg in run.get("messages", []):
            role = msg.get("role", "")
            content = msg.get("content")

            if role not in ("user", "assistant"):
                continue

            # content can be a string, a list of blocks, or None (tool-call only turn)
            if isinstance(content, list):
                text = " ".join(
                    c.get("text", "") for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                ).strip()
            elif isinstance(content, str):
                text = content.strip()
            else:
                continue   # tool-only assistant turn — skip

            if not text:
                continue

            result.append({
                "role": role,
                "content": text,
                "created_at": _ts_to_iso(msg.get("created_at")) or run_ts,
            })

    return result


def _query_transactions(session_id: str | None = None) -> list[dict]:
    """Return completed transactions from DB, merged with any live in-memory ones."""
    import json as _json
    from app.log_store import log_store

    # In-memory live transactions (processing / just completed)
    live = {tx["id"]: tx for tx in log_store.get_all(session_id=session_id)}

    # Historical from DB
    conn = _db_connect()
    db_rows: list[dict] = []
    if conn:
        try:
            cur = conn.cursor()
            q = "SELECT * FROM agent_transactions"
            params: list = []
            if session_id:
                q += " WHERE session_id = %s"
                params.append(session_id)
            q += " ORDER BY started_at DESC LIMIT 200"
            cur.execute(q, params)
            for row in cur.fetchall():
                r = dict(row)
                try:
                    r["events"] = _json.loads(r.get("events") or "[]")
                except Exception:
                    r["events"] = []
                if r.get("started_at") and r.get("completed_at"):
                    try:
                        from datetime import datetime
                        a = datetime.fromisoformat(str(r["started_at"]))
                        b = datetime.fromisoformat(str(r["completed_at"]))
                        r["duration_ms"] = int((b - a).total_seconds() * 1000)
                    except Exception:
                        r["duration_ms"] = None
                else:
                    r["duration_ms"] = None
                db_rows.append(r)
        except Exception as exc:
            logger.error("query_transactions DB: %s", exc)
        finally:
            conn.close()
    # Merge: live takes precedence (more up-to-date events)
    merged = {**{r["id"]: r for r in db_rows}, **live}
    return sorted(merged.values(), key=lambda t: t.get("started_at", ""), reverse=True)


def _query_sessions() -> list[dict]:
    conn = _db_connect()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT session_id, runs, updated_at
            FROM agno_sessions
            ORDER BY updated_at DESC
            LIMIT 100
        """)
        rows = cur.fetchall()

        tx_names: dict[str, str] = {}
        try:
            cur.execute(
                "SELECT session_id, from_number FROM agent_transactions ORDER BY started_at DESC"
            )
            for r in cur.fetchall():
                r_d = dict(r)
                sid, fn = r_d.get("session_id", ""), r_d.get("from_number") or ""
                if sid and fn and not fn.startswith("agent-"):
                    tx_names.setdefault(sid, fn)
        except Exception:
            pass
        for tx_dict in log_store.get_all():
            sid = tx_dict.get("session_id", "")
            fn = tx_dict.get("from_number", "")
            if sid and fn and not fn.startswith("sbot-") and not fn.startswith("italo-"):
                tx_names[sid] = fn

        result = []
        for row in rows:
            row_d = dict(row)
            session_id = row_d.get("session_id", "")

            if session_id.startswith("sbot-web-"):
                channel = "webchat"
                # Use the real name from transaction log when available.
                from_number = tx_names.get(session_id) or session_id[len("sbot-web-"):]
            else:
                channel = "whatsapp"
                from_number = session_id.split("-wa-")[-1] if "-wa-" in session_id else session_id

            messages = _parse_messages(row_d.get("runs"))
            last_user = next(
                (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
            )
            user_count = sum(1 for m in messages if m["role"] == "user")

            result.append({
                "session_id": session_id,
                "from_number": from_number,
                "channel": channel,
                "last_message": last_user[:120],
                "message_count": user_count,
                "updated_at": _ts_to_iso(row_d.get("updated_at")) or row_d.get("updated_at", ""),
            })
        return result
    except Exception as exc:
        logger.error("query_sessions: %s", exc)
        return []
    finally:
        conn.close()


def _query_messages(session_id: str) -> list[dict]:
    conn = _db_connect()
    if not conn:
        return []
    try:
        table = _session_table(conn)
        if not table:
            return []
        # Try exact match first, then fuzzy match for wa-{phone} pattern
        row = conn.execute(
            f"SELECT runs FROM {table} WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            row = conn.execute(
                f"SELECT runs FROM {table} WHERE session_id LIKE ? ORDER BY updated_at DESC LIMIT 1",
                (f"%-wa-{session_id}",)
            ).fetchone()
        msgs = _parse_messages(row["runs"]) if row else []

        # Merge admin-sent messages by phone (only for whatsapp-like sessions)
        if "-wa-" not in session_id and "sbot-web" not in session_id and "person-" not in session_id:
            phone = session_id  # called with raw phone
        elif "-wa-" in session_id:
            phone = session_id.split("-wa-")[-1]
        else:
            phone = None

        if phone:
            try:
                from app import admin_message_log
                admin_msgs = admin_message_log.query_by_phone(phone)
                if admin_msgs:
                    msgs = sorted(
                        msgs + admin_msgs,
                        key=lambda m: m.get("created_at") or "",
                    )
            except Exception as exc:
                logger.warning("merge admin_sent_messages: %s", exc)

        return msgs
    except Exception as exc:
        logger.error("query_messages: %s", exc)
        return []
    finally:
        conn.close()
