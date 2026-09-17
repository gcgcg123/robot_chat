from __future__ import annotations

import math
import re
import os
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


class EmbeddingProvider:
    """Lazy, opt-in sentence-transformers provider; never downloads at startup."""
    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or os.getenv("IOT_EMBEDDING_MODEL", "").strip()
        self._model = None
        self._error = None

    @property
    def enabled(self) -> bool:
        return bool(self.model_name) and os.getenv("IOT_MEMORY_EMBEDDINGS", "0").lower() in {"1", "true", "yes", "on"}

    def status(self) -> dict:
        return {"provider": "sentence-transformers" if self.enabled else "lexical", "model": self.model_name or None, "status": "error" if self._error else ("ready" if self._model else "lazy")}

    def _load(self):
        if self._model is None and self._error is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self.model_name, local_files_only=True)
            except Exception as exc:
                self._error = str(exc)
        return self._model

    def score(self, query: str, text: str):
        model = self._load()
        if model is None:
            return None
        try:
            vectors = model.encode([query, text], normalize_embeddings=True)
            return float(sum(a * b for a, b in zip(vectors[0], vectors[1])))
        except Exception as exc:
            self._error = str(exc)
            return None


_embedding_provider = EmbeddingProvider()


def embedding_provider_status() -> dict:
    return _embedding_provider.status()


def visible_chunk(chunk: MemoryChunk, identity) -> bool:
    if not chunk.active or not chunk.approved:
        return False
    return chunk.owner_user_id is None or (getattr(identity, "decision", None) == "accepted" and chunk.owner_user_id == getattr(identity, "user_id", None))


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w\u4e00-\u9fff]+", text.lower()))


def retrieve(chunks: list[MemoryChunk], identity, query: str, limit: int = 4) -> list[MemoryChunk]:
    candidates = [c for c in chunks if visible_chunk(c, identity)]
    q = _tokens(query)
    embedding = _embedding_provider
    scored = []
    for c in candidates:
        score = embedding.score(query, c.text) if embedding.enabled else None
        if score is None:
            score = len(q & _tokens(c.text)) / max(1, len(q | _tokens(c.text)))
        scored.append((score, c))
    return [c for _, c in sorted(scored, key=lambda x: x[0], reverse=True)[:max(1, limit)]]
