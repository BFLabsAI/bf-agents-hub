from agno.agent import Agent
from agno.db.postgres.postgres import PostgresDb

from app.client_api import ClientApi
from app.config import CLIENT_NAME, CLIENT_ENV, DATABASE_URL, DB_SCHEMA
from app.context import SessionContext
from app.llm_factory import build_model
from prompts.agent_prompt import build_system_prompt
from tools.identity import build_identity_tools
from tools.events import build_event_tools
from tools.category import build_category_tools
from tools.subscription import build_subscription_tools
from tools.payment import build_payment_tools


# Singleton PostgresDb — criado uma vez por processo para reutilizar o pool SQLAlchemy
_pg_db: PostgresDb | None = None


def get_pg_db() -> PostgresDb:
    global _pg_db
    if _pg_db is None:
        _pg_db = PostgresDb(db_url=DATABASE_URL, db_schema=DB_SCHEMA)
    return _pg_db

def create_agent(
    session_id: str = "agent-default",
    from_number: str = "",
    client_name: str = "",
    env: str = "",
    waba_client=None,
) -> Agent:
    state: dict = {
        "from_number": from_number,  # WhatsApp sender — used by identify_by_phone
        "session_id": session_id,
        "client": client_name or CLIENT_NAME,
        "env": env or CLIENT_ENV,
        "entity_id": 1,
        "language_id": 1,
    }
    ctx = SessionContext(state)
    api = ClientApi(ctx)  # ctx ref lets api auto-restore token after restarts

    all_tools = [
        *build_identity_tools(ctx, api),
        *build_event_tools(ctx, api, waba_client=waba_client),
        *build_category_tools(ctx, api),
        *build_subscription_tools(ctx, api),
        *build_payment_tools(ctx, api, waba_client=waba_client),
    ]

    prompt = build_system_prompt()
    db = get_pg_db()

    return Agent(
        name="Agent",
        id="whatsapp-agent",
        session_id=session_id,
        user_id=from_number or None,
        model=build_model(),
        tools=all_tools,
        session_state=state,
        db=db,
        add_history_to_context=True,
        num_history_runs=10,
        description=prompt["description"],
        instructions=prompt["instructions"],
        markdown=False,
    )
