"""
Background task: auto-resume AI for sessions that have been paused too long.

Runs every hour. Resumes any session where:
  - AI is currently paused (human takeover mode)
  - AND the session has been paused for >= AUTO_RESUME_HOURS with no new user reply since

Logic:
  1. For each paused session, look at the last USER message timestamp in agno_sessions.runs.
  2. If that timestamp is >= paused_at → user already replied after the admin took over.
     Resume immediately (user is actively writing, AI should respond).
  3. If no new user message, resume after AUTO_RESUME_HOURS since paused_at
     (prevents sessions from staying paused forever when users go silent).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("italo.auto_resume")

CHECK_INTERVAL_SECONDS = 3600  # run every hour


async def _run_check() -> None:
    """Check all paused sessions and resume those that qualify."""
    from app import pause_registry
    from app.db import get_dict_conn

    candidates = pause_registry.get_paused_with_timestamps()
    if not candidates:
        return

    now = datetime.now(timezone.utc)
    resumed: list[str] = []

    for entry in candidates:
        phone = entry["phone"]
        paused_at_str = entry.get("paused_at")

        try:
            if paused_at_str is None:
                paused_at = None
            elif isinstance(paused_at_str, datetime):
                paused_at = paused_at_str
                if paused_at.tzinfo is None:
                    paused_at = paused_at.replace(tzinfo=timezone.utc)
            else:
                paused_at = datetime.fromisoformat(
                    str(paused_at_str).replace("Z", "+00:00")
                )
                if paused_at.tzinfo is None:
                    paused_at = paused_at.replace(tzinfo=timezone.utc)
        except Exception:
            paused_at = None

        # Find last USER message timestamp in agno_sessions (Postgres)
        last_user_ts: datetime | None = None
        try:
            conn = get_dict_conn()
            try:
                cur = conn.cursor()
                cur.execute(
                    "SELECT runs FROM agno_sessions WHERE session_id LIKE %s "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (f"%-wa-{phone}",),
                )
                row = cur.fetchone()
            finally:
                conn.close()

            if row:
                raw = row["runs"]
                val = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(val, str):
                    val = json.loads(val)
                if isinstance(val, list):
                    for run in reversed(val):
                        if isinstance(run, str):
                            run = json.loads(run)
                        for msg in reversed(run.get("messages", [])):
                            if isinstance(msg, str):
                                msg = json.loads(msg)
                            if msg.get("role") == "user":
                                raw_ts = msg.get("created_at")
                                if isinstance(raw_ts, (int, float)):
                                    last_user_ts = datetime.fromtimestamp(
                                        float(raw_ts), tz=timezone.utc
                                    )
                                elif isinstance(raw_ts, str):
                                    try:
                                        last_user_ts = datetime.fromisoformat(
                                            raw_ts.replace("Z", "+00:00")
                                        )
                                    except Exception as exc:
                                        logger.warning("auto_resume: created_at %r ilegível — sessão fica sem last_user_ts: %s", raw_ts, exc, exc_info=True)
                                break
                        if last_user_ts:
                            break
        except Exception as exc:
            logger.warning("auto_resume: error reading session for %s: %s", phone, exc)

        should_resume = False
        reason = ""

        if paused_at and last_user_ts and last_user_ts > paused_at:
            should_resume = True
            reason = f"user replied at {last_user_ts.isoformat()} after pause at {paused_at.isoformat()}"
        elif paused_at and (now - paused_at) >= timedelta(hours=pause_registry.AUTO_RESUME_HOURS):
            should_resume = True
            reason = f"paused for {(now - paused_at).total_seconds() / 3600:.1f}h (limit {pause_registry.AUTO_RESUME_HOURS}h)"
        elif not paused_at and last_user_ts and (now - last_user_ts) >= timedelta(hours=pause_registry.AUTO_RESUME_HOURS):
            should_resume = True
            reason = "legacy row, last user message old enough"

        if should_resume:
            try:
                pause_registry.resume(phone)
                resumed.append(phone)
                logger.info("auto-resumed AI for %s: %s", phone, reason)
            except Exception as exc:
                logger.error("auto_resume: resume failed for %s: %s", phone, exc)

    if resumed:
        logger.info("auto_resume: resumed %d session(s): %s", len(resumed), resumed)
    else:
        logger.debug("auto_resume: checked %d paused session(s), none qualify yet", len(candidates))


async def start_auto_resume_loop() -> None:
    """Background loop — call once from FastAPI startup."""
    logger.info("auto_resume loop started (interval=%ds, threshold=%.1fh)",
                CHECK_INTERVAL_SECONDS, _get_threshold())
    while True:
        try:
            await _run_check()
        except Exception as exc:
            logger.error("auto_resume loop error: %s", exc)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def _get_threshold() -> float:
    try:
        from app import pause_registry
        return pause_registry.AUTO_RESUME_HOURS
    except Exception:
        return 3.0
