"""Shared pytest fixtures for the SDR template test suite.

Provides:
  - a raw psycopg AsyncConnection to the test Postgres DB
  - a `schema_factory` fixture: given a UNIQUE prefix, runs db/schema.sql with
    {PREFIX} substituted, yields, then DROPs the tables (so parallel test
    modules never collide — each test picks its own prefix, e.g.
    f"test_leadmgr_{n}_").
  - fake httpx transports for uazapi / GHL HTTP
  - a fake multimodal model_caller

asyncio_mode=auto is set in pytest.ini, so async tests need no marker, but
@pytest.mark.asyncio is harmless if added.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import AsyncIterator, Callable

import httpx
import psycopg
import pytest
import pytest_asyncio

# Make the blueprint root importable (config, core, channels, tools, ...).
BLUEPRINT_ROOT = Path(__file__).resolve().parent.parent
if str(BLUEPRINT_ROOT) not in sys.path:
    sys.path.insert(0, str(BLUEPRINT_ROOT))

# Raw (sync-style DSN) connection string for psycopg AsyncConnection.
# psycopg's own DSN is the plain postgresql:// form (NOT the SQLAlchemy
# +psycopg_async form, which is only for AsyncPostgresDb / SQLAlchemy).
TEST_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://user:password@localhost:5432/sdr_template_test",
)

SCHEMA_PATH = BLUEPRINT_ROOT / "db" / "schema.sql"

# Tables defined in schema.sql (without prefix), in drop order (no FKs, any
# order works, but listed leaf-first for clarity).
_SCHEMA_TABLES = ["media", "followup_queue", "products", "leads"]


@pytest_asyncio.fixture
async def db_conn() -> AsyncIterator[psycopg.AsyncConnection]:
    """Yield a raw psycopg AsyncConnection to the test DB (autocommit)."""
    conn = await psycopg.AsyncConnection.connect(TEST_DSN, autocommit=True)
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def schema_factory(
    db_conn: psycopg.AsyncConnection,
) -> AsyncIterator[Callable[[str], "_PreparedSchema"]]:
    """Return a callable: prefix -> creates prefixed schema, auto-dropped.

    Usage in a test:

        async def test_x(schema_factory, db_conn):
            prefix = "test_leadmgr_1_"
            await schema_factory(prefix)
            # ... operate on {prefix}leads etc via db_conn ...

    All prefixes created during the test are dropped on teardown.
    """
    raw_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    created_prefixes: list[str] = []

    async def _make(prefix: str) -> "_PreparedSchema":
        ddl = raw_sql.replace("{PREFIX}", prefix)
        await db_conn.execute(ddl)
        created_prefixes.append(prefix)
        return _PreparedSchema(prefix)

    try:
        yield _make
    finally:
        for prefix in created_prefixes:
            for table in _SCHEMA_TABLES:
                await db_conn.execute(
                    f'DROP TABLE IF EXISTS "{prefix}{table}" CASCADE'
                )


class _PreparedSchema:
    """Tiny handle returned by schema_factory; carries the prefix."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix


# --- Fake HTTP transports --------------------------------------------------

def make_fake_httpx(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    """Build an httpx.AsyncClient backed by a MockTransport.

    `handler(request) -> httpx.Response`. Use to fake uazapi or GHL HTTP so
    tests never hit the network. Example:

        def handler(req):
            return httpx.Response(200, json={"id": "abc"})
        client = make_fake_httpx(handler)
    """
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture
def fake_uazapi() -> Callable[..., httpx.AsyncClient]:
    """Fixture returning the make_fake_httpx factory (uazapi-flavored default).

    Default handler returns 200 {"status":"ok"} for any request; pass your own
    handler to make_fake_httpx for richer behavior.
    """
    def _default(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    def _factory(handler: Callable[[httpx.Request], httpx.Response] | None = None):
        return make_fake_httpx(handler or _default)

    return _factory


@pytest.fixture
def fake_ghl() -> Callable[..., httpx.AsyncClient]:
    """Fixture returning a make_fake_httpx factory (GHL-flavored default).

    Default handler echoes a generic id payload; override per test.
    """
    def _default(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "ghl_test_id"})

    def _factory(handler: Callable[[httpx.Request], httpx.Response] | None = None):
        return make_fake_httpx(handler or _default)

    return _factory


@pytest.fixture
def fake_model_caller() -> Callable[..., object]:
    """Fixture: a fake multimodal model_caller for media_handler tests.

    Returns an async callable that ignores its inputs and returns a canned
    transcript string ("[fake transcript]"). Replace the return by wrapping if
    a test needs a specific value.
    """
    async def _caller(*args: object, **kwargs: object) -> str:
        return "[fake transcript]"

    return _caller
