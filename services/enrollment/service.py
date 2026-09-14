from __future__ import annotations

from dataclasses import dataclass, field
import time
import uuid


STATES = ("pending", "awaiting_device", "collecting_profile", "collecting_samples", "verifying", "completed", "failed", "canceled", "expired")


@dataclass
class Enrollment:
    enrollment_id: str
    user_id: str
    state: str = "pending"
    samples: list[bytes] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 600)


class EnrollmentService:
    def __init__(self): self.items: dict[str, Enrollment] = {}
    def start(self, user_id: str) -> Enrollment:
        e = Enrollment(str(uuid.uuid4()), user_id, "awaiting_device"); self.items[e.enrollment_id] = e; return e
    def transition(self, enrollment_id: str, state: str) -> Enrollment:
        e = self.items[enrollment_id]
        if time.time() > e.expires_at and e.state not in {"completed", "failed", "canceled"}: e.state = "expired"
        if state not in STATES or e.state in {"completed", "failed", "canceled", "expired"}: raise ValueError("invalid_enrollment_transition")
        e.state = state; return e
    def add_sample(self, enrollment_id: str, sample: bytes) -> Enrollment:
        e = self.items[enrollment_id]
        if e.state != "collecting_samples": raise ValueError("samples_not_expected")
        if not sample: raise ValueError("empty_sample")
        e.samples.append(sample); return e
