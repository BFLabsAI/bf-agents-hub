import os
from dotenv import load_dotenv

load_dotenv()

# Client API
CLIENT_API_BASE_URL = os.getenv("CLIENT_API_BASE_URL", "https://api.yourdomain.com")
CLIENT_NAME = os.getenv("CLIENT_NAME", "default")
CLIENT_ENV = os.getenv("CLIENT_ENV", "")

# SSL verification — set to "false" for APIs with self-signed/mismatched certs
CLIENT_SSL_VERIFY = os.getenv("CLIENT_SSL_VERIFY", "true").lower() not in ("false", "0", "no")

# LLM — OpenRouter (OpenAI-compatible)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL_ID = os.getenv("OPENROUTER_MODEL_ID", "anthropic/claude-3-haiku")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# PostgreSQL Database & Session storage
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://user:password@localhost:5432/whatsapp_waba",
)
DB_SCHEMA = os.getenv("DB_SCHEMA", "public")
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "1"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
# Meta WhatsApp Business API
WABA_PHONE_NUMBER_ID = os.getenv("WABA_PHONE_NUMBER_ID", "")
WABA_ACCESS_TOKEN = os.getenv("WABA_ACCESS_TOKEN", "")
WABA_WEBHOOK_VERIFY_TOKEN = os.getenv("WABA_WEBHOOK_VERIFY_TOKEN", "")
WABA_APP_SECRET = os.getenv("WABA_APP_SECRET", "")
WABA_BUSINESS_ACCOUNT_ID = os.getenv("WABA_BUSINESS_ACCOUNT_ID", "")

# Payment confirmation webhook
PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET", "")

# Public base URL for serving generated images / links via WhatsApp
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")


def validate_config() -> None:
    required = {
        "WABA_PHONE_NUMBER_ID": WABA_PHONE_NUMBER_ID,
        "WABA_ACCESS_TOKEN": WABA_ACCESS_TOKEN,
        "WABA_WEBHOOK_VERIFY_TOKEN": WABA_WEBHOOK_VERIFY_TOKEN,
        "WABA_APP_SECRET": WABA_APP_SECRET,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
