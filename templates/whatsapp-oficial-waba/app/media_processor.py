"""
Multimodal preprocessor for WhatsApp media.

Converts audio / image / PDF into plain text using Gemini 2.5 Flash Lite via
OpenRouter. The resulting text is injected into the agent's input so the main
LLM (Grok) can reason over it without needing native multimodal support.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os

import httpx

from app.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL

logger = logging.getLogger("italo.media")

GEMINI_MODEL = os.getenv("GEMINI_PREPROCESSOR_MODEL", "google/gemini-2.5-flash-lite")
_TIMEOUT = 60.0

_PROMPTS = {
    "audio": "Transcreva integralmente este áudio em pt-BR. Responda APENAS com a transcrição, sem prefixos ou comentários.",
    "image": "Descreva o conteúdo desta imagem em 1-3 frases concisas em pt-BR. Se houver texto visível, transcreva-o. Responda direto, sem prefixos.",
    "document": "Resuma o conteúdo deste PDF em pt-BR de forma estruturada (até 250 palavras). Destaque dados-chave (valores, datas, nomes, números de inscrição/CPF) se houver. Responda direto, sem prefixos.",
}


async def process_media(data: bytes, mime_type: str, kind: str) -> str:
    """
    Send media bytes to Gemini and get back plain-text understanding.
    `kind` is one of: 'audio' | 'image' | 'document'.
    Returns the model's text output, or a fallback message on failure.
    """
    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY missing — cannot preprocess media")
        return "[mídia não pôde ser processada — chave da API ausente]"

    prompt = _PROMPTS.get(kind)
    if not prompt:
        return f"[tipo de mídia não suportado: {kind}]"

    b64 = base64.b64encode(data).decode("ascii")

    # OpenAI-compatible multimodal payload. OpenRouter accepts both
    # `image_url` and `file` shapes; for audio Gemini accepts data: URLs in image_url.
    if kind == "audio":
        content = [
            {"type": "text", "text": prompt},
            {"type": "input_audio", "input_audio": {"data": b64, "format": _audio_format(mime_type)}},
        ]
    elif kind == "image":
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
        ]
    else:  # document (PDF)
        content = [
            {"type": "text", "text": prompt},
            {"type": "file", "file": {"filename": "document.pdf", "file_data": f"data:{mime_type};base64,{b64}"}},
        ]

    payload = {
        "model": GEMINI_MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 600,
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data_json = resp.json()
        text = data_json["choices"][0]["message"]["content"].strip()
        logger.info("process_media | kind=%s mime=%s out_chars=%d", kind, mime_type, len(text))
        return text or f"[{kind} sem conteúdo legível]"
    except httpx.HTTPStatusError as e:
        body = e.response.text[:500] if e.response is not None else ""
        logger.error("process_media HTTP error | kind=%s status=%s body=%s", kind, e.response.status_code, body)
        return f"[falha ao processar {kind}: {e.response.status_code}]"
    except Exception as e:
        logger.exception("process_media unexpected error | kind=%s", kind)
        return f"[falha ao processar {kind}: {type(e).__name__}]"


def _audio_format(mime_type: str) -> str:
    """Map MIME type → format hint expected by Gemini."""
    mt = mime_type.lower()
    if "ogg" in mt or "opus" in mt:
        return "ogg"
    if "mp3" in mt or "mpeg" in mt:
        return "mp3"
    if "wav" in mt:
        return "wav"
    if "m4a" in mt or "mp4" in mt or "aac" in mt:
        return "m4a"
    return "ogg"  # WhatsApp default for voice notes
