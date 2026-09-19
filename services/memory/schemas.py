from __future__ import annotations

from dataclasses import dataclass

# Tier names for the hot/cold memory store.
TIER_SLOT = "slot"
TIER_HOT = "hot"
TIER_COLD = "cold"
TIERS = (TIER_SLOT, TIER_HOT, TIER_COLD)

# Slot keys whose whole purpose is "always know this about the user". They are
# injected unconditionally, on top of the per-question retrieval budget.
BASIC_SLOT_KEYS = frozenset({"name", "nickname", "call_me", "identity", "language"})

# Keys that describe a fact with exactly **one current value**, so a later statement
# about the same key replaces the earlier one -- including a correction such as
# "我不叫X，我叫Y". Every other key is a category that can legitimately hold several
# facts at once (skill, interest, hobby, pet, family, goal, ...): "我學會了唱跳"
# followed by "我學會了寫歌詞" is two facts, and the second must add to the first
# rather than overwrite it. That distinction cannot be read off the key or the probe
# alone -- both skills share the key `skill` and the probe "我最近學會了什麼？" -- so
# for these keys the same-fact test is the similarity of the statements themselves.
SINGLE_VALUED_SLOT_KEYS = BASIC_SLOT_KEYS | frozenset({
    "age", "birthday", "birthdate", "gender", "sex", "home", "city", "residence",
    "address", "occupation", "job", "phone", "email",
})


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
