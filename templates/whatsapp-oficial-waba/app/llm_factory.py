from app.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL_ID


def build_model(model_id: str | None = None):
    """
    OpenRouter is OpenAI-compatible — use OpenAIChat with custom base_url and api_key.
    Model IDs: "anthropic/claude-3-haiku", "openai/gpt-4o-mini", "google/gemini-flash-1.5", etc.
    Browse models at: https://openrouter.ai/models
    """
    from agno.models.openai import OpenAIChat
    return OpenAIChat(
        id=model_id or OPENROUTER_MODEL_ID,
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
    )
