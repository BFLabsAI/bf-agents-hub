"""Model factory agnóstico para o Agno Agent.

Suporta nativamente o 9Router (nosso proxy gateway de IA padrão) ou qualquer
provedor compatível com a API da OpenAI (OpenAI direta, OpenRouter, Groq,
Ollama, vLLM, DeepSeek, etc.).

Basta configurar no .env:
  NINEROUTER_BASE_URL= (ex: http://localhost:20128/v1 ou https://seu-proxy/v1)
  NINEROUTER_API_KEY= (sua chave, ou deixe vazio caso o proxy não exija auth)

Se não quiser usar o 9Router, basta apontar NINEROUTER_BASE_URL (ou OPENAI_BASE_URL)
e NINEROUTER_API_KEY (ou OPENAI_API_KEY) para a API OpenAI-compatible que preferir.
"""
from __future__ import annotations

import os
from agno.models.openai import OpenAIChat


def _resolve_llm_config() -> tuple[str | None, str | None]:
    """Resolve base_url e api_key a partir das variáveis de ambiente."""
    base_url = (
        os.getenv("NINEROUTER_BASE_URL")
        or os.getenv("NINEROUTER_URL")
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("LLM_BASE_URL")
        or None
    )
    if base_url:
        base_url = base_url.strip()
        # Garante sufixo /v1 se for informado apenas o host:porta
        if not base_url.endswith("/v1") and not "/v1/" in base_url:
            base_url = f"{base_url.rstrip('/')}/v1"

    api_key = (
        os.getenv("NINEROUTER_API_KEY")
        or os.getenv("NINEROUTER_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("LLM_API_KEY")
        or "none"
    ).strip()

    return base_url, api_key


def nine_router(model_id: str | None = None) -> OpenAIChat:
    """Retorna instância OpenAIChat apontando para o 9Router ou qualquer API OpenAI-compatible.

    Se NINEROUTER_BASE_URL não estiver configurado, a biblioteca usará o default da OpenAI.
    """
    base_url, api_key = _resolve_llm_config()
    chosen_model = model_id or os.getenv("AGNO_DEFAULT_MODEL", "openai/gpt-4o-mini")

    kwargs: dict = {
        "id": chosen_model,
        "api_key": api_key,
        "max_tokens": None,
    }
    if base_url:
        kwargs["base_url"] = base_url

    return OpenAIChat(**kwargs)


def nine_router_fast() -> OpenAIChat:
    """Modelo rápido para tarefas curtas ou debounce."""
    fast_model = os.getenv("AGNO_FAST_MODEL", "mimo/mimo-v2.5-pro")
    return nine_router(model_id=fast_model)


# Aliases para compatibilidade reversa com chamadas existentes
omniroute = nine_router
omniroute_fast = nine_router_fast
get_model = nine_router
