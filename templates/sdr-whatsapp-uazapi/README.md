# SDR Template — generic, parametrizable WhatsApp SDR agent

A production-ready WhatsApp SDR (sales development rep) agent built with the
**Agno** framework (no AgentOS): plain async code + `AsyncPostgresDb` + FastAPI.
It qualifies inbound leads over WhatsApp, runs a multi-day follow-up cadence,
hands qualified leads to a human vendor (repasse), and mirrors state into
GoHighLevel (GHL).

This folder is a **template**. To stand up a new agent for a client, copy it,
fill in `config/` + `.env`, create the prefixed schema, and run it.

---

## What it does

- **Webhook ingest** (`POST /{SLUG}/webhook`): receives uazapi messages,
  filters out bot-sent / not-allowed / empty / duplicate messages, then
  **debounces** bursty WhatsApp messages (`MessageBuffer`) before running the
  combined text through the agent.
- **Multimodal**: inbound audio/image/video/document is transcribed/extracted by
  the **MiMo v2.5 regular** model (the non-`-pro` variant — `-pro` is not
  multimodal) and the extracted text is fed to the agent. No separate Whisper.
- **Qualification + tools**: the agent uses tools to save qualification fields,
  look up products, check business hours, classify lost leads, and request a
  handoff.
- **Follow-up cadence**: an asyncio dispatcher inside `app.py` pops due
  follow-ups and sends each **through `agent.arun()`** (so the sent message
  lands in Agno session history — the agent remembers what it sent), then logs
  it as a GHL note.
- **Daily cadence advance**: an APScheduler job inside `app.py` advances active,
  unpaused leads to the next `cadence_day`, enqueues that day's touches, mirrors
  the GHL stage, and classifies + marks-lost leads that finish the last day.
- **Handoff = repasse only**: a standardized message is sent to BOTH the
  notification group JID AND the single assigned vendor's number. `calendar` and
  `custom_api` modes are stubs that raise `NotImplementedError`.

### Non-negotiable design rules

1. The agent **NEVER reveals it is AI/bot/automation**. If asked, it deflects
   naturally and triggers a handoff. (`system_prompt.py` + `config/identity.py`)
2. Objection-handling guidance and sensitive-question answers are inlined
   **directly into the system prompt** — never into RAG.
3. `cadence_day` in `{PREFIX}leads` is the **single source of truth**; the GHL
   pipeline stage merely mirrors it.
4. Secrets live **only** in `.env` — never hardcoded.
5. Default timezone: `America/Fortaleza`.

---

## Layout

```
config/         # all per-client configuration (identity, cadence, handoff, crm, channel, hours)
core/           # pure logic + persistence (message_buffer, cadence_engine, lead_manager,
                #   handoff_engine, crm_client, media_handler)
channels/       # uazapi channel (send_reply re-export + register_webhook)
cron/           # followup_dispatcher (asyncio loop) + cadence_scheduler / daily_advance (APScheduler)
db/schema.sql   # parametrized schema ({PREFIX} substituted at load time)
agent.py        # the single Agno Agent (built at import)
app.py          # FastAPI app: lifespan, /{SLUG}/webhook, /health
system_prompt.py
tools.py        # @tool functions (thin wrappers over core/*; deps injected via run_context)
tests/          # unit + integration tests
```

---

## Clone the template to a new agent

1. **Copy the folder** to your new agent location:
   ```bash
   cp -r blueprints/sdr-template my-clients/acme-sdr
   ```
2. **Fill `config/`** — these are the only files you normally edit per client:
   - `config/identity.py` — persona, products, tone, **objections**, and
     **sensitive_qa** (loaded into the system prompt, never RAG). Include the
     "are you a bot?" deflection here.
   - `config/cadence.py` — `CADENCE_SCHEDULE` (day 1 uses `offset_min`; days 2-5
     use wall-clock `time`) and `SILENCE_WINDOW_MINUTES`.
   - `config/handoff.py` — `mode="repasse"`, distribution
     (`single`/`round_robin`/`by_product`), vendor roster, group JID.
   - `config/crm.py` — GHL pipeline id + the `stages` map (mirrors cadence_day).
   - `config/business_hours.py` — `window`/`24h`, open/close, workdays, holidays.
   - `config/channel.py` — uazapi instance/base/token (from env).
