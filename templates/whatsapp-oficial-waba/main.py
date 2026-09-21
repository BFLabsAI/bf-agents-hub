"""
CLI entry point for local development and testing.
Run: python main.py
"""
import asyncio

from app.agent_factory import create_agent


async def main() -> None:
    agent = create_agent(session_id="agent-cli-dev")
    print("Agente CLI — digite sua mensagem. Ctrl+C para sair.\n")
    while True:
        try:
            user_input = input("Você: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nEncerrando.")
            break
        if not user_input:
            continue
        result = await agent.arun(user_input)
        reply = result.content if hasattr(result, "content") else str(result)
        print(f"Agente: {reply}\n")


if __name__ == "__main__":
    asyncio.run(main())
