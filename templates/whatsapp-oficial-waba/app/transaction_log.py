from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LogEvent:
    type: str   # api_request | api_response | error
    timestamp: str
    data: dict[str, Any]

    def to_dict(self) -> dict:
        return {"type": self.type, "timestamp": self.timestamp, "data": self.data}


@dataclass
class Transaction:
    id: str
    session_id: str
    from_number: str
    started_at: str
    user_message: str
    agent_reply: str | None = None
    completed_at: str | None = None
    status: str = "processing"       # processing | completed | error
    events: list[LogEvent] = field(default_factory=list)

    @staticmethod
    def new(session_id: str, from_number: str, user_message: str) -> Transaction:
        return Transaction(
            id=uuid.uuid4().hex[:8],
            session_id=session_id,
            from_number=from_number,
            started_at=_now(),
            user_message=user_message,
        )

    @property
    def duration_ms(self) -> int | None:
        if not self.completed_at:
            return None
        try:
            a = datetime.fromisoformat(self.started_at)
            b = datetime.fromisoformat(self.completed_at)
            return int((b - a).total_seconds() * 1000)
        except Exception:
            return None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "from_number": self.from_number,
            "started_at": self.started_at,
            "user_message": self.user_message,
            "agent_reply": self.agent_reply,
            "completed_at": self.completed_at,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "events": [e.to_dict() for e in self.events],
        }
