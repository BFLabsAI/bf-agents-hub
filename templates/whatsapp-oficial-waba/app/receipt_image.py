"""
Generate a premium payment-confirmation card (800×418 PNG) via Playwright.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger("italo")

_RECEIPTS_DIR = Path(__file__).parent.parent / "static" / "receipts"


def _html(event_name: str, amount_str: str, method: str) -> str:
    name = event_name[:80] + ("…" if len(event_name) > 80 else "")
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700;800&display=swap" rel="stylesheet">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width:800px; height:418px;
    font-family:'DM Sans','Segoe UI',system-ui,sans-serif;
    /* white top bleeding into deep green bottom-right */
    background:linear-gradient(148deg,
      #ffffff 0%,
      #f0fdf4 28%,
      #bbf7d0 55%,
      #15803d 82%,
      #14532d 100%);
    display:flex; align-items:center; justify-content:center;
    overflow:hidden;
  }}
  /* subtle noise texture layer */
  body::before {{
    content:'';
    position:absolute; inset:0;
    background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.75' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.03'/%3E%3C/svg%3E");
    pointer-events:none; z-index:0;
  }}
  .card {{
    position:relative; z-index:1;
    width:748px; height:370px;
    /* frosted white card sits on top of the gradient */
    background:rgba(255,255,255,0.82);
    backdrop-filter:blur(2px);
    border-radius:28px;
    border:1.5px solid rgba(255,255,255,0.95);
    box-shadow:
      0 2px 0 rgba(255,255,255,0.9) inset,
      0 24px 56px rgba(21,128,61,0.18),
      0 4px 16px rgba(0,0,0,0.06);
    display:flex; align-items:center;
    padding:40px 52px; gap:44px;
    overflow:hidden;
  }}
  /* green accent stripe on left edge */
  .card::before {{
    content:'';
    position:absolute; left:0; top:0; bottom:0;
    width:5px;
    background:linear-gradient(180deg,#22c55e 0%,#15803d 100%);
    border-radius:28px 0 0 28px;
  }}
  /* ── check circle ── */
  .check-wrap {{
    flex-shrink:0;
    width:108px; height:108px;
    border-radius:50%;
    background:linear-gradient(135deg,#22c55e 0%,#15803d 100%);
    display:flex; align-items:center; justify-content:center;
    box-shadow:
      0 0 0 10px rgba(34,197,94,0.12),
      0 12px 36px rgba(21,128,61,0.35);
  }}
  .check-wrap svg {{ width:52px; height:52px; }}
  /* ── right content ── */
  .info {{ flex:1; min-width:0; }}
  .badge {{
    display:inline-flex; align-items:center; gap:7px;
    border-radius:999px;
    padding:5px 15px;
    background:rgba(21,128,61,0.09);
    border:1.5px solid rgba(21,128,61,0.25);
    color:#15803d; font-size:11px; font-weight:800;
    letter-spacing:2.2px; text-transform:uppercase;
    margin-bottom:13px;
  }}
  .badge::before {{
    content:''; display:block; width:6px; height:6px;
    border-radius:50%;
    background:#22c55e;
    box-shadow:0 0 6px rgba(34,197,94,0.7);
  }}
  .event {{
    font-size:20px; font-weight:800; color:#111827;
    line-height:1.32; margin-bottom:24px;
    display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
    overflow:hidden;
    letter-spacing:-0.2px;
  }}
  .sep {{
    width:100%; height:1px;
    background:linear-gradient(90deg,rgba(21,128,61,0.15) 0%,transparent 70%);
    margin-bottom:22px;
  }}
  .details {{ display:flex; gap:44px; align-items:flex-end; }}
  .dt {{
    font-size:10px; font-weight:700; letter-spacing:2.2px;
    text-transform:uppercase; color:rgba(0,0,0,0.35);
    margin-bottom:6px;
  }}
  .dd {{ font-size:24px; font-weight:800; color:#111827; letter-spacing:-0.5px; }}
  /* green pill — payment method */
  .method-pill {{
    display:inline-block;
    background:linear-gradient(135deg,#22c55e 0%,#15803d 100%);
    border-radius:10px;
    padding:6px 20px;
    font-size:16px; font-weight:800; color:#ffffff;
    letter-spacing:0.4px;
    box-shadow:0 4px 16px rgba(21,128,61,0.35);
  }}
</style>
</head>
<body>
<div class="card">
  <div class="check-wrap">
    <svg viewBox="0 0 24 24" fill="none"
         stroke="#ffffff" stroke-width="2.8"
         stroke-linecap="round" stroke-linejoin="round">
      <polyline points="20 6 9 17 4 12"/>
    </svg>
  </div>
  <div class="info">
    <div class="badge">Pagamento Confirmado</div>
    <div class="event">{name}</div>
    <div class="sep"></div>
    <div class="details">
      <div class="dl">
        <div class="dt">Valor</div>
        <div class="dd">{amount_str}</div>
      </div>
      <div class="dl">
        <div class="dt">Método</div>
        <div class="dd"><span class="method-pill">{method}</span></div>
      </div>
    </div>
  </div>
</div>
</body></html>"""


async def generate_confirmation_image(
    event_name: str,
    amount_str: str,
    method: str,
    account_receive_id: int = 0,
) -> Path:
    """
    Render the confirmation card to PNG and return its Path.
    Filename is deterministic per accountReceiveId so re-sends reuse the file.
    """
    _RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)

    key = str(account_receive_id) if account_receive_id else hashlib.md5(
        (event_name + amount_str).encode()
    ).hexdigest()[:10]
    out = _RECEIPTS_DIR / f"confirm_{key}.png"

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 800, "height": 418})
        await page.set_content(_html(event_name, amount_str, method), wait_until="networkidle")
        await page.screenshot(
            path=str(out),
            clip={"x": 0, "y": 0, "width": 800, "height": 418},
        )
        await browser.close()

    logger.info("receipt_image | saved %s", out)
    return out
