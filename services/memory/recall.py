"""Pick the memories a single turn should see, walking the tier cascade.

Order of the cascade (the point of the whole design):

1. **slot** -- at most 32 entries: basic facts about the user plus whatever is
   currently salient.  Consulted first, and 'basic' keys are injected
   unconditionally so the robot never forgets the user's name.
2. **hot** -- recently remembered facts, consulted when the slots have nothing
   relevant enough.
3. **cold** -- pseudo-deleted rows, searched only as a last resort.  A cold row
   that does match is revived by the flywheel (its hit raises the score back over
   the demote threshold).

A tier ends the walk only when it *matched the question*.  Holding a basic key is
deliberately not enough: a name memory reaches the slot tier after two hits and is
hit every turn, so treating "has a basic key" as "the question is answered" made
every other memory of that user unreachable, hot and cold included.

Relevance is decided with the calibrated floor: 0.68 sits between the measured
hit band (0.781-1.000) and the non-hit band (0.378-0.579) when matching a question
against a stored probe.  Cold search additionally requires a higher bar so a weak
match does not resurrect something the user has effectively forgotten.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from services.memory.flywheel import MemorySettings
from services.memory.repository import load_shared, load_tier
from services.memory.retriever import rank_chunks, visible_chunk
from services.memory.schemas import TIER_COLD, TIER_HOT, TIER_SLOT, MemoryChunk

Embedder = Callable[[str], Sequence[float]]

# Slot keys whose whole purpose is "always know this about the user".
BASIC_SLOT_KEYS = frozenset({"name", "nickname", "call_me", "identity", "language"})


@dataclass
class RecallResult:
    """What the turn will see, plus enough detail to explain or assert on it."""

    selected: list[MemoryChunk] = field(default_factory=list)
    scored: list[tuple[float, MemoryChunk]] = field(default_factory=list)
    tier_reached: str = TIER_SLOT
    slot_best: float = 0.0
    hot_best: float = 0.0
    cold_best: float = 0.0
    revived: list[str] = field(default_factory=list)

    @property
    def chunk_ids(self) -> list[str]:
        return [chunk.chunk_id for chunk in self.selected]


def _is_basic(chunk: MemoryChunk) -> bool:
    return bool(chunk.slot_key) and chunk.slot_key in BASIC_SLOT_KEYS


def _merge_basics(basic: list[MemoryChunk], matched: list[MemoryChunk], config: MemorySettings) -> list[MemoryChunk]:
    """Always carry the basic keys, then whatever this tier actually matched.

    Basics are context ("who am I talking to"), not an answer to the question, so
    they ride along with every tier instead of deciding which tier we stop at.
    """

    picked: list[MemoryChunk] = []
    for chunk in basic[: config.slot_top_n]:
        if chunk not in picked:
            picked.append(chunk)
    for chunk in matched:
        if len(picked) >= config.inject_top_n + len(basic):
            break
        if chunk not in picked:
            picked.append(chunk)
    return picked[: max(config.inject_top_n, len(basic))]


def select_for_turn(
    conn: Any,
    *,
    user_id: str | None,
    query: str,
    identity: Any,
    embedder: Embedder | None = None,
    settings: MemorySettings | None = None,
    now: float | None = None,
    cold_margin: float = 0.05,
) -> RecallResult:
    """Walk slot -> hot -> cold and return the memories to inject.

    The cascade only stops early when the tier **answered the question**.  A user
    whose slot tier merely holds their name has not had every other memory
    answered, so the search continues into hot and cold -- otherwise a basic key
    (which every user has, since a name reaches the slot tier after two hits) would
    make the rest of their memory permanently unreachable.
    """

    config = settings or MemorySettings()
    moment = time.time() if now is None else now
    result = RecallResult()

    query_vector: Sequence[float] | None = None
    if embedder is not None:
        try:
            query_vector = embedder(query)
        except Exception:
            query_vector = None

    shared = load_shared(conn, now=moment, settings=config)

    slots = [c for c in load_tier(conn, user_id, TIER_SLOT, now=moment, settings=config) if visible_chunk(c, identity)]
    shared_visible = [c for c in shared if visible_chunk(c, identity)]
    slot_pool = slots + shared_visible
    slot_ranked = rank_chunks(slot_pool, query, query_vector)
    result.slot_best = slot_ranked[0][0] if slot_ranked else 0.0

    basic = [chunk for chunk in slots if _is_basic(chunk)]
    relevant_slots = [chunk for score, chunk in slot_ranked if score >= config.relevance_floor]

    if relevant_slots:
        result.selected = _merge_basics(basic, relevant_slots, config)
        result.scored = slot_ranked
        result.tier_reached = TIER_SLOT
        return result

    hot = [c for c in load_tier(conn, user_id, TIER_HOT, now=moment, settings=config) if visible_chunk(c, identity)]
    hot_ranked = rank_chunks(hot, query, query_vector)
    result.hot_best = hot_ranked[0][0] if hot_ranked else 0.0
    relevant_hot = [chunk for score, chunk in hot_ranked if score >= config.relevance_floor]
    if relevant_hot:
        result.selected = _merge_basics(basic, relevant_hot, config)
        result.scored = hot_ranked
        result.tier_reached = TIER_HOT
        return result

    cold = [c for c in load_tier(conn, user_id, TIER_COLD, now=moment, settings=config) if visible_chunk(c, identity)]
    cold_ranked = rank_chunks(cold, query, query_vector)
    result.cold_best = cold_ranked[0][0] if cold_ranked else 0.0
    relevant_cold = [
        chunk for score, chunk in cold_ranked if score >= config.relevance_floor + cold_margin
    ]
    if relevant_cold:
        result.selected = _merge_basics(basic, relevant_cold, config)
        result.scored = cold_ranked
        result.tier_reached = TIER_COLD
        result.revived = [chunk.chunk_id for chunk in relevant_cold]
        return result

    # Nothing anywhere answered the question; the basics still go in so the robot
    # keeps knowing who it is talking to.
    result.selected = basic[: config.inject_top_n]
    result.scored = cold_ranked or hot_ranked or slot_ranked
    result.tier_reached = TIER_COLD
    return result
