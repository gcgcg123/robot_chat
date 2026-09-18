"""Pick the memories a single turn should see, walking the tier cascade.

The unit of retrieval is **the question, not the turn**.

1. **slot** -- at most 32 entries: basic facts about the user plus whatever is
   currently salient.  Consulted first, and 'basic' keys are injected
   unconditionally (on top of the budget) so the robot never forgets a name.
2. **hot** -- recently remembered facts.
3. **cold** -- pseudo-deleted rows, the last resort.  A cold row that matches is
   revived by the flywheel (its hit raises the score back over the demote
   threshold).

A turn is split into its questions, each question walks the tiers on its own and
stops at the first tier that answers *it*, and the injection budget is shared out
between them.  See ``select_for_turn`` for the formula and for the three measured
incidents that this shape fixes.

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
from services.memory.retriever import rank_chunks, split_queries, visible_chunk
from services.memory.schemas import BASIC_SLOT_KEYS, TIER_COLD, TIER_HOT, TIER_SLOT, MemoryChunk

# Batch contract, matching repository.Embedder: one call embeds every sub-question.
Embedder = Callable[[Sequence[str]], list[list[float]]]


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
    """Always carry the basic keys, then the memories that answered a question.

    Basics are context ("who am I talking to"), not an answer, so they ride along on
    top of the budget instead of consuming it.
    """

    picked: list[MemoryChunk] = []
    for chunk in basic[: config.slot_top_n]:
        if chunk not in picked:
            picked.append(chunk)
    for chunk in matched:
        if chunk not in picked:
            picked.append(chunk)
    return picked


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
    """Answer every question in the turn, consulting every tier for each.

    The floor is the precision gate: **every memory that clears it** for a question
    the user asked is injected, from every tier, not just that question's best one and
    not just from the first tier that happened to match.  The measured bands make that
    safe -- a real match scores 0.781-1.000 against its probe while an unrelated
    question scores 0.378-0.579 -- and it means a question answered by several
    memories gets all of them instead of an arbitrary winner.

    The tiers still matter, but only through their threshold: cold rows have to clear
    ``relevance_floor + cold_margin``, which is what keeps a pseudo-deleted memory
    from coming back on a weak match.  They are no longer a stopping rule, because
    every version of "something already matched, stop looking" has cost an answer:

    * a slot tier holding only the user's name ended the walk, making every other
      memory unreachable;
    * a tier match for the *first* question ended the walk for the whole turn.
      Measured: the name matched its slot at 0.8208 and the mango preference in the
      hot tier scored 0.7555 against that same message -- over the floor -- yet was
      never considered;
    * ranking every memory once against the whole message averaged its topics
      together.  Measured: the age memory scores 0.7172 against "我成年了吗？" alone
      but 0.5898 against "我成年了吗？我叫什么名字？我喜欢吃什么？";
    * taking only the top match per question dropped a second question that a comma
      had glued to the first.  Measured: the skill memory scored 0.6066 against the
      merged segment "我喜欢吃什么，我会哪些东西" while the food memory scored
      0.9047, so the skill question got no answer at all;
    * stopping a question at the first tier that matched hid a stronger match below.
      Measured: for "我会什么" the name matched its slot at 0.6857 while the skill
      memory in the hot tier scored 0.7293, so the robot said it had no idea what the
      user could do.

    ``inject_top_n`` is only a guard rail against a request that matches a large part
    of a long-lived account, not a ranking cut; when it bites, the highest-scoring
    memories are kept.  Basic keys ride along on top and do not consume it.
    """

    config = settings or MemorySettings()
    moment = time.time() if now is None else now
    result = RecallResult()

    # Each question is embedded separately, so a topic is not diluted by whatever
    # else the user asked in the same breath. One batched call for all of them.
    queries = split_queries(query)
    query_vectors: list[Sequence[float]] = []
    if embedder is not None and queries:
        try:
            query_vectors = list(embedder(queries))
        except Exception:
            query_vectors = []

    shared = load_shared(conn, now=moment, settings=config)
    slots = [c for c in load_tier(conn, user_id, TIER_SLOT, now=moment, settings=config) if visible_chunk(c, identity)]
    basic = [chunk for chunk in slots if _is_basic(chunk)]

    # Tier pools are loaded at most once per turn, and only when some question has to
    # reach that far: a question answered from the slots never queries hot or cold.
    pools: dict[str, list[MemoryChunk]] = {TIER_SLOT: slots + [c for c in shared if visible_chunk(c, identity)]}

    def pool(tier: str) -> list[MemoryChunk]:
        if tier not in pools:
            pools[tier] = [
                c for c in load_tier(conn, user_id, tier, now=moment, settings=config) if visible_chunk(c, identity)
            ]
        return pools[tier]

    order = (TIER_SLOT, TIER_HOT, TIER_COLD)
    floors = {
        TIER_SLOT: config.relevance_floor,
        TIER_HOT: config.relevance_floor,
        TIER_COLD: config.relevance_floor + cold_margin,
    }

    matched: list[MemoryChunk] = []
    best_score: dict[str, float] = {}
    revived: list[str] = []
    best = {tier: 0.0 for tier in order}
    contributing: list[str] = []

    for index, question in enumerate(queries):
        vector = query_vectors[index] if index < len(query_vectors) else None
        for tier in order:
            ranked = rank_chunks(pool(tier), question, vector)
            if ranked:
                best[tier] = max(best[tier], ranked[0][0])
                result.scored = ranked
            found = [(chunk, score) for score, chunk in ranked if score >= floors[tier]]
            if not found:
                continue
            if tier not in contributing:
                contributing.append(tier)
            for chunk, score in found:
                if chunk in basic:
                    continue
                if chunk not in matched:
                    matched.append(chunk)
                best_score[chunk.chunk_id] = max(best_score.get(chunk.chunk_id, 0.0), score)
            if tier == TIER_COLD:
                revived.extend(chunk.chunk_id for chunk, _score in found)
            # No break: a weaker match in a shallower tier must not hide a stronger
            # one deeper down. Measured: for "我会什么", the name matched its slot at
            # 0.6857 while the skill memory in hot scored 0.7293 -- stopping at the
            # slot tier answered "I have no idea what you can do".

    if len(matched) > config.inject_top_n:
        matched.sort(key=lambda chunk: best_score.get(chunk.chunk_id, 0.0), reverse=True)
        matched = matched[: config.inject_top_n]

    result.selected = _merge_basics(basic, matched, config)
    result.slot_best, result.hot_best, result.cold_best = best[TIER_SLOT], best[TIER_HOT], best[TIER_COLD]
    result.tier_reached = contributing[0] if contributing else TIER_COLD
    result.revived = revived
    return result
