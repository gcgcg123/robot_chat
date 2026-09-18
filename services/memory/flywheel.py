"""Score decay and tier transitions for the tiered long-term memory store.

The design is a small cache hierarchy per user:

    slot (<= 32, basic facts + currently salient topics)
      ^ promote when the score crosses a threshold
    hot  (recently remembered, scored)
      v demote when the score decays below a threshold
    cold (pseudo-deleted; only searched when slot and hot find nothing)
      ^ a hit revives it back into hot

Scores **decay lazily**: ``effective_score()`` computes the decayed value from
``last_hit_at`` on every read, so there is no background job, no cron and no
migration of stored values.  A memory nobody mentions simply keeps losing score
until it falls out of ``hot``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from services.memory.schemas import TIER_COLD, TIER_HOT, TIER_SLOT, TIERS

SECONDS_PER_DAY = 86400.0

_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class MemorySettings:
    """Tunables for the flywheel. Defaults come from the calibration run."""

    slot_capacity: int = 32
    promote_score: float = 1.5
    demote_score: float = 0.2
    half_life_days: float = 14.0
    hit_bonus: float = 0.5
    default_importance: float = 0.5
    relevance_floor: float = 0.68
    # Safety cap on how many memories one turn may inject (basic keys are extra).
    # Every memory that clears the floor is relevant to some question the user asked,
    # so this is only a guard rail against a request that matches a large part of a
    # long-lived account's memory -- not a ranking cut. When it does bite, the
    # highest-scoring memories are kept.
    inject_top_n: int = 12
    # How many basic keys (name, ...) may be injected unconditionally.
    slot_top_n: int = 4
    auto_extract: bool = True
    max_candidates_per_turn: int = 3
    # Write-path de-duplication. The model is asked to reuse a slot key for the
    # same fact, but it does not always comply ("hobby"/"guitar"/"instrument" for
    # one hobby), so a proposal whose probe is *almost the same question* as an
    # existing row is merged into it instead of inserted as a near-duplicate.
    #
    # 0.90 is a measured bound, not a guess. Calibration with the shipped BGE
    # model (recorded in docs/MEMORY_FLYWHEEL.md and pinned by
    # tests/memory/test_embeddings.py::test_real_model_bands_justify_the_merge_threshold):
    #   same fact, reworded   0.6086 .. 0.9564
    #   one word apart, DIFFERENT fact (狗/貓, 吉他/鋼琴)
    #                         0.6573 .. 0.8258
    #   unrelated             0.3680 .. 0.4505
    # The bands overlap, so a low threshold would merge "my dog's name" into "my
    # cat's name". 0.90 keeps 0.074 of headroom over the worst near-miss and
    # therefore only merges near-identical probes; the paraphrases it misses are
    # the reason the extractor is also asked to reuse keys. > 1.0 disables merging.
    merge_similarity: float = 0.90
    # A matching key or probe only makes a row a *candidate* for merging. Whether the
    # two rows really are the same fact is decided by the similarity of their
    # statements, because a key is often a category rather than a fact: "我學會了
    # 唱跳rap籃球" and "我學會了寫歌詞" share the key `skill` and the probe
    # "我最近學會了什麼？", so key and probe both say "same" while the statements are
    # plainly two different skills. Calibration on statement embeddings:
    #   same fact restated    0.8234 .. 0.9738
    #   different fact        0.4479 .. 0.7646
    # 0.80 sits between them (lexical bigrams overlap and cannot be used here).
    # Single-valued keys (see SINGLE_VALUED_SLOT_KEYS) bypass this, so a correction
    # like "我不叫X，我叫Y" still replaces the old value.
    statement_merge_similarity: float = 0.80

    @classmethod
    def from_env(cls, env: dict | None = None) -> "MemorySettings":
        source = os.environ if env is None else env

        def number(name: str, fallback: float) -> float:
            raw = str(source.get(name, "")).strip()
            try:
                return float(raw) if raw else fallback
            except ValueError:
                return fallback

        def integer(name: str, fallback: int) -> int:
            raw = str(source.get(name, "")).strip()
            return int(raw) if raw.isdigit() and int(raw) > 0 else fallback

        return cls(
            slot_capacity=integer("IOT_MEMORY_SLOT_CAPACITY", 32),
            promote_score=number("IOT_MEMORY_PROMOTE_SCORE", 1.5),
            demote_score=number("IOT_MEMORY_DEMOTE_SCORE", 0.2),
            half_life_days=number("IOT_MEMORY_HALF_LIFE_DAYS", 14.0),
            hit_bonus=number("IOT_MEMORY_HIT_BONUS", 0.5),
            relevance_floor=number("IOT_MEMORY_RELEVANCE_FLOOR", 0.68),
            inject_top_n=integer("IOT_MEMORY_INJECT_TOP_N", 12),
            slot_top_n=integer("IOT_MEMORY_SLOT_TOP_N", 4),
            auto_extract=str(source.get("IOT_MEMORY_AUTO_EXTRACT", "1")).strip().lower() in _TRUTHY,
            max_candidates_per_turn=integer("IOT_MEMORY_MAX_CANDIDATES", 3),
            merge_similarity=number("IOT_MEMORY_MERGE_SIMILARITY", 0.90),
            statement_merge_similarity=number("IOT_MEMORY_STATEMENT_MERGE_SIMILARITY", 0.80),
        )


def effective_score(
    *,
    importance: float,
    hits: int,
    last_hit_at: float | None,
    created_at: float,
    now: float,
    settings: MemorySettings | None = None,
) -> float:
    """Return the lazily decayed score of one memory.

    ``last_hit_at`` (falling back to ``created_at``) anchors the decay, so the
    value is a pure function of the row plus the current time.
    """

    config = settings or MemorySettings()
    anchor = last_hit_at if last_hit_at else created_at
    age_days = max(0.0, (now - anchor) / SECONDS_PER_DAY)
    raw = max(0.0, float(importance)) + max(0, int(hits)) * config.hit_bonus
    if config.half_life_days <= 0:
        return raw
    return raw * (0.5 ** (age_days / config.half_life_days))


def tier_for_score(score: float, settings: MemorySettings | None = None) -> str:
    """Map a score onto its tier (the only place tier rules live)."""

    config = settings or MemorySettings()
    if score >= config.promote_score:
        return TIER_SLOT
    if score < config.demote_score:
        return TIER_COLD
    return TIER_HOT


def next_tier(
    *,
    current_tier: str,
    importance: float,
    hits: int,
    last_hit_at: float | None,
    created_at: float,
    now: float,
    settings: MemorySettings | None = None,
) -> tuple[str, float]:
    """Return ``(tier, score)`` after applying decay and the thresholds."""

    config = settings or MemorySettings()
    score = effective_score(
        importance=importance,
        hits=hits,
        last_hit_at=last_hit_at,
        created_at=created_at,
        now=now,
        settings=config,
    )
    if current_tier not in TIERS:
        current_tier = TIER_HOT
    target = tier_for_score(score, config)
    # Revival is a two-step climb.  A cold row that is mentioned again returns to
    # hot memory and has to be mentioned again to earn a slot back; without this
    # clamp a long-forgotten row carrying an old hit count would skip the hot tier
    # entirely the moment it was found again.  Scores only fall while a row sits
    # untouched, so this can never trigger without an actual hit.
    if current_tier == TIER_COLD and target == TIER_SLOT:
        target = TIER_HOT
    return target, score


def rank_slots(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order slot rows by score, highest first (stable, so tests are repeatable)."""

    return sorted(rows, key=lambda row: float(row.get("score") or 0.0), reverse=True)
