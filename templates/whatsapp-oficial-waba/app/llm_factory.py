"""LLM Factory para o template WhatsApp Oficial.

Compatível com o 9Router (nosso gateway padrão) ou qualquer API compatível com OpenAI
(OpenAI direta, OpenRouter, Groq, local Ollama, vLLM, DeepSeek, etc.).
"""
from __future__ import annotations

from agno.models.openai import OpenAIChat
from app.config import NINEROUTER_API_KEY, NINEROUTER_BASE_URL, AGNO_DEFAULT_MODEL


def build_model(model_id: str | None = None) -> OpenAIChat:
    """Retorna uma instância OpenAIChat configurada para o 9Router ou provider OpenAI-compatible."""
    chosen_id = model_id or AGNO_DEFAULT_MODEL

    kwargs: dict = {
        "id": chosen_id,
        "api_key": NINEROUTER_API_KEY or "none",
        "max_tokens": None,
    }

    base_url = NINEROUTER_BASE_URL.strip() if NINEROUTER_BASE_URL else None
    if base_url:
        if not base_url.endswith("/v1") and not "/v1/" in base_url:
            base_url = f"{base_url.rstrip('/')}/v1"
        kwargs["base_url"] = base_url

    return OpenAIChat(**kwargs)
