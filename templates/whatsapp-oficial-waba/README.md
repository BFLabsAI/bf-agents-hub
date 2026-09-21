# agno-waba-template

Production-ready WhatsApp agent template built with the [Agno AI framework](https://github.com/agno-agi/agno) and the official Meta WhatsApp Cloud API. Ships with a multi-user session pool (one Agno agent per phone number, persisted in SQLite), rich interactive messages (carousels, lists, PIX, boleto), media processing (audio transcription, image description, PDF extraction), operator handoff flows, and an admin dashboard with a real-time SSE stream — all in a single Python service you can run locally in seconds or deploy on any VPS with Docker.

---

## Tech Stack

| Component | Technology | Notes |
|---|---|---|
| AI Agent Framework | [Agno](https://github.com/agno-agi/agno) | Session persistence, tool orchestration, history |
| LLM Gateway | OpenRouter (OpenAI-compatible) | Works with any model: Claude, GPT-4, Grok, Gemini |
| Default Model | `anthropic/claude-3-haiku` | Configurable via `OPENROUTER_MODEL_ID` env var |
| Session Persistence | SQLite (via Agno `SqliteDb`) | No external database required |
| Web Framework | FastAPI + Uvicorn | Async, production-grade |
| Messaging Protocol | Meta WhatsApp Cloud API v23.0 | Official WABA — not a third-party gateway |
| HTTP Client | httpx (async) | Used by `WABAClient` and `ClientApi` |

---

## Architecture

```
User (WhatsApp)
     │ sends message
     ▼
Meta WABA Cloud ──────────────────────────────────────────────────────┐
     │ POST /webhooks/waba (HMAC-SHA256)                              │
     ▼                                                                │
app/whatsapp_api.py (FastAPI)                                         │
     │                                                                │
     ├─ MessageBuffer (debounce — waits N seconds for rapid messages) │
     │                                                                │
     ├─ MediaProcessor (audio→text, image→description, PDF→text)     │
     │    └─ via LLM (OpenRouter)                                     │
     │                                                                │
     └─ WABABridge                                                    │
          │                                                           │
          └─ AgentRuntime (session pool)                              │
               │ one Agent per phone number                           │
               ▼                                                      │
          Agno Agent.arun(message)                                    │
               │                                                      │
               ├─ Tools call ClientAPI (your REST API)               │
               │                                                      │
               └─ WABAClient ─────────────────────────────────────────┘
                    sends: text, carousel, list, image, document, PIX
```

**WABAClient** (`app/waba_client.py`): Sends all WhatsApp message types (text, interactive buttons/lists, carousels, images, documents, typing indicator) against the Meta Graph API v23.0.

**WABABridge + AgentRuntime** (`app/whatsapp_bridge.py`): Thread-safe session pool keyed by phone number. Creates one Agno Agent per user, lazily, persisting the session across restarts via SQLite.

**MessageBuffer** (`app/message_buffer.py`): Debounces rapid messages from the same user (e.g. a voice note followed immediately by a text) so the agent receives one combined input instead of two separate turns.

**SessionContext** (`app/context.py`): Typed property/setter wrapper over Agno's `session_state` dict (auto-persisted to SQLite after every agent run). All domain state — user identity, current flow step, API tokens — lives here. No manual persistence code needed.

**ClientApi** (`app/client_api.py`): Async httpx client that calls your external REST API. All tool functions call this. Replace the endpoint paths and authentication scheme with your own API.

**Admin Panel** (`/admin`): REST endpoints backed by `app/admin_router.py` plus a real-time SSE stream for monitoring live sessions, transaction logs, LLM usage, and payment confirmations.

---

## Quickstart

### Prerequisites

- Python 3.12+
- Docker + Docker Compose (for production)
- A Meta WhatsApp Business Account with a registered phone number
- An OpenRouter API key (free tier works for development)

### Step 1 — Clone and install

```bash
git clone https://github.com/BFLabsAI/agno-api-oficial.git
cd agno-api-oficial
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Step 2 — Configure .env

Edit `.env` and fill in the values below. The full reference is at the bottom of this document.

**LLM (required)**
```bash
OPENROUTER_API_KEY=sk-or-v1-...   # from openrouter.ai/keys
OPENROUTER_MODEL_ID=anthropic/claude-3-haiku
```

**Meta WhatsApp Cloud API (required for production)**
See "Getting WABA Credentials" below.
```bash
WABA_PHONE_NUMBER_ID=...
WABA_ACCESS_TOKEN=...
WABA_WEBHOOK_VERIFY_TOKEN=any-secret-string-you-choose
WABA_APP_SECRET=...
```

**Your Client API (replace with your own)**
```bash
CLIENT_API_BASE_URL=https://api.yourservice.com
CLIENT_NAME=yourservice
```

### Step 3 — Getting WABA Credentials

1. Go to [developers.facebook.com](https://developers.facebook.com) → **Create App** → **Business**
2. Add the **WhatsApp** product to the app
3. Go to **WhatsApp → API Setup**:
   - Copy **Phone Number ID** → `WABA_PHONE_NUMBER_ID`
   - Generate a **Permanent System User Token** → `WABA_ACCESS_TOKEN`
   - Copy **App Secret** from Basic Settings → `WABA_APP_SECRET`
4. Deploy your app (needs a public HTTPS URL)
5. Register the webhook: `https://your-domain.com/webhooks/waba`
   - Set your `WABA_WEBHOOK_VERIFY_TOKEN` as the Verify Token
   - Subscribe to: `messages`

### Step 4 — Run

**CLI (development — no WhatsApp or network required)**
```bash
python3 main.py
```
This starts an interactive terminal loop using the same agent that runs in production (`session_id = "agent-cli-dev"`, no `waba_client`). Tools that send WhatsApp messages fall back to plain text output.

**Docker (production)**
```bash
docker-compose up -d
# Service listens on port 8000 — put Caddy or nginx in front for HTTPS
```

**Caddy reverse proxy (example)**
```
your-domain.com {
    reverse_proxy localhost:8000
}
```

### Step 5 — Test

Send a WhatsApp message to your registered number, then check the logs:
```bash
docker-compose logs -f
```

---

## Project Structure

```
agno-api-oficial/
├── app/
│   ├── __init__.py
│   ├── admin_message_log.py      # In-memory log of admin-originated messages
│   ├── admin_router.py           # FastAPI router: admin dashboard REST + SSE stream
│   ├── agent_factory.py          # create_agent() — builds an Agno Agent with all tools
│   ├── auto_resume.py            # Resumes interrupted sessions after service restart
│   ├── client_api.py             # Async httpx client for your external REST API
│   ├── config.py                 # Reads and validates all environment variables
│   ├── context.py                # SessionContext — typed wrapper over session_state
│   ├── error_log.py              # Persists agent/tool errors to SQLite for review
│   ├── event_card_image.py       # Generates event card images for WhatsApp carousels
│   ├── llm_factory.py            # build_model() — creates OpenAIChat → OpenRouter
│   ├── llm_usage_log.py          # Tracks LLM token usage per session
│   ├── log_store.py              # In-memory + SQLite transaction log (LogStore)
│   ├── markdown_to_html.py       # Converts agent markdown to WhatsApp-safe HTML
│   ├── media_processor.py        # Transcribes audio, describes images, extracts PDFs
│   ├── message_buffer.py         # MessageBuffer — debounces rapid messages per user
│   ├── operator_notes_store.py   # Persists operator notes attached to sessions
│   ├── operators_store.py        # Registry of human operators for handoff routing
│   ├── pause_registry.py         # Pauses/resumes agent for a session (operator takeover)
│   ├── payment_generated_log.py  # Logs PIX/boleto payment requests and their status
│   ├── payment_webhook_log.py    # Logs incoming payment confirmation webhooks
│   ├── pdf_renderer.py           # Renders HTML payment receipts to PDF
│   ├── quick_replies_store.py    # Stores quick-reply templates for admin use
│   ├── receipt_cache.py          # Caches generated payment receipts to avoid duplicates
│   ├── receipt_image.py          # Generates payment receipt images
│   ├── transaction_log.py        # Data structures for message exchange log entries
│   ├── user_log.py               # Per-user activity log for audit and analytics
│   ├── waba_client.py            # WABAClient — sends all WhatsApp message types
│   ├── whatsapp_api.py           # FastAPI app — all HTTP endpoints and webhook handlers
│   └── whatsapp_bridge.py        # WABABridge + AgentRuntime session pool
├── tools/
│   ├── __init__.py
│   ├── category.py               # Category/professional-tier tools (list, register)
│   ├── events.py                 # Event discovery and detail tools (carousel/list)
│   ├── identity.py               # User identification tools (phone, document)
│   ├── payment.py                # Payment tools (PIX, boleto, card checkout)
│   ├── receipts.py               # Receipt retrieval and resend tools
│   └── subscription.py           # Subscription lifecycle tools (create, cancel, pay)
├── prompts/
│   └── agent_prompt.py           # build_system_prompt() — 7-block prompt skeleton
├── static/
│   └── admin.html                # Admin dashboard SPA (sessions, transactions, SSE)
├── main.py                       # CLI entry point for local interactive testing
├── Dockerfile                    # python:3.12-slim, installs requirements, exposes 8000
├── docker-compose.yml            # Single-service compose config with volume for SQLite
├── .env.example                  # Template for all required environment variables
├── requirements.txt              # agno, openai, httpx, fastapi, uvicorn, pydantic v2
├── sanity_test.py                # End-to-end sanity checks against a live agent
└── CLAUDE.md                     # Internal architecture notes (Agno + iTarget context)
```

---

## Customizing for Your Project

### 1. Name your agent

Edit `app/agent_factory.py`: change the `name`, `id`, and `session_id` prefix passed to the `Agent` constructor. The `session_id` is the SQLite key — choose a prefix that is unique to your project (e.g. `"mybot-wa-{number}"`).

### 2. Define your session state

Edit `app/context.py`: add, rename, or remove fields in `SessionContext` to match your domain. The file is well-commented with examples. Every property/setter pair writes directly to `self._s`, which is the same dict Agno serializes to SQLite after each run — no manual persistence code required.

Infrastructure fields to keep: `session_id`, `from_number`, `access_token`.

Replace domain fields (`activity_schedule_id`, `cost_center_id`, etc.) with your own concepts.

### 3. Connect your API

Edit `app/client_api.py`: replace the endpoint paths, authentication header logic, and response field names with your API's. The existing methods demonstrate the common patterns — bearer token auth, list endpoints, detail endpoints, create/update.

### 4. Write your tools

Each file in `tools/` exports a `build_*_tools(ctx, api)` factory function that returns a plain list of async functions. Agno introspects the function signature and docstring to build the tool schema for the LLM.

```python
from app.context import SessionContext
from app.client_api import ClientApi

def build_my_tools(ctx: SessionContext, api: ClientApi) -> list:

    async def my_tool(param: str) -> dict:
        """Tool description — the agent reads this to decide when to call it."""
        if not ctx.user_id:
            return {
                "status": "error",
                "message": "User not identified",
                "nextActionHint": "Call identify_user first",
            }

        result = await api.get_something(param)
        ctx.set_current_item(result["id"])  # persists to SQLite automatically

        return {
            "status": "success",
            "data": result,
            "nextActionHint": "Show the result to the user and ask what they want to do next.",
        }

    return [my_tool]
```

Key rules:
- **Always return `nextActionHint`** — explicit LLM guidance for the next step. This prevents reasoning regressions across model versions and keeps multi-step flows on track regardless of which model is in use.
- **Store relevant IDs and tokens in `ctx`** — Agno auto-persists to SQLite after each run. No explicit save calls needed.
- **Never trust cached context for live data** — always call the API for fresh state. See Bloco 7 in the prompt for enforcement rules.

### 5. Write your prompt

Edit `prompts/agent_prompt.py`. The file contains 7 commented blocks with `{PLACEHOLDER}` variables. Fill in:

| Block | What to fill in |
|---|---|
| **PERSONA** | Agent name, company, role, 2-3 personality adjectives |
| **Bloco 1** | Language register, tone, forbidden words, approved interjections |
| **Bloco 2** | WhatsApp formatting rules (`*bold*` not `**bold**`), emoji policy, message length |
| **Bloco 3** | Greeting rules (once per conversation, by first name only) |
| **Bloco 4** | Empathetic error handling, unsupported-action messages, support channel links |
| **Bloco 5** | Tool sequencing — define each business flow as explicit ordered steps |
| **Bloco 6** | Domain glossary — abbreviations and jargon mapped to tool calls |
| **Bloco 7** | Data freshness rules — which live data types must always come from the API |

> The `build_system_prompt()` function returns `{"description": ..., "instructions": [...]}`. Agno passes `description` and `instructions` directly to the `Agent` constructor — do not change the return signature.

### 6. Register your tools

In `app/agent_factory.py`, import your tool builder and add it to `all_tools`:

```python
from tools.my_module import build_my_tools

all_tools = [
    *build_identity_tools(ctx, api),
    *build_my_tools(ctx, api),      # add here
    ...
]
```

---

## Rich Messages

`WABAClient` (`app/waba_client.py`) can send every interactive message type supported by the Meta Cloud API:

```python
# Plain text
await waba.send_text(to="5511999999999", body="Hello!")

# Typing indicator (shows the "typing..." bubble)
await waba.send_typing(to="5511999999999")

# Interactive list (up to 10 rows, organized in sections)
await waba.send_list_message(
    to="5511999999999",
    header="Available Options",
    body="Select one:",
    button_text="See options",
    sections=[
        {
            "title": "Section A",
            "rows": [{"id": "opt_1", "title": "Option 1", "description": "Details"}],
        }
    ],
)

# Event carousel (2–10 cards, each with image, title, subtitle, and quick-reply buttons)
await waba.send_event_carousel(
    to="5511999999999",
    intro_text="Here are the upcoming events:",
    events=[
        {
            "activity_schedule_id": 42,
            "title": "Workshop: Advanced Python",
            "subtitle": "São Paulo — Nov 20",
            "image_url": "https://...",
        }
    ],
)

# Image
await waba.send_image(to="5511999999999", url="https://...", caption="Your receipt")

# Document
await waba.send_document(
    to="5511999999999",
    url="https://...",
    filename="boleto.pdf",
    caption="Your payment slip",
)
```

> Button IDs in the carousel follow the convention `info_{activityScheduleId}` and `sub_{activityScheduleId}`. The bridge (`app/whatsapp_bridge.py`) converts `button_reply` and `list_reply` clicks back into natural-language text before passing them to the agent.

---

## HTTP Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check — returns `{"status": "ok"}` |
| GET | `/webhooks/waba` | Meta webhook challenge verification (GET with `hub.challenge`) |
| POST | `/webhooks/waba` | Incoming WhatsApp messages — HMAC-SHA256 authenticated |
| POST | `/webhooks/payment-confirmed` | External payment confirmation — sends WhatsApp notification to user |
| GET | `/admin` | Admin dashboard HTML (`static/admin.html`) |
| GET | `/admin/sessions` | Active session list with metadata |
| GET | `/admin/sessions/{id}/messages` | Full conversation history for a session |
| GET | `/admin/transactions` | All message exchange log entries |
| GET | `/admin/payment-webhooks` | Log of received payment confirmation webhooks |
| GET | `/admin/stream` | SSE real-time event stream (sessions, messages, payments) |

**Webhook security**: All incoming Meta messages are verified via HMAC-SHA256 using `WABA_APP_SECRET`. Requests with a missing or invalid `X-Hub-Signature-256` header are rejected with HTTP 403.

**Payment webhook security**: The `POST /webhooks/payment-confirmed` endpoint checks the `X-Webhook-Secret` header against `PAYMENT_WEBHOOK_SECRET`. Leave this env var empty in development to disable the check.

**Admin security**: Admin routes have no built-in authentication by default. Restrict access at the reverse-proxy level (IP allowlist, HTTP basic auth) or implement the `require_auth` FastAPI dependency in `app/admin_router.py`.

---

## Session Commands

Users can send these commands directly in WhatsApp:

| Command | Effect |
|---|---|
| `/flush` | Deletes their session from SQLite and from the in-memory pool. The next message starts a fresh conversation. Useful for debugging broken state. |

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `CLIENT_API_BASE_URL` | Yes | `https://api.yourdomain.com` | Base URL of the external REST API your tools will call |
| `CLIENT_NAME` | No | `default` | Tenant/client identifier passed to the API (if multi-tenant) |
| `CLIENT_ENV` | No | _(empty)_ | Environment suffix appended to API paths (e.g. `.stg` for staging) |
| `CLIENT_SSL_VERIFY` | No | `true` | Set to `false` for APIs with self-signed or mismatched TLS certificates |
| `OPENROUTER_API_KEY` | Yes | — | OpenRouter API key — get one at [openrouter.ai/keys](https://openrouter.ai/keys) |
| `OPENROUTER_MODEL_ID` | No | `anthropic/claude-3-haiku` | Any model ID from [openrouter.ai/models](https://openrouter.ai/models) |
| `SESSION_DB_PATH` | No | `tmp/agent.db` | Path to the SQLite file for Agno session persistence |
| `WABA_PHONE_NUMBER_ID` | Yes | — | Meta Phone Number ID (from WhatsApp → API Setup in Meta Developer Console) |
| `WABA_ACCESS_TOKEN` | Yes | — | Meta access token — use a permanent System User token in production |
| `WABA_WEBHOOK_VERIFY_TOKEN` | Yes | — | Any secret string you choose; must match what you set in the Meta webhook config |
| `WABA_APP_SECRET` | Yes | — | App Secret from Meta Developer Console → App Settings → Basic — used for HMAC-SHA256 verification |
| `WABA_BUSINESS_ACCOUNT_ID` | No | — | WhatsApp Business Account ID — required for some advanced API calls |
| `PAYMENT_WEBHOOK_SECRET` | No | _(empty)_ | Shared secret for `POST /webhooks/payment-confirmed`. Leave empty to disable auth (dev only) |
| `PUBLIC_BASE_URL` | No | _(empty)_ | Public HTTPS URL of this service — used to build image/receipt links sent to users |

---

## Deployment

### Minimum Requirements

- 512 MB RAM, 1 vCPU
- Public HTTPS URL (required by Meta for webhook registration)
- Persistent storage for `./tmp/agent.db` (the SQLite session store)

### Docker Compose

The included `docker-compose.yml` mounts `./tmp` as a volume so the SQLite database survives container restarts:

```yaml
services:
  whatsapp-agent:
    build: .
    container_name: whatsapp-agent
    command: uvicorn app.whatsapp_api:app --host 0.0.0.0 --port 8000 --log-level info
    env_file: .env
    ports:
      - "8000:8000"
    volumes:
      - ./tmp:/app/tmp
    restart: unless-stopped
```

```bash
docker-compose up -d
docker-compose logs -f
```

### Caddy (recommended reverse proxy for HTTPS)

```
your-domain.com {
    reverse_proxy localhost:8000
}
```

### Scaling

- **Single instance**: SQLite works well for up to ~1,000 concurrent users. The in-memory session pool and per-number locks handle concurrent messages safely.
- **Multi-instance**: Replace SQLite with PostgreSQL by changing the `db=` parameter in `app/agent_factory.py` from `SqliteDb` to Agno's `PgDb`. The rest of the code is unchanged.

---

## License

MIT. Use freely for any project.
