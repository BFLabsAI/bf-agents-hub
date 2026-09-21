"""
TEMPLATE EXAMPLE — Adapt endpoints, field names, and business logic to your client's API.

Demonstrates: fetching paid subscription receipts from the API, rendering them from HTML to PDF,
and delivering the PDF file via WhatsApp as a document message.

Patterns shown:
  - Filter paid subscriptions by linkReceipt presence and status
  - Keyword-based receipt selection (empty keyword → list options; multiple matches → ask to narrow)
  - HTML → PDF rendering via render_receipt_pdf (Playwright/WeasyPrint — see app/pdf_renderer.py)
  - WABA document delivery: upload bytes → send as PDF file in chat
  - sent_via_whatsapp=True flag: tell the LLM to confirm in one sentence, not repeat the filename

What to customize:
  - Replace linkReceipt, statusSubscriptionDescription field names with your API's schema
  - Replace _is_paid logic with your API's paid/complete status field
  - Replace render_receipt_pdf with your own PDF generation if your API provides direct PDF URLs
"""
from __future__ import annotations

import json
import logging
import re

from agno.tools import tool

from app.context import SessionContext
from app.client_api import ClientAPI
from app.pdf_renderer import render_receipt_pdf

logger = logging.getLogger(__name__)


def _title_case(s: str) -> str:
    if not s:
        return ""
    # Only convert strings that are mostly uppercase
    letters = [c for c in s if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / len(letters) < 0.7:
        return s
    parts = s.split()
    return " ".join(p.capitalize() for p in parts)


def _slugify(s: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", (s or "").lower(), flags=re.UNICODE)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return (s[:maxlen] or "recibo").strip("-")


def _is_paid(sub: dict) -> bool:
    """
    PATTERN: Determine if a subscription has a receipt available.
    A subscription has a receipt only when it is paid AND the API has populated linkReceipt.
    TODO: replace linkReceipt and statusSubscriptionDescription with your API's field names.
    TODO: replace "pendente", "cancelado", "em aberto" keywords with your API's pending/cancelled status values.
    """
    if not sub.get("linkReceipt"):  # TODO: replace with your API's receipt URL field name
        return False
    status = (sub.get("statusSubscriptionDescription") or "").lower()  # TODO: replace with your API's status field
    # Reject explicit pending / cancelled statuses
    bad = ("pendente", "cancelado", "em aberto")  # TODO: replace with your API's non-paid status keywords
    return not any(b in status for b in bad)


def build_receipt_tools(ctx: SessionContext, api: ClientAPI, waba_client=None) -> list:

    @tool
    async def send_receipt(title_keyword: str = "") -> str:
        """
        Envia o recibo em PDF de uma inscrição paga do usuário.

        Busca em my_subscriptions as inscrições com linkReceipt preenchido, filtra por
        title_keyword (case-insensitive, substring), renderiza o HTML do recibo em PDF
        e entrega o arquivo pelo canal ativo:
          - WhatsApp: upload em /media + send document (aparece como arquivo PDF na conversa)
          - Web: cacheia o PDF e retorna um card HTML com botão de download

        Args:
            title_keyword: termo para filtrar pelo título do evento/inscrição
              (ex: "sgee", "congresso sbot", "anuidade 2025"). Vazio = lista opções.
        """
        try:
            subs = await api.my_subscriptions()
        except Exception as exc:
            logger.error("send_receipt | my_subscriptions falhou: %s", exc)
            return json.dumps({"status": "error", "message": f"Erro ao consultar inscrições: {exc}"})

        paid = [s for s in subs if _is_paid(s)]
        if not paid:
            return json.dumps({
                "status": "no_receipts",
                "message": "Nenhum recibo disponível — só inscrições já pagas geram recibo.",
                "nextActionHint": "Informe o usuário que não há recibo disponível e pergunte se ele quer ver as pendências.",
            })

        if not title_keyword.strip():
            return json.dumps({
                "status": "needs_keyword",
                "available": [
                    {
                        "title": _title_case(s.get("title") or ""),
                        "createdAt": s.get("createdAt") or "",
                        "status": s.get("statusSubscriptionDescription") or "",
                    }
                    for s in paid
                ],
                "nextActionHint": (
                    "Apresente a lista de inscrições pagas ao usuário e pergunte de qual evento ele quer o recibo. "
                    "Depois chame send_receipt de novo com title_keyword preenchido."
                ),
            })

        kw = title_keyword.strip().lower()
        matches = [s for s in paid if kw in (s.get("title") or "").lower()]
        if not matches:
            return json.dumps({
                "status": "not_found",
                "message": f"Nenhum recibo encontrado para '{title_keyword}'.",
                "available": [_title_case(s.get("title") or "") for s in paid],
                "nextActionHint": "Liste os eventos disponíveis e peça para o usuário ser mais específico.",
            })

        if len(matches) > 3:
            return json.dumps({
                "status": "multiple_matches",
                "count": len(matches),
                "available": [_title_case(s.get("title") or "") for s in matches],
                "nextActionHint": "Peça para o usuário ser mais específico (ex: incluir o ano) para evitar mandar vários recibos.",
            })

        sent: list[dict] = []
        errors: list[dict] = []
        for sub in matches:
            title = _title_case(sub.get("title") or "recibo")
            url = sub["linkReceipt"]
            filename = f"recibo-{_slugify(sub.get('title') or 'evento')}.pdf"
            try:
                pdf = await render_receipt_pdf(url)
            except Exception as exc:
                logger.error("send_receipt | render %r falhou: %s", title, exc)
                errors.append({"title": title, "stage": "render", "error": str(exc)})
                continue

            if not waba_client:
                errors.append({"title": title, "stage": "delivery", "error": "Nenhum canal de entrega disponível."})
                continue

            try:
                await waba_client.send_document_from_bytes(
                    to=ctx.from_number or "",
                    pdf_bytes=pdf,
                    filename=filename,
                    caption=f"Recibo: {title}",
                )
                sent.append({"title": title, "filename": filename})
            except Exception as exc:
                logger.error("send_receipt | delivery %r falhou: %s", title, exc)
                errors.append({"title": title, "stage": "delivery", "error": str(exc)})

        if not sent:
            return json.dumps({"status": "error", "message": "Não consegui gerar o recibo.", "errors": errors})

        return json.dumps({
            "status": "success",
            "sent_via_whatsapp": True,  # idem sent_via_web — acknowledge in one short line
            "sent_count": len(sent),
            "sent": sent,
            "errors": errors,
            "nextActionHint": "Confirme em UMA frase curta que o(s) recibo(s) foi/foram enviado(s). Não repita o nome do arquivo em texto.",
        })

    return [send_receipt]
