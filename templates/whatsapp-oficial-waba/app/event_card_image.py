"""Generate an event card placeholder image (800×418 PNG) via Playwright."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger("italo")

_EVENT_CARDS_DIR = Path(__file__).parent.parent / "static" / "event-cards"


def _html(title: str, dates: str = "") -> str:
    safe_title = title[:80] + ("…" if len(title) > 80 else "")
    dates_html = (
        f'<div style="font-size:16px;font-weight:500;color:rgba(255,255,255,0.75);'
        f'margin-top:10px;letter-spacing:0.5px;">{dates}</div>'
    ) if dates else ""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;600;800&display=swap" rel="stylesheet">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width:800px; height:418px;
    font-family:'DM Sans','Segoe UI',system-ui,sans-serif;
    background:linear-gradient(135deg,#1F4E8C 0%,#2E6BB8 60%,#3B82C4 100%);
    display:flex; align-items:center; justify-content:center;
    overflow:hidden;
  }}
  body::before {{
    content:'';
    position:absolute; inset:0;
    background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.65' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.04'/%3E%3C/svg%3E");
    pointer-events:none;
  }}
  .wrap {{
    position:relative; z-index:1;
    display:flex; flex-direction:column;
    align-items:center; justify-content:center;
    text-align:center;
    padding:40px 80px;
    gap:0;
  }}
  .icon {{ margin-bottom:20px; opacity:0.95; }}
  .title {{
    font-size:28px; font-weight:800; color:#ffffff;
    line-height:1.3; letter-spacing:-0.3px;
    text-shadow:0 2px 12px rgba(0,0,0,0.18);
    display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
    overflow:hidden;
  }}
</style>
</head>
<body>
<div class="wrap">
  <div class="icon">
    <svg width="72" height="72" viewBox="0 0 24 24" fill="none"
         xmlns="http://www.w3.org/2000/svg">
      <rect x="3" y="4" width="18" height="18" rx="3" ry="3"
            stroke="white" stroke-width="1.8" fill="rgba(255,255,255,0.12)"/>
      <line x1="3" y1="9" x2="21" y2="9" stroke="white" stroke-width="1.8"/>
      <line x1="8" y1="2" x2="8" y2="6" stroke="white" stroke-width="2"
            stroke-linecap="round"/>
      <line x1="16" y1="2" x2="16" y2="6" stroke="white" stroke-width="2"
            stroke-linecap="round"/>
      <rect x="7" y="12" width="3" height="3" rx="0.5" fill="white" opacity="0.8"/>
      <rect x="10.5" y="12" width="3" height="3" rx="0.5" fill="white" opacity="0.8"/>
      <rect x="14" y="12" width="3" height="3" rx="0.5" fill="white" opacity="0.8"/>
      <rect x="7" y="16" width="3" height="3" rx="0.5" fill="white" opacity="0.8"/>
      <rect x="10.5" y="16" width="3" height="3" rx="0.5" fill="white" opacity="0.8"/>
    </svg>
  </div>
  <div class="title">{safe_title}</div>
  {dates_html}
</div>
</body></html>"""


async def generate_event_card_image(
    activity_schedule_id: int | str,
    title: str,
    dates: str = "",
) -> Path:
    """Render the event card to PNG and return its Path. Cached by activity_schedule_id."""
    _EVENT_CARDS_DIR.mkdir(parents=True, exist_ok=True)

    key = str(activity_schedule_id) if activity_schedule_id else hashlib.md5(title.encode()).hexdigest()[:10]
    out = _EVENT_CARDS_DIR / f"event_{key}.png"

    if out.exists():
        return out

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 800, "height": 418})
        await page.set_content(_html(title, dates), wait_until="networkidle")
        await page.screenshot(
            path=str(out),
            clip={"x": 0, "y": 0, "width": 800, "height": 418},
        )
        await browser.close()

    logger.info("event_card_image | saved %s", out)
    return out
