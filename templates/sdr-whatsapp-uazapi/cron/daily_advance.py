"""Daily-advance callable that APScheduler invokes (importable + unit-testable).

This is the thin entry point the FastAPI lifespan registers as a cron job. It
opens a DB connection, runs cadence_scheduler.advance_daily_cadence with the
configured cadence schedule + CRM stage map, and closes the connection. Kept
separate from cadence_scheduler so the job wiring can be imported and tested in
isolation without spinning up the whole app.
"""
from __future__ import annotations

import logging
from typing import Any

from cron import cadence_scheduler

logger = logging.getLogger("sdr.cadence")


async def run_daily_advance(
    conn_factory: Any,
    prefix: str,
    schedule: dict,
    crm: Any | None = None,
    crm_stages: dict | None = None,
    content_provider: Any | None = None,
    loss_classifier: Any | None = None,
) -> int:
    """Open a connection, advance daily cadence, close. Returns count advanced.

    `conn_factory` is an async callable yielding a DB connection. Never raises
    out — logs and returns 0 on failure (so the scheduler keeps ticking).
    """
    try:
        conn = await conn_factory()
    except Exception:
        logger.error("daily_advance: could not open DB connection", exc_info=True)
        return 0

    try:
        return await cadence_scheduler.advance_daily_cadence(
            conn,
            prefix,
            schedule,
            crm=crm,
            crm_stages=crm_stages,
            content_provider=content_provider,
            loss_classifier=loss_classifier,
        )
    except Exception:
        logger.error("daily_advance: run failed", exc_info=True)
        return 0
    finally:
        close = getattr(conn, "close", None)
        if close is not None:
            import asyncio

            res = close()
            if asyncio.iscoroutine(res):
                await res
