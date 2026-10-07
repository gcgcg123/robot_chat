"""Device-side voiceprint enrollment: the board speaks, the board's own samples are stored.

Why this exists. Measured 2026-10-06 on real hardware: templates enrolled through the browser scored
**0.38 and 0.44** against the same person talking to an ESP32, below the 0.55 threshold, while the
browser's own samples scored 0.53-0.79 against each other. The microphone, distance and codec are
part of the speaker's embedding, so a template that must match *this* device has to be recorded *on*
this device.

The pending state is in memory, like the browser wizard's (`services/enrollment/service.py`) and the
gateway's other live registries. A restart therefore abandons an enrollment in progress, which is the
right failure mode for a one-minute operation the operator is standing in front of.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

#: How long an enrollment stays open before the operator has to start over.
DEFAULT_TTL_SECONDS = 600

#: What to say back when a sample is rejected, keyed by the quality gate's own reason
#: (services/audio/quality.py). The user is standing in front of the board and can only act on
#: "speak louder" or "closer", not on "no_speech".
REJECTION_HINTS = {
    "too_short": "這句話有點短，請慢慢說完整一句",
    "no_speech": "我沒聽到聲音，請靠近一點、大聲一點",
    "clipping": "聲音有點破，請離麥克風遠一點點",
    "empty_audio": "我沒收到聲音，請再說一次",
    "voiceprint_unavailable": "這台服務還沒裝聲紋功能",
    "speaker_mismatch": "這一句聽起來不像同一個人，請本人對著設備再說一次",
}


@dataclass
class DeviceEnrollment:
    device_id: str
    user_id: str
    language: str = "zh-CN"
    #: How many recordings this enrollment needs.  The device asks for more than the browser
    #: wizard (``services/voiceprint/enrollment.REQUIRED_SAMPLES``): a board microphone at desk
    #: distance yields looser templates, and the operator is standing right there anyway.
    required_samples: int = 3
    steps_done: list[int] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + DEFAULT_TTL_SECONDS)
    last_reason: str = ""
    #: Set once templates exist; the registry drops the entry at that point.
    completed: bool = False
    result: dict = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at

    @property
    def next_step(self) -> int | None:
        from services.voiceprint.enrollment import steps_for

        return next((step for step in steps_for(self.required_samples) if step not in self.steps_done), None)

    @property
    def sample_count(self) -> int:
        return len(self.steps_done)

    def as_dict(self, prompts: list[str] | None = None) -> dict:
        remaining = max(0.0, self.expires_at - time.time())
        return {
            "device_id": self.device_id,
            "user_id": self.user_id,
            "language": self.language,
            "state": "completed" if self.completed else ("expired" if self.expired else "collecting"),
            "step": self.next_step,
            "sample_count": self.sample_count,
            "required_samples": self.required_samples,
            "expires_in": round(remaining),
            "last_reason": self.last_reason,
            "prompts": prompts or [],
            "result": self.result,
        }


class DeviceEnrollmentRegistry:
    """One pending enrollment per device; starting a new one replaces the old."""

    def __init__(self, required_samples: int = 3) -> None:
        self._items: dict[str, DeviceEnrollment] = {}
        self.required_samples = max(1, int(required_samples or 3))

    def start(self, device_id: str, user_id: str, language: str = "zh-CN") -> DeviceEnrollment:
        item = DeviceEnrollment(
            device_id=device_id,
            user_id=user_id,
            language=language,
            required_samples=self.required_samples,
        )
        self._items[device_id] = item
        return item

    def get(self, device_id: str) -> DeviceEnrollment | None:
        item = self._items.get(device_id)
        if item is None:
            return None
        if item.expired and not item.completed:
            self._items.pop(device_id, None)
            return None
        return item

    def cancel(self, device_id: str) -> bool:
        return self._items.pop(device_id, None) is not None

    def finish(self, device_id: str) -> None:
        self._items.pop(device_id, None)

    def snapshot(self) -> dict:
        return {device_id: item.as_dict() for device_id, item in self._items.items()}


def prompt_for(language: str, step: int) -> str:
    """The sentence the user is asked to say for ``step`` (1-based), from the shared prompt table."""

    from services.enrollment.languages import LANGUAGES

    prompts = list((LANGUAGES.get(language) or LANGUAGES["zh-CN"])["prompts"])
    if not prompts:
        return ""
    return str(prompts[(max(1, step) - 1) % len(prompts)])


def prompts_for(language: str) -> list[str]:
    from services.enrollment.languages import LANGUAGES

    return [str(value) for value in (LANGUAGES.get(language) or LANGUAGES["zh-CN"])["prompts"]]


def next_request_id() -> str:
    return uuid.uuid4().hex
