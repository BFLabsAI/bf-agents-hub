import logging

import httpx
from app.config import WABA_PHONE_NUMBER_ID, WABA_ACCESS_TOKEN

GRAPH_API_URL = "https://graph.facebook.com/v23.0"
logger = logging.getLogger("italo.waba")


class WABAClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=GRAPH_API_URL,
            headers={
                "Authorization": f"Bearer {WABA_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def _post(self, payload: dict) -> dict:
        resp = await self._client.post(f"/{WABA_PHONE_NUMBER_ID}/messages", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def send_text(
        self,
        to: str,
        body: str,
        reply_to_wamid: str | None = None,
    ) -> dict:
        payload: dict = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": body, "preview_url": False},
        }
        if reply_to_wamid:
            payload["context"] = {"message_id": reply_to_wamid}
        return await self._post(payload)

    async def send_event_carousel(
        self,
        to: str,
        intro_text: str,
        events: list[dict],
        default_image: str = "https://icongresso-x.s3-sa-east-1.amazonaws.com/demo/activity-scheduling/176347561341102.png",
    ) -> dict:
        """
        Send a WhatsApp interactive carousel (2+ events).

        Each event in `events`:
        {
            "activity_schedule_id": int,
            "title": str,
            "subtitle": str,   # location or type (optional)
            "image_url": str,  # falls back to default_image
        }

        Button ID convention: info_{activityScheduleId}
        Clicking → button_reply.id = "info_{activityScheduleId}"

        Format confirmed from n8n workflow (v23.0):
          interactive.type = "carousel"
          interactive.action.cards[].type = "BUTTON"
          buttons[].type = "quick_reply" (carousel-specific — NOT "reply")
        """
        cards = []
        for i, e in enumerate(events[:10]):
            aid = e["activity_schedule_id"]
            title = e.get("title") or "Evento"
            body_text = f"*{title}*"
            if e.get("subtitle"):
                body_text += f"\n{e['subtitle']}"
            image_url = e.get("image_url") or default_image

            cards.append({
                "card_index": i,
                "type": "BUTTON",
                "header": {
                    "type": "image",
                    "image": {"link": image_url},
                },
                "body": {"text": body_text[:160]},
                "action": {
                    "buttons": [
                        {
                            "type": "quick_reply",
                            "quick_reply": {
                                "id": f"info_{aid}",
                                "title": "Mais informações",
                            },
                        },
                        {
                            "type": "quick_reply",
                            "quick_reply": {
                                "id": f"sub_{aid}",
                                "title": "Inscrever-se",
                            },
                        },
                    ]
                },
            })

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "carousel",
                "body": {"text": intro_text},
                "action": {"cards": cards},
            },
        }
        logger.info("send_event_carousel | to=%s cards=%d payload=%s", to, len(cards), payload)
        return await self._post(payload)

    async def send_event_list(
        self,
        to: str,
        intro_text: str,
        events: list[dict],
    ) -> dict:
        """
        Send a WhatsApp interactive list message (fallback for 1 event, or no images).

        Each event in `events`:
        {
            "activity_schedule_id": int,
            "title": str,       # used as row description (max 72 chars)
            "type_activity": str,  # used as row title (max 24 chars)
        }

        Clicking a row → list_reply.id = "info_{activityScheduleId}"
        """
        rows = []
        for i, e in enumerate(events[:10]):
            aid = e["activity_schedule_id"]
            row_title = (e.get("type_activity") or e.get("title") or "Evento")[:24]
            row_desc = (e.get("title") or "")[:72]
            rows.append({
                "id": f"info_{aid}",
                "title": row_title,
                "description": row_desc,
            })

        header_text = "Evento disponível" if len(rows) == 1 else "Eventos Disponíveis"
        btn_text = "Ver evento" if len(rows) == 1 else "Ver eventos"

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "header": {"type": "text", "text": header_text},
                "body": {"text": intro_text},
                "footer": {"text": "Clique para ver detalhes ou se inscrever"},
                "action": {
                    "button": btn_text,
                    "sections": [{"title": "Disponíveis", "rows": rows}],
                },
            },
        }
        logger.info("send_event_list | to=%s events=%d", to, len(rows))
        return await self._post(payload)

    async def send_pix_order_details(
        self,
        to: str,
        pix_copy_paste: str,
        amount_cents: int,
        event_title: str = "Pagamento",
        merchant_name: str = "iTarget",
        pix_key: str = "123e4567-e12b-12d1-a456-426655440000",
        pix_key_type: str = "EVP",
        extra_items: list[dict] | None = None,
        qr_code: str = "",  # noqa: ARG002 — accepted for API parity with WebPresenter
    ) -> dict:
        """
        Send PIX payment via Meta order_details interactive message.
        extra_items: list of {"description": str, "amount": float} for annuity fees.
        """
        order_items = [
            {
                "retailer_id": "item-1",
                "name": event_title[:60],
                "amount": {"value": amount_cents, "offset": 100},
                "quantity": 1,
            }
        ]
        total_cents = amount_cents
        for i, fee in enumerate(extra_items or []):
            fee_cents = int(float(fee.get("amount", 0)) * 100)
            if fee_cents > 0:
                order_items.append({
                    "retailer_id": f"item-{i + 2}",
                    "name": (fee.get("description") or f"Anuidade {i + 1}")[:60],
                    "amount": {"value": fee_cents, "offset": 100},
                    "quantity": 1,
                })
                total_cents += fee_cents

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "order_details",
                "body": {"text": "Detalhes do seu pagamento"},
                "action": {
                    "name": "review_and_pay",
                    "parameters": {
                        "reference_id": f"itarget-{int(__import__('time').time())}",
                        "type": "digital-goods",
                        "payment_type": "br",
                        "payment_settings": [
                            {
                                "type": "pix_dynamic_code",
                                "pix_dynamic_code": {
                                    "code": pix_copy_paste,
                                    "merchant_name": merchant_name,
                                    "key": pix_key,
                                    "key_type": pix_key_type,
                                },
                            }
                        ],
                        "currency": "BRL",
                        "total_amount": {"value": total_cents, "offset": 100},
                        "order": {
                            "status": "pending",
                            "items": order_items,
                            "subtotal": {"value": total_cents, "offset": 100},
                        },
                    },
                },
            },
        }
        logger.info("send_pix_order_details | to=%s total_cents=%d", to, total_cents)
        return await self._post(payload)

    async def send_boleto_document(self, to: str, pdf_url: str) -> dict:
        """Send boleto PDF as a WhatsApp document message."""
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "document",
            "document": {
                "link": pdf_url,
                "caption": "Segue seu boleto em PDF ⬆️\n\nLogo abaixo envio a linha digitável para você copiar e colar.",
                "filename": "boleto.pdf",
            },
        }
        logger.info("send_boleto_document | to=%s", to)
        return await self._post(payload)

    async def send_boleto_order_details(
        self,
        to: str,
        digitable_line: str,
        amount_cents: int,
        event_title: str = "Pagamento",
    ) -> dict:
        """Send boleto digitável line via Meta order_details interactive message."""
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "order_details",
                "body": {"text": "Detalhes do seu pagamento"},
                "action": {
                    "name": "review_and_pay",
                    "parameters": {
                        "reference_id": f"itarget-boleto-{int(__import__('time').time())}",
                        "type": "digital-goods",
                        "payment_type": "br",
                        "payment_settings": [
                            {
                                "type": "boleto",
                                "boleto": {"digitable_line": digitable_line},
                            }
                        ],
                        "currency": "BRL",
                        "total_amount": {"value": amount_cents, "offset": 100},
                        "order": {
                            "status": "pending",
                            "tax": {"value": 0, "offset": 100, "description": "boleto"},
                            "items": [
                                {
                                    "retailer_id": "item-1",
                                    "name": event_title[:60],
                                    "amount": {"value": amount_cents, "offset": 100},
                                    "quantity": 1,
                                }
                            ],
                            "subtotal": {"value": amount_cents, "offset": 100},
                        },
                    },
                },
            },
        }
        logger.info("send_boleto_order_details | to=%s digitable_line=***", to)
        return await self._post(payload)

    async def upload_media(
        self,
        file_bytes: bytes,
        filename: str,
        mime_type: str = "application/pdf",
    ) -> str:
        """
        POST /{phone_id}/media (multipart). Returns Meta's media_id.
        Uses a fresh AsyncClient because the base client has Content-Type:
        application/json in its defaults, which breaks multipart uploads.
        """
        files = {
            "file": (filename, file_bytes, mime_type),
            "type": (None, mime_type),
            "messaging_product": (None, "whatsapp"),
        }
        async with httpx.AsyncClient(
            base_url=GRAPH_API_URL,
            headers={"Authorization": f"Bearer {WABA_ACCESS_TOKEN}"},
            timeout=60.0,
        ) as client:
            resp = await client.post(f"/{WABA_PHONE_NUMBER_ID}/media", files=files)
        resp.raise_for_status()
        media_id = resp.json()["id"]
        logger.info("upload_media | filename=%s media_id=%s bytes=%d",
                    filename, media_id, len(file_bytes))
        return media_id

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        """
        Download media binary from Meta by media_id. Two-step:
          1. GET /{media_id} → returns temporary URL (5 min TTL) + mime_type
          2. GET <that URL> with Bearer → returns the binary
        Returns (bytes, mime_type).
        """
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {WABA_ACCESS_TOKEN}"},
            timeout=60.0,
        ) as client:
            meta_resp = await client.get(f"{GRAPH_API_URL}/{media_id}")
            meta_resp.raise_for_status()
            meta = meta_resp.json()
            url = meta["url"]
            mime_type = meta.get("mime_type", "application/octet-stream")

            bin_resp = await client.get(url)
            bin_resp.raise_for_status()
            data = bin_resp.content

        logger.info("download_media | media_id=%s mime=%s bytes=%d", media_id, mime_type, len(data))
        return data, mime_type

    async def send_document_from_bytes(
        self,
        to: str,
        pdf_bytes: bytes,
        filename: str,
        caption: str = "",
    ) -> dict:
        """Upload binary to Meta and send as a document message."""
        media_id = await self.upload_media(pdf_bytes, filename, "application/pdf")
        document: dict = {"id": media_id, "filename": filename}
        if caption:
            document["caption"] = caption
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "document",
            "document": document,
        }
        logger.info("send_document_from_bytes | to=%s filename=%s", to, filename)
        return await self._post(payload)

    async def send_payment_confirmation_button(
        self,
        to: str,
        body_text: str,
        image_media_id: str,
        account_receive_id: int,
    ) -> dict:
        """
        Send an interactive button message with an image header and an 'Emitir Recibo' button.
        image_media_id: Meta media_id from upload_media().
        """
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "header": {
                    "type": "image",
                    "image": {"id": image_media_id},
                },
                "body": {"text": body_text},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {
                                "id": f"receipt_{account_receive_id}",
                                "title": "Emitir Recibo",
                            },
                        }
                    ]
                },
            },
        }
        logger.info("send_payment_confirmation_button | to=%s ar_id=%s", to, account_receive_id)
        return await self._post(payload)

    async def send_template(
        self,
        to: str,
        template_name: str,
        language: str = "pt_BR",
        components: list[dict] | None = None,
    ) -> dict:
        payload: dict = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
            },
        }
        if components:
            payload["template"]["components"] = components
        logger.info("send_template | to=%s template=%s lang=%s", to, template_name, language)
        return await self._post(payload)

    async def mark_as_read(self, wamid: str) -> None:
        """Mark message as read and show typing indicator simultaneously."""
        try:
            await self._client.post(
                f"/{WABA_PHONE_NUMBER_ID}/messages",
                json={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": wamid,
                    "typing_indicator": {"type": "text"},
                },
            )
        except Exception:
            pass

    async def send_typing(self, wamid: str) -> None:
        """Refresh the typing indicator (keeps the '...' visible during long runs)."""
        try:
            await self._client.post(
                f"/{WABA_PHONE_NUMBER_ID}/messages",
                json={
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": wamid,
                    "typing_indicator": {"type": "text"},
                },
            )
        except Exception:
            pass

    async def aclose(self) -> None:
        await self._client.aclose()
