-- SDR Template — parametrized schema.
-- All table names are prefixed with {PREFIX} which the loader substitutes
-- (e.g. production: ""; tests: "test_lead_1_"). Substitution is a plain
-- str.replace("{PREFIX}", prefix) before execution.
--
-- IMPORTANT: cadence_day in {PREFIX}leads is the SINGLE source of truth for
-- where a lead is in the cadence; the GHL pipeline stage merely mirrors it.

-- ---------------------------------------------------------------------------
-- Leads
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS {PREFIX}leads (
    id              BIGSERIAL PRIMARY KEY,
    phone           TEXT NOT NULL UNIQUE,
    name            TEXT,
    -- status ∈ active | qualified | assigned | scheduled | lost | optout
    status          TEXT NOT NULL DEFAULT 'active',
    cadence_day     SMALLINT NOT NULL DEFAULT 1,
    cadence_paused  BOOLEAN NOT NULL DEFAULT FALSE,
    last_activity   TIMESTAMPTZ,
    silence_since   TIMESTAMPTZ,
    assigned_vendor TEXT,
    handoff_done    BOOLEAN NOT NULL DEFAULT FALSE,
    ghl_contact_id  TEXT,
    ghl_opp_id      TEXT,
    loss_reason     TEXT,
    fields          JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS {PREFIX}leads_status_idx       ON {PREFIX}leads (status);
CREATE INDEX IF NOT EXISTS {PREFIX}leads_cadence_day_idx  ON {PREFIX}leads (cadence_day);

-- ---------------------------------------------------------------------------
-- Follow-up queue
-- ---------------------------------------------------------------------------
-- content JSONB shape: {"type": "text"|"media", "text": "...", "media_url": "..."}
CREATE TABLE IF NOT EXISTS {PREFIX}followup_queue (
    id               BIGSERIAL PRIMARY KEY,
    phone            TEXT NOT NULL,
    scheduled_at     TIMESTAMPTZ NOT NULL,
    content          JSONB NOT NULL,
    -- status ∈ pending | sent | cancelled
    status           TEXT NOT NULL DEFAULT 'pending',
    cadence_day      SMALLINT,
    ghl_note_logged  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS {PREFIX}followup_due_idx
    ON {PREFIX}followup_queue (status, scheduled_at);
CREATE INDEX IF NOT EXISTS {PREFIX}followup_phone_idx
    ON {PREFIX}followup_queue (phone);

-- ---------------------------------------------------------------------------
-- Products
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS {PREFIX}products (
    id          SERIAL PRIMARY KEY,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT,
    description TEXT,
    vendor_key  TEXT,
    active      BOOLEAN NOT NULL DEFAULT TRUE
);

-- ---------------------------------------------------------------------------
-- Media (inbound audio/image/video/doc stored to S3 + transcript/extraction)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS {PREFIX}media (
    id          BIGSERIAL PRIMARY KEY,
    phone       TEXT NOT NULL,
    s3_key      TEXT,
    s3_url      TEXT,
    -- media_type ∈ audio | image | video | document
    media_type  TEXT NOT NULL,
    transcript  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS {PREFIX}media_phone_idx ON {PREFIX}media (phone);

-- ---------------------------------------------------------------------------
-- Knowledge base (OPTIONAL — production concern)
-- ---------------------------------------------------------------------------
-- The knowledge base uses a PGVector table {PREFIX}knowledge. The pgvector
-- extension is NOT installed on this box yet, so this table is intentionally
-- left out of the default DDL above. In production, after `CREATE EXTENSION
-- vector;`, create it roughly like:
--
--   CREATE TABLE {PREFIX}knowledge (
--       id          BIGSERIAL PRIMARY KEY,
--       content     TEXT,
--       meta        JSONB DEFAULT '{}',
--       embedding   vector(1536)
--   );
--
-- Keep it optional; never load objections / sensitive-QA into RAG (those go
-- directly into the system prompt via config/identity.py).
