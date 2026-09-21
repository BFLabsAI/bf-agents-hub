"""Tests for system_prompt.build_system_prompt (pure function)."""
from __future__ import annotations

from system_prompt import build_system_prompt


def _identity() -> dict:
    return {
        "agent_name": "Ana",
        "company_name": "ACME",
        "company_desc": "Empresa que vende o produto X.",
        "products": [
            {
                "slug": "produto-x",
                "name": "Produto X",
                "description": "Descrição curta do produto X.",
                "vendor_key": "vendedor_1",
            },
            {
                "slug": "produto-y",
                "name": "Produto Y",
                "description": "Outro produto.",
                "vendor_key": "vendedor_2",
            },
        ],
        "tone": "Cordial, objetivo, consultivo.",
        "objections": {
            "está caro": "Reforce o valor e o retorno.",
            "vou pensar": "Pergunte qual a dúvida em aberto.",
        },
        "sensitive_qa": {
            "você é um robô?": "Sou da equipe da ACME, estou aqui pra te ajudar.",
            "qual o endereço?": "Atendemos online em todo o Brasil.",
        },
    }


def test_includes_persona_and_products():
    prompt = build_system_prompt(_identity())
    assert "Ana" in prompt
    assert "ACME" in prompt
    assert "Empresa que vende o produto X." in prompt
    assert "Cordial, objetivo, consultivo." in prompt
    # every product name + description present
    assert "Produto X" in prompt
    assert "Descrição curta do produto X." in prompt
    assert "Produto Y" in prompt
    assert "Outro produto." in prompt


def test_never_reveals_ai_and_triggers_handoff():
    prompt = build_system_prompt(_identity()).lower()
    # Mentions the core forbidden concepts
    assert "nunca" in prompt
    assert "ia" in prompt or "inteligência artificial" in prompt
    assert "bot" in prompt or "robô" in prompt
    assert "automação" in prompt or "automacao" in prompt
    # Deflect + handoff instruction present
    assert "handoff" in prompt or "repasse" in prompt or "transfer" in prompt
    assert "deflet" in prompt or "desconvers" in prompt or "naturalmente" in prompt


def test_inlines_objections_and_sensitive_qa():
    identity = _identity()
    prompt = build_system_prompt(identity)
    # every objection key AND its guidance present verbatim
    for key, guidance in identity["objections"].items():
        assert key in prompt
        assert guidance in prompt
    # every sensitive question AND its exact answer present verbatim
    for question, answer in identity["sensitive_qa"].items():
        assert question in prompt
        assert answer in prompt


def test_inlines_optional_rich_identity_blocks():
    """Optional rich keys (mission, audience, voices, vocab, goals, price,
    emergency) are inlined verbatim when present."""
    identity = _identity()
    identity.update(
        {
            "mission": "Transformar sofrimento em movimento de recuperacao.",
            "audience": "Quem chega quase nunca chega inteiro.",
            "voice_modes": "Modo Palacio acolhe; Modo Falcao conduz.",
            "vocabulary_use": "passo, recuperacao, so por hoje",
            "vocabulary_avoid": "cura milagrosa, caso perdido",
            "qualification_goals": "Descubra para quem e e a substancia.",
            "conversion_goal": "Convide para conversar com o Marcelo Palacio.",
            "price_policy": "Nao puxe preco; ancore no valor.",
            "emergency_protocol": "Em risco de vida, oriente SAMU 192 e CVV 188.",
        }
    )
    prompt = build_system_prompt(identity)
    for key in (
        "mission",
        "audience",
        "voice_modes",
        "vocabulary_use",
        "vocabulary_avoid",
        "qualification_goals",
        "conversion_goal",
        "price_policy",
        "emergency_protocol",
    ):
        assert identity[key] in prompt, f"{key} not inlined"


def test_rich_blocks_omitted_when_absent():
    """Minimal identity (no rich keys) still builds without error — backward
    compatible."""
    minimal = _identity()  # no rich keys
    prompt = build_system_prompt(minimal)
    assert "ACME" in prompt  # base content still present


def test_includes_style_and_qualification_rules():
    prompt = build_system_prompt(_identity()).lower()
    # Portuguese
    assert "português" in prompt or "portugues" in prompt
    # short messages
    assert "curt" in prompt
    # no markdown
    assert "markdown" in prompt
    # one question at a time
    assert "uma pergunta" in prompt or "uma de cada vez" in prompt
    # progressive qualification — don't re-ask known data
    assert "qualific" in prompt
    assert (
        "não repita" in prompt
        or "nao repita" in prompt
        or "não pergunte novamente" in prompt
        or "já sabe" in prompt
        or "ja sabe" in prompt
    )
