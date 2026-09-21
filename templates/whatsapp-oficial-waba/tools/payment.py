"""
TEMPLATE EXAMPLE — Adapt endpoints, field names, and business logic to your client's API.

Demonstrates: complete payment flow — discover methods, generate PIX, generate bank slip (boleto),
retrieve PDF, and handle WhatsApp interactive message delivery with fallback to plain text.

Patterns shown:
  - list_payment_methods: fetch available methods + cart totals; handle card-only edge case
  - process_pix_payment: generate PIX → send interactive WhatsApp message → graceful fallback to text
  - process_bank_payment: generate boleto → send PDF document + interactive digitável line via WABA
  - _extract_pdf_from_html: scrape PDF URL from gateway HTML redirect page (Vindi/Yapay pattern)
  - print_bank_payment: retrieve PDF URL by token (fallback when process_bank_payment didn't deliver it)
  - sent_via_whatsapp flag: tells the LLM not to repeat payment codes in text when already sent
  - pglog.record: conversion tracking for every payment generation — adapt to your analytics
"""
import asyncio
import json
from urllib.parse import parse_qs, urlparse

import httpx
from agno.tools import tool

import app.error_log as error_log
import app.payment_generated_log as pglog
from app.config import PIX_EXPIRATION_SECONDS, PIX_KEY, PIX_KEY_TYPE, PIX_MERCHANT_NAME
from app.context import SessionContext
from app.client_api import ClientAPI


