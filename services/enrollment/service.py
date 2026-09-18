from __future__ import annotations

from dataclasses import dataclass, field
import time
import uuid


STATES = ("pending", "awaiting_device", "collecting_profile", "collecting_samples", "verifying", "completed", "failed", "canceled", "expired")


@dataclass
class Enrollment:
    enrollment_id: str
    user_id: str
    language: str = 'yue-HK'
    state: str = "pending"
    result: dict | None = None
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 600)


class EnrollmentService:
    """Coordinates the in-progress enrollment wizard; samples live in the DB."""
    def __init__(self): self.items: dict[str, Enrollment] = {}
    def start(self, user_id: str, language: str = 'yue-HK') -> Enrollment:
        self.cancel_user(user_id)
        e = Enrollment(str(uuid.uuid4()), user_id, language, 'awaiting_device')
        self.items[e.enrollment_id] = e
        return e

    def cancel_user(self, user_id: str):
        for item in self.items.values():
            if item.user_id == user_id and item.state != 'completed':
                item.state = 'canceled'

    def collecting(self, enrollment_id: str) -> Enrollment:
        item = self.items[enrollment_id]
        if time.time() > item.expires_at:
            item.state = 'expired'
        if item.state != 'collecting_samples':
            raise ValueError('enrollment_' + item.state)
        return item
    def transition(self, enrollment_id: str, state: str) -> Enrollment:
        e = self.items[enrollment_id]
        if time.time() > e.expires_at and e.state not in {"completed", "failed", "canceled"}: e.state = "expired"
        if state not in STATES or e.state in {"completed", "failed", "canceled", "expired"}: raise ValueError("invalid_enrollment_transition")
        e.state = state; return e
