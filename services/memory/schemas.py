from __future__ import annotations

from dataclasses import dataclass

# Tier names for the hot/cold memory store.
TIER_SLOT = "slot"
TIER_HOT = "hot"
TIER_COLD = "cold"
TIERS = (TIER_SLOT, TIER_HOT, TIER_COLD)


@dataclass(frozen=True)
class MemoryChunk:
    chunk_id: str
    owner_user_id: str | None
    text: str
    source_id: str
    source_kind: str
    approved: bool = False
    active: bool = True
    # --- tiered memory state -------------------------------------------------
    tier: str = TIER_HOT
    score: float = 0.0
    hits: int = 0
    importance: float = 0.5
    slot_key: str | None = None
    # Canonical question the memory answers. Matching a user question against
    # this is far more reliable than matching it against `text`; see
    # services/memory/retriever.py.
    probe: str | None = None
    embedding: tuple[float, ...] | None = None
