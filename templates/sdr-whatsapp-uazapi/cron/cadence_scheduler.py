"""Daily cadence advance — APScheduler job (runs inside app.py).

Once per day (per business timezone) advances eligible leads to the next
cadence_day, enqueues that day's touches, and mirrors the new stage to GHL.
Leads that finish the cadence (advance past the last configured day) are
classified + marked lost. Must log every run (decision 3).

The pure logic lives in `advance_daily_cadence` so it is unit-testable in
isolation (see cron/daily_advance.py for the thin importable wrapper that the
APScheduler job invokes).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core import cadence_engine, lead_manager

logger = logging.getLogger("sdr.cadence")


def _default_content_provider(schedule: dict):
    """Default content provider: emits a generic placeholder per touch.

    A real deployment injects a richer provider (e.g. pulling per-day copy from
    config). Kept here so the job works out-of-the-box with config/cadence.py.
    """

    def _provider(day: int, idx: int) -> dict:
        return {"type": "text", "text": "", "media_url": None, "day": day, "idx": idx}

    return _provider


def _last_day(schedule: dict) -> int:
    return max(schedule.keys()) if schedule else 0


async def advance_daily_cadence(
    conn: Any,
    prefix: str,
    schedule: dict,
    crm: Any | None = None,
    crm_stages: dict | None = None,
    content_provider: Any | None = None,
    loss_classifier: Any | None = None,
    now: datetime | None = None,
) -> int:
    """Advance cadence for all eligible active leads (once-per-day job).

    Eligible leads: status='active' AND cadence_paused=FALSE. For each:
      - bump cadence_day (lead_manager.advance_cadence_day)
      - if the new day exceeds the last configured day, classify + mark lost
        (no further touches enqueued)
      - otherwise enqueue the new day's touches (cadence_engine.enqueue_followups)
        and mirror the new stage to GHL via `crm` (if provided + opp id known)

    Returns the number of leads advanced. Logs the run (INFO) with counts.
    """
    now = now or datetime.now(timezone.utc)
    last_day = _last_day(schedule)
    provider = content_provider or _default_content_provider(schedule)
    stages = crm_stages or {}

    cur = await conn.execute(
        f'SELECT phone, cadence_day, entered_at_ts, ghl_opp_id FROM ('
        f'  SELECT phone, cadence_day, '
        f'         COALESCE(created_at, now()) AS entered_at_ts, ghl_opp_id '
        f'  FROM "{prefix}leads" '
        f"  WHERE status = 'active' AND cadence_paused = FALSE"
        f') t'
    )
    rows = await cur.fetchall()
    cols = [d.name for d in cur.description]
    leads = [dict(zip(cols, r)) for r in rows]

    advanced = 0
    lost = 0
    enqueued_total = 0

    for lead in leads:
        phone = lead["phone"]
        entered_at = lead["entered_at_ts"]
        try:
            new_day = await lead_manager.advance_cadence_day(conn, prefix, phone)
            advanced += 1

            if new_day > last_day:
                reason = "unspecified"
                if loss_classifier is not None:
                    try:
                        reason = await loss_classifier(phone)
                    except Exception:
                        logger.error(
                            "cadence: loss classify failed phone=%s", phone,
                            exc_info=True,
                        )
                await lead_manager.set_status(
                    conn, prefix, phone, "lost", loss_reason=reason
                )
                lost += 1
                logger.info(
                    "cadence: lead finished cadence -> lost phone=%s reason=%s",
                    phone, reason,
                )
                continue

            touches = cadence_engine.compute_touches_for_day(
                new_day, entered_at, schedule
            )
            n = await cadence_engine.enqueue_followups(
                conn, prefix, phone, new_day, touches, provider
            )
            enqueued_total += n

            # Mirror stage to GHL (cadence_day is the source of truth).
            opp_id = lead.get("ghl_opp_id")
            stage_id = stages.get(str(new_day))
            if crm is not None and opp_id and stage_id:
                try:
                    await crm.move_stage(opp_id, stage_id)
                except Exception:
                    logger.error(
                        "cadence: GHL move_stage failed phone=%s day=%s", phone, new_day,
                        exc_info=True,
                    )

            logger.info(
                "cadence: advanced phone=%s -> day=%s enqueued=%d", phone, new_day, n
            )
        except Exception:
            logger.error(
                "cadence: advance FAILED phone=%s", phone, exc_info=True
            )

    logger.info(
        "cadence daily run complete: eligible=%d advanced=%d lost=%d enqueued=%d",
        len(leads), advanced, lost, enqueued_total,
    )
    return advanced


def schedule_cadence_job(scheduler: Any, **kwargs: Any) -> Any:
    """Register the daily advance_daily_cadence job on an APScheduler instance.

    Expected kwargs: ``func`` (the async callable to run, typically the
    daily_advance.run wrapper bound to conn_factory/config), ``hour``/``minute``
    (default 09:00), ``timezone`` (default America/Fortaleza), plus any extra
    APScheduler trigger args. Returns the scheduled job.
    """
    func = kwargs.pop("func")
    hour = kwargs.pop("hour", 9)
    minute = kwargs.pop("minute", 0)
    tz = kwargs.pop("timezone", "America/Fortaleza")
    job_id = kwargs.pop("id", "daily_cadence_advance")

    return scheduler.add_job(
        func,
        trigger="cron",
        hour=hour,
        minute=minute,
        timezone=tz,
        id=job_id,
        replace_existing=True,
        **kwargs,
    )