def build_payment_tools(ctx: SessionContext, api: ClientAPI, waba_client=None) -> list:

    @tool
    async def list_payment_methods() -> str:
        """
        Discover which payment methods are available (PIX, Boleto, Card).
        Always call after create_subscription or when user is already subscribed.
        Only present the methods returned in availableLabels.
        Never assume a method is available without calling this first.
        """
        try:
            if not ctx.account_receive_ids:
                return json.dumps({"status": "error", "message": "Nenhuma inscrição selecionada para pagamento. Selecione uma inscrição primeiro."})
            if not ctx.cost_center_id:
                return json.dumps({"status": "error", "message": "Centro de custo não identificado. Chame select_pending_payments ou check_existing_subscription novamente.", "nextAction": "retry_subscription_flow"})
            methods = await api.list_payment_methods(ctx.account_receive_ids, ctx.cost_center_id)
            ctx.set_available_payment_methods(methods)

            pix = next((m for m in methods if m.get("type") == "pix"), None)
            bank = next((m for m in methods if m.get("type") == "bankPayment"), None)
            card = next((m for m in methods if m.get("type") == "card"), None)

            checkout_url = ctx.card_payment_url() if card else None

            # No PIX/boleto → card-only
            if card and not pix and not bank:
                if not checkout_url:
                    return json.dumps({
                        "status": "card_only_no_link",
                        "message": (
                            "Apenas cartão disponível, mas não foi possível gerar o link autenticado "
                            "pois o cadastro deste usuário não possui hashLink configurado. "
                            "Oriente o usuário a acessar o site ou app para fazer login manualmente "  # TODO: replace with your platform's name/URL
                            "e finalizar o pagamento por lá."
                        ),
                        "nextActionHint": (
                            "Informe ao usuário que o pagamento por cartão precisa ser feito diretamente "
                            "no site/app (login manual). Não invente nem envie nenhuma URL."  # TODO: replace with your platform's name
                        ),
                    })
                return json.dumps({
                    "status": "redirect_to_site",
                    "checkoutUrl": checkout_url,
                    "nextAction": "redirect_to_checkout",
                    "nextActionHint": (
                        f"Pagamento disponível apenas por cartão. Envie o checkoutUrl ao usuário: {checkout_url}"
                    ),
                })

            labels = []
            if pix:
                labels.append("PIX")
            if bank:
                labels.append("Boleto")
            if card:
                labels.append("Cartão")

            # Gate: no methods available
            if not labels:
                return json.dumps({
                    "status": "no_methods_available",
                    "nextActionHint": (
                        "Nenhum método de pagamento disponível para esta inscrição. "
                        "Informe ao usuário e oriente a entrar em contato com o suporte para regularizar."  # TODO: replace with your support contact
                    ),
                })

            # Busca valor final com desconto via /subscribe/cart
            # Sempre limpa valores anteriores para evitar stale state
            ctx.set_cart_amounts(total=0, total_with_discount=0, discount=0)
            cart_total_with_discount: float | None = None
            cart_discount: float = 0.0
            try:
                origin = 5 if ctx.annuity_account_receive_ids else 2
                cart_data = await api.get_subscription_cart(
                    account_receive_ids=ctx.account_receive_ids,
                    origin_inscription=origin,
                )
                # O cart retorna todos os itens vinculados (evento + anuidades).
                # Se o check_existing_subscription só trouxe o ar_id do evento,
                # o cart completa a lista com os ar_ids das anuidades.
                cart_ar_ids = [
                    int(item["accountReceiveId"])
                    for item in cart_data.get("items", [])
                    if item.get("accountReceiveId")
                ]
                if cart_ar_ids and set(cart_ar_ids) != set(ctx.account_receive_ids):
                    ctx.set_account_receive_ids(cart_ar_ids)
                    # Re-busca métodos com ar_ids completos
                    methods = await api.list_payment_methods(ctx.account_receive_ids, ctx.cost_center_id)
                    ctx.set_available_payment_methods(methods)
                    pix  = next((m for m in methods if m.get("type") == "pix"), None)
                    bank = next((m for m in methods if m.get("type") == "bankPayment"), None)
                    card = next((m for m in methods if m.get("type") == "card"), None)
                    checkout_url = ctx.card_payment_url() if card else None

                cart_summary = cart_data.get("summary", {})
                raw_total = cart_summary.get("totalWithDiscount")
                raw_discount = cart_summary.get("discount")
                if raw_total is not None and float(raw_total) > 0:
                    cart_total_with_discount = float(raw_total)
                    cart_discount = float(raw_discount or 0)
                    ctx.set_cart_amounts(
                        total=float(cart_summary.get("total") or 0),
                        total_with_discount=cart_total_with_discount,
                        discount=cart_discount,
                    )
            except Exception:
                pass  # não bloqueia o fluxo se o cart falhar

            def _fmt(v: float) -> str:
                return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

            if cart_total_with_discount and cart_discount > 0:
                value_hint = (
                    f"Informe que já garantiu um desconto de {_fmt(cart_discount)} "
                    f"e que o valor final ficou {_fmt(cart_total_with_discount)}. "
                )
            elif cart_total_with_discount:
                value_hint = f"O valor total é {_fmt(cart_total_with_discount)}. "
            else:
                value_hint = ""

            resp_data: dict = {
                "status": "success",
                "availableLabels": labels,
                "checkoutUrl": checkout_url,
                "nextAction": "ask_payment_method",
                "nextActionHint": (
                    value_hint +
                    "Pergunte a forma de pagamento listada em availableLabels. "
                    "Se o usuário escolher Cartão, envie o checkoutUrl diretamente — não chame nenhuma outra ferramenta. "
                    "Se escolher PIX, chame process_pix_payment. "
                    "Se escolher Boleto, chame process_bank_payment."
                ),
            }
            # Só inclui totalWithDiscount e discount se foram obtidos com sucesso
            if cart_total_with_discount:
                resp_data["totalWithDiscount"] = cart_total_with_discount
                resp_data["discount"] = cart_discount
            return json.dumps(resp_data)
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 403:
                ctx.set_access_token("")
                return json.dumps({
                    "status": "token_expired",
                    "nextActionHint": (
                        "Token expirado. Chame identify_by_phone() IMEDIATAMENTE "
                        "para re-autenticar e depois repita list_payment_methods."
                    ),
                })
            error_log.record(ctx.from_number, ctx.from_number, "list_payment_methods", exc,
                             {"account_receive_ids": ctx.account_receive_ids, "cost_center_id": ctx.cost_center_id})
            return json.dumps({
                "status": "error",
                "message": str(exc),
                "nextActionHint": (
                    "Não conseguiu carregar os métodos de pagamento. "
                    "Informe ao usuário que houve uma instabilidade momentânea e pergunte se deseja tentar novamente. "
                    "NÃO sugira nenhum link, URL ou checkout — você não tem essa informação. "
                    "NÃO invente métodos disponíveis. Aguarde o usuário confirmar para então re-chamar list_payment_methods."
                ),
            })

    async def _fresh_payment_methods() -> list:
        """Always fetch fresh payment methods from API — never trust session cache."""
        methods = await api.list_payment_methods(ctx.account_receive_ids, ctx.cost_center_id)
        ctx.set_available_payment_methods(methods)
        return methods

    @tool
    async def process_pix_payment() -> str:
        """
        Generate PIX payment. Sends an interactive WhatsApp message with copy-paste button directly to the user.
        After calling this tool, confirm to the user that the PIX was sent — do NOT include the code in text.
        """
        try:
            methods = await _fresh_payment_methods()
            pix_method = next((m for m in methods if m.get("type") == "pix"), None)
            if not pix_method:
                return json.dumps({"status": "error", "message": "PIX não disponível."})

            payment = await api.process_pix_payment(
                account_receive_ids=ctx.account_receive_ids,
                gateway_id=pix_method.get("gatewayId") or pix_method.get("id"),
                expires_in=PIX_EXPIRATION_SECONDS,
            )

            pix_copy_paste = payment.get("pixCopyPaste") or payment.get("copyPaste") or ""
            qr_code = payment.get("qrCode") or payment.get("qr_code") or ""

            # Record every PIX generation — including retries — for conversion tracking.
            pglog.record(
                person_id=ctx.person_id,
                person_name=ctx.first_name or "",
                event_title=ctx.event_title or "",
                account_receive_ids=ctx.account_receive_ids or [],
                method="pix",
                amount=float(payment.get("amount") or ctx.event_amount or 0),
                gateway_ref=pix_copy_paste[:80],
                session_id=ctx.session_id,
            )

            if waba_client and pix_copy_paste:
                include_annuity = bool(ctx.pending_membership_details)
                extra_items = ctx.pending_membership_details if include_annuity else None
                # amount_cents = só o valor do evento.
                # send_pix_order_details soma event + cada extra_item → total correto.
                # Se passar o total já consolidado + extra_items, a anuidade duplica.
                if extra_items:
                    amount_cents = int(ctx.event_amount * 100)
                else:
                    amount_cents = int(float(ctx.cart_total_with_discount or payment.get("amount") or ctx.event_amount or 0) * 100)
                try:
                    await waba_client.send_pix_order_details(
                        to=ctx.from_number,
                        pix_copy_paste=pix_copy_paste,
                        amount_cents=amount_cents,
                        event_title=ctx.event_title,
                        merchant_name=PIX_MERCHANT_NAME,
                        pix_key=PIX_KEY,
                        pix_key_type=PIX_KEY_TYPE,
                        extra_items=extra_items,
                        qr_code=qr_code,
                    )
                    return json.dumps({
                        "status": "success",
                        "sent_via_whatsapp": True,
                        "nextActionHint": (
                            "O PIX interativo foi enviado diretamente ao usuário via WhatsApp. "
                            "Confirme em UMA frase curta (ex: 'PIX enviado! Válido por 1 hora.') "
                            "e pergunte se precisa de mais alguma coisa."
                        ),
                    })
                except Exception as waba_exc:
                    # Template failed but PIX exists — fallback to plain text
                    error_log.record(ctx.from_number, ctx.from_number, "process_pix_payment",
                                     waba_exc, {"fallback": "text_plain"}, error_type="waba_send_failed")
                    valor_fmt = f"R$ {amount_cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                    try:
                        await waba_client.send_text(
                            to=ctx.from_number,
                            body=(
                                f"Segue o código PIX para pagamento de {valor_fmt}:\n\n"
                                f"`{pix_copy_paste}`\n\n"
                                "Copie o código acima e cole no seu banco. Válido por 1 hora."
                            ),
                        )
                        return json.dumps({
                            "status": "success",
                            "sent_via_whatsapp": True,
                            "delivery_method": "text_fallback",
                            "nextActionHint": (
                                "O código PIX foi enviado como texto (o envio interativo falhou). "
                                "Confirme que o código foi enviado e peça para copiar e colar no banco."
                            ),
                        })
                    except Exception:
                        # Even plain text failed — return code for LLM to include in reply
                        return json.dumps({
                            "status": "success",
                            "sent_via_whatsapp": False,
                            "pixCopyPaste": pix_copy_paste,
                            "amount": amount_cents / 100,
                            "nextActionHint": (
                                "Não foi possível enviar via WhatsApp. "
                                "Inclua o código PIX na sua resposta de texto para o usuário copiar."
                            ),
                        })

            # Fallback — no waba_client: return code for agent to include in text
            return json.dumps({
                "status": "success",
                "pixCopyPaste": pix_copy_paste,
                "qrCode": payment.get("qrCode") or payment.get("qr_code"),
            })
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 403:
                ctx.set_access_token("")
                return json.dumps({
                    "status": "token_expired",
                    "nextActionHint": (
                        "Token expirado. Chame identify_by_phone() IMEDIATAMENTE "
                        "para re-autenticar e depois repita process_pix_payment."
                    ),
                })
            error_log.record(ctx.from_number, ctx.from_number, "process_pix_payment", exc,
                             {"account_receive_ids": ctx.account_receive_ids})
            return json.dumps({"status": "error", "message": str(exc),
                               "nextActionHint": "O PIX não foi gerado ou não foi enviado. NÃO confirme que o PIX foi enviado. Informe e ofereça tentar novamente ou outro método."})

    @tool
    async def process_bank_payment() -> str:
        """
        Generate bank slip (boleto). Sends PDF document + interactive digitável line directly via WhatsApp.
        After calling this tool, confirm to the user that the boleto was sent — do NOT include codes in text.
        """
        try:
            methods = await _fresh_payment_methods()
            bank_method = next((m for m in methods if m.get("type") == "bankPayment"), None)
            if not bank_method:
                return json.dumps({"status": "error", "message": "Boleto não disponível."})

            payment = await api.process_bank_payment(
                account_receive_ids=ctx.account_receive_ids,
                gateway_id=bank_method.get("gatewayId") or bank_method.get("id"),
            )

            # typeable_barcode extracted by ClientAPI from gatewayResponse
            typeable_barcode = payment.get("_typeable_barcode") or payment.get("digitableLine") or ""
            amount_cents = int(float(ctx.cart_total_with_discount or payment.get("amount") or ctx.event_amount or 0) * 100)

            # Record every boleto generation for conversion tracking.
            pglog.record(
                person_id=ctx.person_id,
                person_name=ctx.first_name or "",
                event_title=ctx.event_title or "",
                account_receive_ids=ctx.account_receive_ids or [],
                method="boleto",
                amount=amount_cents / 100,
                gateway_ref=typeable_barcode[:80],
                session_id=ctx.session_id,
            )

            # Get print token from response url field
            token = payment.get("printToken") or payment.get("print_token", "")
            if not token and payment.get("url"):
                qs = parse_qs(urlparse(payment["url"]).query)
                token = (qs.get("token") or [""])[0]
            if token:
                ctx.set_print_token(token)

            # Get PDF URL
            pdf_url: str | None = None
            if token:
                try:
                    pdf_data = await api.print_bank_payment(token)
                    inner = pdf_data.get("data", pdf_data) if isinstance(pdf_data, dict) else pdf_data
                    pdf_url = inner.get("url") or inner.get("pdfUrl") or inner.get("pdf_url")
                    # If print endpoint returns HTML redirect, extract PDF URL from it
                    if isinstance(pdf_url, str) and not pdf_url.endswith(".pdf"):
                        pdf_url = None
                except Exception:
                    pass

            # Fallback: charges[0].print_url from gatewayResponse (Vindi/Yapay HTML page)
            if not pdf_url:
                yapay_url = payment.get("_charges_print_url")
                if yapay_url:
                    pdf_url = await _extract_pdf_from_html(yapay_url)

            if waba_client:
                # Send PDF document first (failure is non-fatal)
                if pdf_url:
                    try:
                        await waba_client.send_boleto_document(to=ctx.from_number, pdf_url=pdf_url)
                        await asyncio.sleep(3)
                    except Exception as pdf_exc:
                        error_log.record(ctx.from_number, ctx.from_number, "process_bank_payment",
                                         pdf_exc, {"pdf_url": pdf_url}, error_type="waba_send_failed")

                # Send interactive digitável line
                if typeable_barcode:
                    try:
                        await waba_client.send_boleto_order_details(
                            to=ctx.from_number,
                            digitable_line=typeable_barcode,
                            amount_cents=amount_cents,
                            event_title=ctx.event_title,
                        )
                        return json.dumps({
                            "status": "success",
                            "sent_via_whatsapp": True,
                            "pdf_sent": bool(pdf_url),
                            "nextActionHint": (
                                "O boleto foi enviado ao usuário via WhatsApp"
                                + (" com PDF e linha digitável interativa." if pdf_url else " com linha digitável interativa (sem PDF).")
                                + " Confirme em UMA frase curta e pergunte se precisa de mais alguma coisa."
                            ),
                        })
                    except Exception as boleto_exc:
                        # Interactive template failed — fallback to plain text
                        error_log.record(ctx.from_number, ctx.from_number, "process_bank_payment",
                                         boleto_exc, {"fallback": "text_plain"}, error_type="waba_send_failed")
                        try:
                            await waba_client.send_text(
                                to=ctx.from_number,
                                body=(
                                    "Não consegui enviar o boleto interativo, mas aqui está a linha digitável:\n\n"
                                    f"`{typeable_barcode}`\n\n"
                                    "Copie e cole no seu banco para pagar."
                                ),
                            )
                            return json.dumps({
                                "status": "success",
                                "sent_via_whatsapp": True,
                                "delivery_method": "text_fallback",
                                "nextActionHint": (
                                    "A linha digitável do boleto foi enviada como texto (o envio interativo falhou). "
                                    "Confirme que foi enviada e peça para copiar e colar no banco."
                                ),
                            })
                        except Exception:
                            return json.dumps({
                                "status": "success",
                                "sent_via_whatsapp": False,
                                "digitableLine": typeable_barcode,
                                "nextActionHint": (
                                    "Não foi possível enviar via WhatsApp. "
                                    "Inclua a linha digitável na sua resposta de texto para o usuário copiar."
                                ),
                            })

                # Boleto generated but no digitável line yet (async processing)
                return json.dumps({
                    "status": "success",
                    "sent_via_whatsapp": bool(pdf_url),
                    "pdf_sent": bool(pdf_url),
                    "digitableLine": None,
                    "nextActionHint": (
                        "Boleto gerado. "
                        + ("PDF enviado ao usuário. " if pdf_url else "")
                        + "A linha digitável ainda não está disponível (processamento assíncrono). "
                        "Informe o usuário que o boleto está sendo processado e que o código chegará em instantes."
                    ),
                })

            # Fallback — no waba_client
            return json.dumps({
                "status": "success",
                "digitableLine": typeable_barcode or None,
                "pdfUrl": pdf_url,
                "printToken": token or None,
            })
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 403:
                ctx.set_access_token("")
                return json.dumps({
                    "status": "token_expired",
                    "nextActionHint": (
                        "Token expirado. Chame identify_by_phone() IMEDIATAMENTE "
                        "para re-autenticar e depois repita process_bank_payment."
                    ),
                })
            error_log.record(ctx.from_number, ctx.from_number, "process_bank_payment", exc,
                             {"account_receive_ids": ctx.account_receive_ids})
            return json.dumps({"status": "error", "message": str(exc),
                               "nextActionHint": "O boleto não foi gerado. Informe e ofereça PIX como alternativa."})

    async def _extract_pdf_from_html(url: str) -> str | None:
        """Fetch an HTML page and extract the PDF URL (same logic as n8n Code node)."""
        import re
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(url)
                html = resp.text
            match = re.search(r'window\.location\s*=\s*["\']([^"\']+\.pdf)["\']', html)
            if match:
                return match.group(1).replace("amp;", "")
            alt = re.search(r'["\']( https?://[^"\'\\s]+\.pdf)["\']', html)
            if alt:
                return alt.group(1).replace("amp;", "")
        except Exception:
            pass
        return None

    @tool
    async def print_bank_payment() -> str:
        """
        Get PDF URL for a boleto. Only call if process_bank_payment did not already send the boleto.
        """
        try:
            if not ctx.print_token:
                return json.dumps({"status": "token_required", "message": "Chame process_bank_payment primeiro."})
            data = await api.print_bank_payment(ctx.print_token)
            pdf = data.get("data", data) if isinstance(data, dict) else data
            return json.dumps({
                "status": "success",
                "pdfUrl": pdf.get("url") or pdf.get("pdfUrl"),
                "digitableLine": pdf.get("digitableLine") or pdf.get("digitable_line") or None,
            })
        except Exception as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 403:
                ctx.set_access_token("")
                return json.dumps({
                    "status": "token_expired",
                    "nextActionHint": (
                        "Token expirado. Chame identify_by_phone() IMEDIATAMENTE "
                        "para re-autenticar e depois repita print_bank_payment."
                    ),
                })
            error_log.record(ctx.from_number, ctx.from_number, "print_bank_payment", exc,
                             {"print_token": ctx.print_token})
            return json.dumps({"status": "error", "message": str(exc),
                               "nextActionHint": "Não conseguiu obter o PDF do boleto. Oriente o usuário a acessar o site para baixar."})  # TODO: replace with your platform's name

    return [list_payment_methods, process_pix_payment, process_bank_payment, print_bank_payment]
