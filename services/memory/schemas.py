from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryChunk:
    chunk_id: str
    owner_user_id: str | None
    text: str
    source_id: str
    source_kind: str
    approved: bool = False
    active: bool = True

