from __future__ import annotations

import math
import re
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


def retrieve(chunks: list[MemoryChunk], identity, query: str, limit: int = 4) -> list[MemoryChunk]:
    candidates = [c for c in chunks if visible_chunk(c, identity)]
    q = _tokens(query)
    scored = [(len(q & _tokens(c.text)) / max(1, len(q | _tokens(c.text))), c) for c in candidates]
    return [c for _, c in sorted(scored, key=lambda x: x[0], reverse=True)[:max(1, limit)]]
