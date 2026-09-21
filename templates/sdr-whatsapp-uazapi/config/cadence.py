"""Follow-up cadence schedule.

CADENCE_SCHEDULE maps cadence_day (1..5) -> list of touch specs for that day.

Two kinds of touch spec:
  - {"offset_min": N}  -> a touch N minutes AFTER the lead entered the cadence
                          (used on day 1, where touches are relative to entry).
  - {"time": "HH:MM"}  -> a touch at a fixed wall-clock time (local timezone,
                          see config/business_hours.py) on that cadence day.

SILENCE_WINDOW_MINUTES is how long a lead must be silent before follow-ups
resume / a paused cadence may unpause (see core.cadence_engine.should_unpause).
"""
from __future__ import annotations

CADENCE_SCHEDULE: dict[int, list[dict]] = {
    1: [{"offset_min": 0}, {"offset_min": 60}],
    2: [{"time": "09:00"}, {"time": "15:00"}],
    3: [{"time": "09:00"}, {"time": "15:00"}],
    4: [{"time": "09:00"}, {"time": "14:00"}, {"time": "18:00"}],
    5: [{"time": "09:00"}, {"time": "13:00"}, {"time": "16:00"}, {"time": "19:00"}],
}

SILENCE_WINDOW_MINUTES: int = 120


# ---------------------------------------------------------------------------
# Follow-up copy (+1 Passo voice).
#
# FOLLOWUP_COPY[day] is a list of seed messages, one per touch in
# CADENCE_SCHEDULE[day] (same order). These are SEEDS: each is sent THROUGH
# agent.arun (see cron/followup_dispatcher), so the agent adapts the tone
# naturally and the message lands in the conversation history. Acolhimento
# first, always a próximo passo, day 5 is the last (firmer but still warm)
# attempt. Never reveals automation.
# ---------------------------------------------------------------------------
FOLLOWUP_COPY: dict[int, list[str]] = {
    1: [
        "Olá! Tudo bem? Passando para saber se conseguiu avaliar o que conversamos. "
        "Estou à disposição para tirar qualquer dúvida que tenha surgido!",
        "Oi! Sei que a rotina é corrida. Ficou com alguma dúvida sobre como podemos te ajudar?",
    ],
    2: [
        "Bom dia! Como estão as coisas por aí? Se quiser, podemos agendar uma conversa rápida "
        "com um dos nossos especialistas para você ver na prática como funciona.",
        "Olá! Passando só para lembrar que estamos aqui caso queira entender mais detalhes da nossa solução.",
    ],
    3: [
        "Oi! Tudo bem? Muitas pessoas que nos procuram costumam ter dúvidas sobre prazos e formatos. "
        "Gostaria de ver um resumo detalhado para o seu caso?",
        "Olá! Continuo à disposição por aqui. Se fizer sentido dar o próximo passo agora, só me avisar!",
    ],
    4: [
        "Bom dia! Passando para checar se podemos te ajudar com algo mais ou se prefere "
        "retomar essa conversa mais adiante.",
        "Oi! Sem pressa, apenas para não deixar seu contato sem resposta. Quer que eu mantenha seu atendimento ativo?",
    ],
    5: [
        "Olá! Como não tivemos retorno recente, vou encerrar este contato por enquanto para não incomodar. "
        "Caso queira retomar no futuro, as portas continuam abertas. Tenha uma excelente semana!",
    ],
}


def make_followup_content_provider(copy: dict[int, list[str]] | None = None):
    """Build the content_provider(day, idx) -> content dict used by the daily
    cadence advance to enqueue follow-ups.

    Returns a dict {"type","text","media_url","day","idx"}. Falls back to the
    last copy of the day for an out-of-range idx so the job never crashes.
    """
    roster = copy if copy is not None else FOLLOWUP_COPY

    def _provider(day: int, idx: int) -> dict:
        texts = roster.get(day) or roster.get(str(day)) or []
        if texts:
            text = texts[idx] if idx < len(texts) else texts[-1]
        else:
            text = ""
        return {
            "type": "text",
            "text": text,
            "media_url": None,
            "day": day,
            "idx": idx,
        }

    return _provider
