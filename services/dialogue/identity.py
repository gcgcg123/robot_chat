"""Whose memories a dialogue turn is allowed to reach.

The PC simulator lets the operator pick a user from a dropdown, and
``POST /api/chat`` takes ``user_id`` straight from the request body.  That is a
*claim* about who is speaking, not an identification of them.

With ``IOT_MEMORY_REQUIRE_IDENTITY=1`` the claim is refused, so
``services.memory.retriever.visible_chunk`` stops personal memory from entering
the prompt and only shared knowledge is used.  Flip it on once real voiceprint
identification is wired into the turn; until then the default (0) keeps the
simulator workflow intact.
"""
from __future__ import annotations

import os

from services.voiceprint.matcher import IdentityResult

_TRUTHY = {"1", "true", "yes", "on"}

# Reason strings surface in logs / tracing so it is obvious which path was taken.
REASON_SELECTED = "selected_user"
REASON_REQUIRED = "identity_required"


def memory_identity_required(env: dict | None = None) -> bool:
    """Whether personal memory needs a verified speaker identity."""

    source = os.environ if env is None else env
    return str(source.get("IOT_MEMORY_REQUIRE_IDENTITY", "")).strip().lower() in _TRUTHY


def conversation_identity(
    user_id: str,
    voiceprint_id: str | None = None,
    *,
    require_identity: bool | None = None,
    env: dict | None = None,
) -> IdentityResult:
    """Identity used to gate personal memory for a single turn.

    ``identity.decision`` drives ``visible_chunk``: only ``"accepted"`` unlocks a
    chunk owned by a user.  When identification is required but no voiceprint has
    been checked, the decision is ``"unknown"`` and the owner stays ``None`` so a
    claim cannot be mistaken for a verified speaker.
    """

    required = memory_identity_required(env) if require_identity is None else require_identity
    if required:
        return IdentityResult("unknown", None, 0.0, None, voiceprint_id, REASON_REQUIRED)
    return IdentityResult("accepted", user_id, 1.0, None, voiceprint_id, REASON_SELECTED)