3. **Create `.env`** from `.env.example` and fill in all secrets and IDs.
4. **Create the schema** with your prefix (`""` in production):
   ```bash
   # substitute {PREFIX} -> "" (production) and run against your DB
   sed 's/{PREFIX}//g' db/schema.sql | psql "$DATABASE_URL_PSYCOPG"
   ```
   `DATABASE_URL_PSYCOPG` is the plain `postgresql://...` DSN. `DATABASE_URL`
   (used by `AsyncPostgresDb`) is the SQLAlchemy `postgresql+psycopg_async://...`
   form.
5. **Start it** (single worker — the async engine is not fork-safe):
   ```bash
   python app.py
   # or: uvicorn app:create_app --factory --workers 1 --host 0.0.0.0 --port $PORT
   ```

---

## Environment variables

See `.env.example` for the full list. Highlights:

| Var | Purpose |
|-----|---------|
| `DATABASE_URL` | `postgresql+psycopg_async://...` — for `AsyncPostgresDb` (agent sessions). |
| `DATABASE_URL_PSYCOPG` | plain `postgresql://...` — for cron DB connections (optional; derived from `DATABASE_URL` if unset). |
| `SLUG` | webhook path + session/user id prefix (default `sdr`). |
| `PORT` | HTTP port (default `7780`). |
| `OMNIROUTE_API_KEY`, `AGNO_DEFAULT_MODEL` | model routing (Omniroute proxy). |
| `AGNO_MULTIMODAL_MODEL` | **must** be the regular multimodal model (`mimo/mimo-v2.5`, not `-pro`). |
| `UAZAPI_BASE`, `UAZAPI_TOKEN`, `UAZAPI_INSTANCE` | WhatsApp channel. |
| `GHL_PIT`, `GHL_LOCATION_ID`, `GHL_PIPELINE_ID` | GoHighLevel CRM. |
| `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` | media storage. |
| `NOTIFICATION_GROUP_JID`, `VENDOR_PHONE` | repasse handoff targets. |
| `SDR_REGISTER_WEBHOOK` | set to `1` to register the uazapi webhook (APPEND-only) on startup. Default off so it never fires in tests. |
| `SDR_START_BACKGROUND` | set to `0` to disable the dispatcher loop + scheduler (default on). |
| `WEBHOOK_URL` | the public URL to register with uazapi. |

Webhook registration is **APPEND-only**: it fetches the instance's existing
webhooks and adds this one without clobbering co-tenant integrations
(`channels/uazapi.register_webhook`). It is idempotent.

---

## Handoff (repasse only)

This build supports only the `repasse` handoff mode. A repasse sends one
standardized message to **both** the notification group JID and the assigned
vendor's direct number (`core.handoff_engine.targets_for_repasse`). If the CRM
sync fails during a handoff, the message is still delivered but flagged so the
vendor captures the lead manually. The `calendar` and `custom_api` modes are
intentionally stubs that raise `NotImplementedError`.

---

## RAG knowledge base (production)

The optional knowledge base uses a PGVector table `{PREFIX}knowledge` and is
**left out of the default DDL** because `pgvector` may not be installed. In
production, install it first:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

then create the `{PREFIX}knowledge` table as sketched at the bottom of
`db/schema.sql`. **Never** load objections or sensitive-QA answers into RAG —
those go directly into the system prompt via `config/identity.py`.

---

## Tests

Real Postgres is used for DB-backed tests (each test picks a unique table
prefix). Network, GHL, uazapi, and the live model are always faked.

```bash
cd blueprints/sdr-template
/root/bf-agents/.venv/bin/python -m pytest -v
```
