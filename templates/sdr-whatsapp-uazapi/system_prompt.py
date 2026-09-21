"""System prompt builder.

build_system_prompt assembles the full system message from config/identity.py.
It MUST inline the objection-handling guidance and the sensitive-question
answers DIRECTLY into the prompt (never via RAG), and MUST encode the
non-negotiable identity rule: the agent never reveals it is AI/automation; if
asked, it deflects naturally and triggers a handoff.
"""
from __future__ import annotations


def build_system_prompt(identity: dict) -> str:
    """Build the agent's system prompt string from the IDENTITY config.

    Args:
        identity: the IDENTITY dict (agent_name, company_name, company_desc,
            products, tone, objections, sensitive_qa).

    Returns the complete system message text, with objections + sensitive_qa
    inlined and the identity/handoff rules stated explicitly.
    """
    agent_name = identity["agent_name"]
    company_name = identity["company_name"]
    company_desc = identity["company_desc"]
    tone = identity["tone"]
    products = identity.get("products", [])

    # Optional rich identity blocks. Each is inlined verbatim only when present,
    # so a minimal IDENTITY (no rich keys) still builds correctly.
    optional_blocks: list[tuple[str, str]] = [
        ("PROPÓSITO", identity.get("mission", "")),
        ("QUEM CHEGA ATÉ VOCÊ", identity.get("audience", "")),
        ("MODOS DE VOZ", identity.get("voice_modes", "")),
        ("O QUE VOCÊ PRECISA QUALIFICAR", identity.get("qualification_goals", "")),
        ("SEU OBJETIVO (CONVERSÃO = REPASSE)", identity.get("conversion_goal", "")),
        ("COMO TRATAR PREÇO", identity.get("price_policy", "")),
        (
            "PROTOCOLO DE EMERGÊNCIA (RISCO DE VIDA)",
            identity.get("emergency_protocol", ""),
        ),
        ("PALAVRAS QUE VOCÊ USA", identity.get("vocabulary_use", "")),
        ("PALAVRAS QUE VOCÊ EVITA", identity.get("vocabulary_avoid", "")),
    ]
    rich_block = "\n\n".join(
        f"{label}:\n{value}" for label, value in optional_blocks if value
    )

    product_lines = []
    for p in products:
        product_lines.append(
            f"- {p['name']}: {p['description']}"
        )
    product_block = "\n".join(product_lines)

    identity_rule = (
        "REGRA INEGOCIÁVEL DE IDENTIDADE:\n"
        "Você NUNCA revela que é uma IA, inteligência artificial, bot, robô "
        "ou automação. Se o lead perguntar se você é um robô, IA, bot ou "
        "automação, você desconversa de forma natural (deflect, naturalmente, "
        "sem confirmar nem negar de forma robótica) e aciona o handoff "
        "(repasse) para um atendente humano. Jamais admita ser automação."
    )

    objections = identity.get("objections", {})
    objection_lines = [
        f'- Quando o lead disser "{obj}": {guidance}'
        for obj, guidance in objections.items()
    ]
    objection_block = "\n".join(objection_lines)

    sensitive_qa = identity.get("sensitive_qa", {})
    qa_lines = [
        f'- Pergunta: "{q}" -> Responda: {answer}'
        for q, answer in sensitive_qa.items()
    ]
    qa_block = "\n".join(qa_lines)

    style_rules = (
        "Regras de estilo e qualificação:\n"
        "- Responda SEMPRE em português do Brasil.\n"
        "- Use mensagens curtas, no estilo de conversa de WhatsApp.\n"
        "- NUNCA use markdown (sem asteriscos, sem listas com hífen, sem "
        "títulos, sem formatação). Texto puro.\n"
        "- Faça apenas uma pergunta por vez (uma pergunta de cada vez).\n"
        "- Conduza uma qualificação progressiva do lead. Não repita perguntas "
        "cujas respostas você já sabe; não pergunte novamente dados já "
        "informados."
    )

    rich_section = f"{rich_block}\n\n" if rich_block else ""

    return (
        f"Você é {agent_name}, atendente da {company_name}.\n"
        f"Sobre a empresa: {company_desc}\n"
        f"Tom de voz: {tone}\n\n"
        f"{rich_section}"
        f"Produtos que você pode apresentar:\n{product_block}\n\n"
        f"{identity_rule}\n\n"
        f"Como lidar com objeções:\n{objection_block}\n\n"
        f"Respostas para perguntas sensíveis (use exatamente estas respostas):\n"
        f"{qa_block}\n\n"
        f"{style_rules}\n"
    )
