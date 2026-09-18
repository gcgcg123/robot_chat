from __future__ import annotations

import math
import re
from typing import Any, Iterable, Sequence

from .schemas import MemoryChunk


class RAGProvider:
    """Optional retrieval boundary. Disabled by default for Phase 1.

    Project-specific knowledge is intentionally not bundled here; callers must
    explicitly enable and provide their own retrieval implementation in a
    later customization phase.
    """
    enabled = False

    def retrieve(self, query: str, *, user_id: str | None = None, identity=None, limit: int = 4) -> list[MemoryChunk]:
        return []


def visible_chunk(chunk: MemoryChunk, identity) -> bool:
    if not chunk.active or not chunk.approved:
        return False
    return chunk.owner_user_id is None or (getattr(identity, "decision", None) == "accepted" and chunk.owner_user_id == getattr(identity, "user_id", None))


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w\u4e00-\u9fff]+", text.lower()))


def bigrams(text: str) -> set[str]:
    """Character bigrams, which is what makes short Chinese comparable.

    ``_tokens`` groups a whole Chinese run into one token, so "我叫什麼名字" and
    "使用者叫絕絕子" share nothing at all and every Chinese pair scores 0.  Bigrams
    recover the overlap ("叫什" vs "者叫" at least share sub-pieces of 叫).
    """

    compact = re.sub(r"[\s\W_]+", "", text.lower())
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[i:i + 2] for i in range(len(compact) - 1)}


def lexical_similarity(left: str, right: str) -> float:
    """Jaccard overlap over the union of character bigrams and word tokens."""

    a = bigrams(left) | _tokens(left)
    b = bigrams(right) | _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def cosine(left: Sequence[float] | None, right: Sequence[float] | None) -> float:
    """Cosine similarity for stored unit vectors (falls back to a real cosine)."""

    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return float(dot / (norm_left * norm_right))


def score_chunk(
    chunk: MemoryChunk,
    query: str,
    query_vector: Sequence[float] | None = None,
    *,
    probe_weight: float = 1.0,
    text_weight: float = 0.25,
) -> float:
    """Relevance of one memory for this question.

    With embeddings, the **probe** (the canonical question the memory answers) is
    the primary signal: measured separation for question-vs-question is 0.215 at
    worst, versus 0.001 for question-vs-statement.  The statement text is kept as
    a small secondary signal, and lexical bigrams cover the no-embedding path.
    """

    if query_vector is not None and chunk.embedding is not None:
        probe_score = cosine(query_vector, chunk.embedding)
        text_score = lexical_similarity(query, chunk.text)
        return probe_weight * probe_score + text_weight * text_score
    return max(
        lexical_similarity(query, chunk.probe or ""),
        lexical_similarity(query, chunk.text),
    )


def rank_chunks(
    chunks: Iterable[MemoryChunk],
    query: str,
    query_vector: Sequence[float] | None = None,
) -> list[tuple[float, MemoryChunk]]:
    """Score and sort candidates, highest first (ties keep insertion order)."""

    scored = [(score_chunk(chunk, query, query_vector), chunk) for chunk in chunks]
    return sorted(scored, key=lambda item: item[0], reverse=True)


def select_relevant(
    chunks: Iterable[MemoryChunk],
    query: str,
    query_vector: Sequence[float] | None,
    *,
    floor: float,
    limit: int,
) -> list[MemoryChunk]:
    """Top ``limit`` candidates that clear ``floor`` (never empty-by-accident)."""

    ranked = rank_chunks(chunks, query, query_vector)
    picked = [chunk for score, chunk in ranked if score >= floor][: max(1, limit)]
    return picked


def retrieve(chunks: list[MemoryChunk], identity, query: str, limit: int = 4) -> list[MemoryChunk]:
    """Lexical-only retrieval kept for callers without an embedder."""

    candidates = [c for c in chunks if visible_chunk(c, identity)]
    scored = [(lexical_similarity(query, c.text), c) for c in candidates]
    return [c for _, c in sorted(scored, key=lambda x: x[0], reverse=True)[:max(1, limit)]]
