from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import uuid


EVENT_TYPES = {"turn.started", "stt.final", "tts.segment", "tts.end", "turn.interrupted", "turn.failed", "playback.started", "playback.completed"}


@dataclass(frozen=True)
class DeviceEvent:
    device_id: str
    session_id: str
    turn_id: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    protocol_version: int = 1

    def __post_init__(self):
        if self.type not in EVENT_TYPES:
            raise ValueError("unsupported_event_type")
        if self.protocol_version != 1:
            raise ValueError("unsupported_protocol_version")

    def as_dict(self) -> dict[str, Any]:
        return {"protocol_version": self.protocol_version, "event_id": self.event_id, "device_id": self.device_id, "session_id": self.session_id, "turn_id": self.turn_id, "type": self.type, "payload": self.payload}
