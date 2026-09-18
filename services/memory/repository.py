"""Read and write access to the tiered long-term memory store.

Memory belongs to the *person being accompanied* (the ``users`` row), not to the
operator logged into the Dashboard.  ``user_id`` is therefore the only scope this
layer needs, and ``memory_chunks.owner_user_id`` is a real foreign key so a
memory can never hang off a non-existent account.

Visibility is NOT decided here: ``services.memory.retriever`` applies
``visible_chunk``, which is the single gate for ``approved``, ``active`` and
identity.  This module drops only what a turn can never use -- hard-deleted rows
and other users' chunks -- and manages the slot/hot/cold tiers on top.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Callable, Iterable, Sequence

from services.memory.embeddings import cosine_similarity
from services.memory.flywheel import (
    MemorySettings,
    effective_score,
    next_tier,
)
from services.memory.schemas import TIER_COLD, TIER_HOT, TIER_SLOT, TIERS, MemoryChunk

_COLUMNS = (
    "chunk_id, owner_user_id, text, source_id, source_kind, approved, active, "
    "probe, slot_key, tier, importance, hits, last_hit_at, created_at, embedding_json"
)

# Bound the candidate set so a long-lived account cannot build an unbounded prompt.
MAX_CANDIDATES = 500

# How many of a user's rows the write path compares against for near-duplicates.
# Only rows that already carry a vector take part, and the newest ones are the
# plausible matches, so a bounded scan keeps a write O(limit) without losing
# anything a re-statement would realistically hit.
NEAR_DUPLICATE_SCAN = 200

Embedder = Callable[[Sequence[str]], list[list[float]]]


def _encode_embedding(values: Iterable[float] | None) -> str | None:
    if not values:
        return None
    return json.dumps([round(float(v), 6) for v in values])


def _decode_embedding(raw: Any) -> tuple[float, ...] | None:
    if not raw:
        return None
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(values, list) or not values:
        return None
    try:
        return tuple(float(v) for v in values)
    except (TypeError, ValueError):
        return None


def _to_chunk(row: Any, *, now: float, settings: MemorySettings) -> MemoryChunk:
    importance = float(row["importance"] if row["importance"] is not None else settings.default_importance)
    hits = int(row["hits"] or 0)
    created_at = float(row["created_at"] or now)
    last_hit_at = row["last_hit_at"]
    score = effective_score(
        importance=importance,
        hits=hits,
        last_hit_at=last_hit_at,
        created_at=created_at,
        now=now,
        settings=settings,
    )
    return MemoryChunk(
        chunk_id=row["chunk_id"],
        owner_user_id=row["owner_user_id"],
        text=row["text"],
        source_id=row["source_id"],
        source_kind=row["source_kind"],
        approved=bool(row["approved"]),
        active=bool(row["active"]),
        tier=str(row["tier"] or TIER_HOT),
        score=score,
        hits=hits,
        importance=importance,
        slot_key=row["slot_key"],
        probe=row["probe"],
        embedding=_decode_embedding(row["embedding_json"]),
    )


def load_tier(
    conn: Any,
    user_id: str | None,
    tier: str,
    *,
    now: float | None = None,
    settings: MemorySettings | None = None,
    limit: int = MAX_CANDIDATES,
) -> list[MemoryChunk]:
    """Rows of one tier owned by this user, newest first."""

    config = settings or MemorySettings()
    moment = time.time() if now is None else now
    if not user_id:
        return []
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM memory_chunks "
        "WHERE active = 1 AND owner_user_id = ? AND tier = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (user_id, tier, limit),
    ).fetchall()
    return [_to_chunk(row, now=moment, settings=config) for row in rows]


def load_shared(
    conn: Any,
    *,
    now: float | None = None,
    settings: MemorySettings | None = None,
    limit: int = MAX_CANDIDATES,
) -> list[MemoryChunk]:
    """Administrator-owned shared knowledge (``owner_user_id IS NULL``)."""

    config = settings or MemorySettings()
    moment = time.time() if now is None else now
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM memory_chunks "
        "WHERE active = 1 AND owner_user_id IS NULL ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_to_chunk(row, now=moment, settings=config) for row in rows]


def load_memories(conn: Any, user_id: str | None, *, now: float | None = None, settings: MemorySettings | None = None) -> list[MemoryChunk]:
    """Every candidate this turn may reach: this user's rows plus shared ones.

    Kept for callers that want one flat list; the tier cascade uses
    :func:`load_tier` so it can stop early.
    """

    config = settings or MemorySettings()
    moment = time.time() if now is None else now
    chunks = load_shared(conn, now=moment, settings=config)
    if user_id:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM memory_chunks "
            "WHERE active = 1 AND owner_user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, MAX_CANDIDATES),
        ).fetchall()
        chunks.extend(_to_chunk(row, now=moment, settings=config) for row in rows)
    return chunks


def recall(
    conn: Any,
    user_id: str | None,
    *,
    now: float | None = None,
    settings: MemorySettings | None = None,
    allowed_tiers: Sequence[str] = (TIER_SLOT, TIER_HOT, TIER_COLD),
) -> list[MemoryChunk]:
    """All tiers in cascade order: slots, then hot, then cold."""

    config = settings or MemorySettings()
    moment = time.time() if now is None else now
    chunks = load_shared(conn, now=moment, settings=config)
    for tier in allowed_tiers:
        if tier not in TIERS:
            continue
        chunks.extend(load_tier(conn, user_id, tier, now=moment, settings=config))
    return chunks


def duplicate_groups(
    conn: Any,
    user_id: str,
    *,
    settings: MemorySettings | None = None,
    limit: int = NEAR_DUPLICATE_SCAN,
) -> list[list[dict[str, Any]]]:
    """Group rows that are the same fact stored more than once.

    ``upsert_memory`` prevents new duplicates, but rows created before the merge
    pass existed (or below its threshold) can still be sitting there.  Grouping is
    a single greedy pass newest-first: a row joins the first group whose
    representative it is similar enough to, which is O(n * groups) and only ever
    compares probes.

    Only groups with two or more members are returned.
    """

    config = settings or MemorySettings()
    rows = conn.execute(
        "SELECT chunk_id, text, probe, slot_key, tier, importance, hits, approved, created_at, last_hit_at, embedding_json "
        "FROM memory_chunks WHERE owner_user_id=? AND active=1 AND embedding_json IS NOT NULL "
        "ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()

    groups: list[tuple[list[float], list[dict[str, Any]]]] = []
    for row in rows:
        vector = _decode_embedding(row["embedding_json"])
        if vector is None:
            continue
        item = {key: row[key] for key in row.keys() if key != "embedding_json"}
        for representative, members in groups:
            if cosine_similarity(list(vector), representative) >= config.merge_similarity:
                members.append(item)
                break
        else:
            groups.append((list(vector), [item]))
    return [members for _representative, members in groups if len(members) > 1]


def _find_near_duplicate(
    conn: Any,
    user_id: str,
    embedding: Sequence[float] | None,
    *,
    settings: MemorySettings | None = None,
    limit: int = NEAR_DUPLICATE_SCAN,
) -> tuple[Any, float]:
    """Find the user's row whose probe is almost the same question.

    This exists because the model does not reliably reuse slot keys: one hobby has
    been stored as ``hobby``, ``guitar``, ``hobby_guitar`` and ``instrument``.  Key
    and text equality cannot see that, but the probe vectors can.

    The threshold is deliberately conservative -- see ``MemorySettings.merge_similarity``
    for the measured bands.  Because "one word different, different fact" (my dog's
    name vs my cat's name) scores up to 0.826 while honest paraphrases can be as low
    as 0.609, a low threshold would destroy distinct memories; only near-identical
    probes are merged, and paraphrases are left to the extractor's key reuse.
    """

    config = settings or MemorySettings()
    if embedding is None or config.merge_similarity > 1.0:
        return None, 0.0
    rows = conn.execute(
        "SELECT chunk_id, text, probe, slot_key, importance, hits, approved, embedding_json "
        "FROM memory_chunks WHERE owner_user_id=? AND embedding_json IS NOT NULL "
        "ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()

    best, best_score = None, 0.0
    for row in rows:
        stored = _decode_embedding(row["embedding_json"])
        if stored is None:
            continue
        score = cosine_similarity(list(embedding), list(stored))
        if score > best_score:
            best, best_score = row, score
    if best is not None and best_score >= config.merge_similarity:
        return best, best_score
    return None, best_score


def upsert_memory(
    conn: Any,
    *,
    user_id: str,
    text: str,
    probe: str | None = None,
    slot_key: str | None = None,
    importance: float = 0.5,
    approved: bool = False,
    source_id: str = "conversation",
    source_kind: str = "auto",
    embedding: Sequence[float] | None = None,
    now: float | None = None,
    settings: MemorySettings | None = None,
) -> dict[str, Any]:
    """Insert a memory, or refresh the existing one that already holds this fact.

    Re-stating a known fact must not pile up near-duplicates, so a proposal is
    matched against the user's rows in three passes:

    1. the same ``slot_key`` -- the identity the model is asked to reuse;
    2. an identical ``text`` -- the same statement with no key to match on;
    3. a near-identical **probe** (see :func:`_find_near_duplicate`), which is what
       catches a re-statement filed under a different key.

    A match is updated in place: accumulated ``hits`` survive, ``importance`` takes
    the higher value, and ``approved`` is only ever raised.
    """

    config = settings or MemorySettings()
    moment = time.time() if now is None else now
    probe_value = (probe or text)
    encoded = _encode_embedding(embedding)

    existing = None
    action = "updated"
    similarity = 0.0
    if slot_key:
        existing = conn.execute(
            "SELECT chunk_id, text, probe, slot_key, importance, hits, approved, embedding_json FROM memory_chunks "
            "WHERE owner_user_id=? AND slot_key=?",
            (user_id, slot_key),
        ).fetchone()
    if existing is None:
        existing = conn.execute(
            "SELECT chunk_id, text, probe, slot_key, importance, hits, approved, embedding_json FROM memory_chunks "
            "WHERE owner_user_id=? AND text=?",
            (user_id, text),
        ).fetchone()
    if existing is None:
        existing, similarity = _find_near_duplicate(conn, user_id, embedding, settings=config)
        if existing is not None:
            action = "merged"

    if existing is not None:
        merged_importance = max(float(existing["importance"] or 0.0), float(importance))
        # An unverified paraphrase must not rewrite a statement the user confirmed
        # in their own words; only the retrieval key (probe) and the bookkeeping
        # move.  A re-statement the user actually said does replace the text.
        keep_verified_text = bool(existing["approved"]) and not bool(approved)
        merged_text = existing["text"] if keep_verified_text else text
        merged_probe = existing["probe"] if keep_verified_text else probe_value
        # A vector is valid only for the probe that produced it. Preserve both
        # when rejecting a proposal; clear stale vectors to allow lexical recall.
        merged_embedding = encoded
        if keep_verified_text or (encoded is None and merged_probe == existing["probe"]):
            merged_embedding = existing["embedding_json"]
        conn.execute(
            "UPDATE memory_chunks SET text=?, probe=?, slot_key=COALESCE(?, slot_key), importance=?, "
            "approved=MAX(approved, ?), active=1, updated_at=?, "
            "embedding_json=? WHERE chunk_id=?",
            (merged_text, merged_probe, slot_key, merged_importance, int(bool(approved)), moment, merged_embedding, existing["chunk_id"]),
        )
        conn.commit()
        return {"chunk_id": existing["chunk_id"], "action": action, "similarity": round(similarity, 4)}

    chunk_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO memory_chunks(chunk_id, owner_user_id, text, source_id, source_kind, approved, "
        "active, embedding_json, created_at, probe, slot_key, tier, importance, hits, last_hit_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            chunk_id, user_id, text, source_id, source_kind, int(bool(approved)), 1, encoded, moment,
            probe_value, slot_key, TIER_HOT, float(importance), 0, None, moment,
        ),
    )
    conn.commit()
    return {"chunk_id": chunk_id, "action": "inserted"}


def bump_hits(conn: Any, chunk_ids: Iterable[str], *, now: float | None = None) -> int:
    """Reward the memories that actually reached the prompt."""

    ids = [str(chunk_id) for chunk_id in chunk_ids if chunk_id]
    if not ids:
        return 0
    moment = time.time() if now is None else now
    placeholders = ",".join("?" for _ in ids)
    cursor = conn.execute(
        f"UPDATE memory_chunks SET hits = hits + 1, last_hit_at = ?, updated_at = ? WHERE chunk_id IN ({placeholders})",
        [moment, moment, *ids],
    )
    conn.commit()
    return int(cursor.rowcount or 0)


def evaluate_tiers(
    conn: Any,
    user_id: str,
    *,
    now: float | None = None,
    settings: MemorySettings | None = None,
) -> dict[str, int]:
    """Recompute decayed scores, then move rows between tiers.

    Returns a small report (``promoted`` / ``demoted`` / ``revived`` /
    ``slot_overflow``) so a caller can log or assert on what the flywheel did.
    """

    config = settings or MemorySettings()
    moment = time.time() if now is None else now
    report = {"promoted": 0, "demoted": 0, "revived": 0, "slot_overflow": 0}

    rows = conn.execute(
        "SELECT chunk_id, tier, importance, hits, last_hit_at, created_at FROM memory_chunks "
        "WHERE active = 1 AND owner_user_id = ?",
        (user_id,),
    ).fetchall()
    for row in rows:
        current = str(row["tier"] or TIER_HOT)
        target, score = next_tier(
            current_tier=current,
            importance=float(row["importance"] or config.default_importance),
            hits=int(row["hits"] or 0),
            last_hit_at=row["last_hit_at"],
            created_at=float(row["created_at"] or moment),
            now=moment,
            settings=config,
        )
        if target == current:
            continue
        if target == TIER_SLOT and current != TIER_SLOT:
            report["promoted"] += 1
        elif target == TIER_HOT and current == TIER_COLD:
            report["revived"] += 1
        elif target == TIER_COLD:
            report["demoted"] += 1
        conn.execute("UPDATE memory_chunks SET tier=?, updated_at=? WHERE chunk_id=?", (target, moment, row["chunk_id"]))

    # Keep the slot tier bounded: the lowest-scoring slots fall back to hot.
    slots = conn.execute(
        "SELECT chunk_id, importance, hits, last_hit_at, created_at FROM memory_chunks "
        "WHERE active = 1 AND owner_user_id = ? AND tier = ?",
        (user_id, TIER_SLOT),
    ).fetchall()
    if len(slots) > config.slot_capacity:
        scored = sorted(
            (
                (
                    effective_score(
                        importance=float(row["importance"] or config.default_importance),
                        hits=int(row["hits"] or 0),
                        last_hit_at=row["last_hit_at"],
                        created_at=float(row["created_at"] or moment),
                        now=moment,
                        settings=config,
                    ),
                    row["chunk_id"],
                )
                for row in slots
            ),
            key=lambda item: item[0],
        )
        for _score, chunk_id in scored[: len(slots) - config.slot_capacity]:
            conn.execute("UPDATE memory_chunks SET tier=?, updated_at=? WHERE chunk_id=?", (TIER_HOT, moment, chunk_id))
            report["slot_overflow"] += 1

    conn.commit()
    return report


def apply_turn(
    conn: Any,
    *,
    user_id: str,
    used_chunk_ids: Iterable[str] = (),
    candidates: Iterable[dict[str, Any]] = (),
    embedder: Embedder | None = None,
    now: float | None = None,
    settings: MemorySettings | None = None,
) -> dict[str, Any]:
    """Close a turn: score what was used, store what was proposed, retier.

    This is the write half of the flywheel.  Scoring first means a memory that
    was just used can be promoted in the same turn.
    """

    config = settings or MemorySettings()
    moment = time.time() if now is None else now

    hits = bump_hits(conn, used_chunk_ids, now=moment)

    stored: list[dict[str, Any]] = []
    proposals = list(candidates or [])
    if proposals and config.auto_extract:
        probes = [item.get("probe") or item.get("text") or "" for item in proposals]
        vectors: list[list[float]] = []
        if embedder is not None and probes:
            try:
                vectors = embedder(probes)
            except Exception:
                vectors = []
        for index, item in enumerate(proposals):
            vector = vectors[index] if index < len(vectors) else None
            stored.append(
                upsert_memory(
                    conn,
                    user_id=user_id,
                    text=item["text"],
                    probe=item.get("probe"),
                    slot_key=item.get("slot_key"),
                    importance=float(item.get("importance", config.default_importance)),
                    approved=bool(item.get("approved")),
                    source_id=item.get("source_id", "conversation"),
                    source_kind=item.get("source_kind", "auto"),
                    embedding=vector,
                    now=moment,
                    settings=config,
                )
            )

    tiers = evaluate_tiers(conn, user_id, now=moment, settings=config)
    return {"hit": hits, "stored": stored, "tiers": tiers}


def set_tier(conn: Any, chunk_id: str, tier: str, *, now: float | None = None) -> None:
    """Force a tier (used by the Dashboard memory panel)."""

    if tier not in TIERS:
        raise ValueError("invalid_tier")
    moment = time.time() if now is None else now
    conn.execute("UPDATE memory_chunks SET tier=?, updated_at=? WHERE chunk_id=?", (tier, moment, chunk_id))
    conn.commit()


def tier_counts(conn: Any, user_id: str) -> dict[str, int]:
    counts = {tier: 0 for tier in TIERS}
    for row in conn.execute(
        "SELECT tier, COUNT(*) AS n FROM memory_chunks WHERE active=1 AND owner_user_id=? GROUP BY tier",
        (user_id,),
    ):
        name = str(row["tier"] or TIER_HOT)
        counts[name] = int(row["n"])
    return counts
