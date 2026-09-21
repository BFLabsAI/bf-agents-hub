"""
HTML → PDF renderer for iTarget receipts.

Receipts come from the iTarget backend as an HTML page (print-oriented). We
download the page, inject a small @page rule so the content fits an A4 sheet,
and hand the result to weasyprint.

Kept isolated from the tools/agent code so the heavy weasyprint import only
happens when a receipt is actually requested.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("italo.pdf")

# The iTarget receipt page 403s without a browser UA.
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Default A4 has ~2cm margins → container width (21cm) overflows. Drop the
# margin and cap the container a bit below page width.
_PRINT_CSS = """
@page { size: A4; margin: 0.6cm; }
html, body { margin: 0; padding: 0; }
.geral { width: 19.5cm !important; margin: 0 auto !important; }
"""


async def render_receipt_pdf(receipt_url: str, *, timeout: float = 30.0) -> bytes:
    """Download the receipt HTML and convert to a PDF byte string."""
    from weasyprint import HTML, CSS  # lazy import — heavy

    async with httpx.AsyncClient(
        follow_redirects=True,
        headers={"User-Agent": _UA},
        timeout=timeout,
    ) as client:
        resp = await client.get(receipt_url)
        resp.raise_for_status()
        html = resp.text

    logger.info("pdf_renderer | url=%s html_bytes=%d", receipt_url[:80], len(html))
    pdf = HTML(string=html, base_url=receipt_url).write_pdf(
        stylesheets=[CSS(string=_PRINT_CSS)]
    )
    logger.info("pdf_renderer | pdf_bytes=%d", len(pdf))
    return pdf
