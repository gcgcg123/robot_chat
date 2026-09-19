from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any


@dataclass(frozen=True)
class DialogueResult:
    """Stable, transport-neutral result for one dialogue turn."""

    turn_id: str
    session_id: str
    user_id: str | None
    identity: Any
    text: str
    emotion: str
    risk: dict[str, Any]
    reply: str
    citations: list[str] = field(default_factory=list)
    segments: list[str] = field(default_factory=list)
    latency_ms: dict[str, int] = field(default_factory=dict)
    model: dict[str, Any] = field(default_factory=dict)
    # Memories that actually made it into the prompt (the flywheel rewards these)
    used_memory_ids: list[str] = field(default_factory=list)
    # Raw `remember` proposals from the model, for the caller to persist
    memory_candidates: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        identity = asdict(self.identity) if is_dataclass(self.identity) else self.identity
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "identity": identity,
            "text": self.text,
            "emotion": self.emotion,
            "risk": self.risk,
            "reply": self.reply,
            "citations": list(self.citations),
            "segments": list(self.segments),
            "latency_ms": dict(self.latency_ms),
            "model": dict(self.model),
            "used_memory_ids": list(self.used_memory_ids),
            "memory_candidates": list(self.memory_candidates),
        }

