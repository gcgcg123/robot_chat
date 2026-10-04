from __future__ import annotations

from dataclasses import dataclass
import os
import time
from typing import Any, Callable, Sequence

import numpy as np

from services.knowledge import repository
from services.knowledge.rerank import DEFAULT_RERANK_BELOW, DEFAULT_RERANK_FLOOR, DEFAULT_RERANK_KEEP
from services.memory.retriever import score_chunk, split_queries
from services.memory.schemas import MemoryChunk

# Floors are measured, not guessed. On the 15-question calibration set of the first corpus
# (data/knowledge/calibration/), the lowest score of a correct answer is 0.6395 and the
# highest score of a question the corpus should *not* answer is 0.6016, so 0.623 sits between
# the two bands with ~0.02 margin on each side. The first draft of this file used 0.68/0.78,
# which the calibration rejected: it silently dropped 6 of 11 correct hits.
#
# 'scenario' and 'reference' stay separate knobs because they are separate kinds of text
# ("老年人不肯吃饭时怎么说" vs "什么是沟通"), and a corpus whose bands do separate can set
# them apart; on this corpus they do not, so both default to the measured midpoint.
DEFAULT_FLOOR_SCENARIO = 0.623
DEFAULT_FLOOR_REFERENCE = 0.623
# Cosine is only the *ranking* pre-filter here; the final score is the same hybrid function
# the memory flywheel uses (cosine + character bigrams). Scoring every chunk with Python
# would cost ~67 ms per 500 chunks, so only the best few per question are scored in Python.
CANDIDATE_POOL = 20
INJECT_TOP_N = 4
# A reranked pick may sit below the calibrated floor (that floor describes the weaker
# question-vs-statement signal), but never below this. Only used when the reranker cannot report
# its own scores (the LLM reranker picks indices, it does not score them).
RERANK_SANITY_FLOOR = 0.45


def _kind_of(provider: "KnowledgeProvider", chunk: MemoryChunk) -> str:
    return str(provider.chunk_detail(chunk).get("kind") or "scenario")


@dataclass(frozen=True)
class KnowledgeSettings:
    floor_scenario: float = DEFAULT_FLOOR_SCENARIO
    floor_reference: float = DEFAULT_FLOOR_REFERENCE
    inject_top_n: int = INJECT_TOP_N
    candidate_pool: int = CANDIDATE_POOL
    enabled: bool = True
    # Second stage. Retrieval is confident enough above `rerank_below`, so the reranker only
    # runs when the best hybrid score is weaker than that; see services/knowledge/rerank.py.
    # OFF by default: the local cross-encoder is measured to be a real improvement (A 12/14 ->
    # 13/14, whole set 26/28 -> 27/28, gate passing on its own score bands) but it costs ~8 s per
    # reranked turn on this 4-core laptop, which is too slow inside a companion conversation. It is
    # wired and switchable, not dead code: IOT_KNOWLEDGE_RERANK=local.
    rerank_enabled: bool = False
    rerank_below: float = DEFAULT_RERANK_BELOW
    rerank_keep: int = DEFAULT_RERANK_KEEP
    # Stage-2 admission threshold, on the reranker's own score when it reports one.
    rerank_floor: float = DEFAULT_RERANK_FLOOR
    # Max-window scoring (schema 8). OFF by default as well: measured, it lifts the hit band
    # (0.6243 -> 0.6463) and widens the margin (0.0017 -> 0.0057), but it promotes unrelated chunks
    # for one scenario question, dropping A from 86% to 79% -- below the gate. Kept behind a switch
    # because the band improvement is real and a stronger embedding model would likely keep both.
    windows_enabled: bool = False

    @classmethod
    def from_env(cls, env: dict | None = None) -> "KnowledgeSettings":
        source = os.environ if env is None else env

        def flag(name: str, default: bool) -> bool:
            raw = str(source.get(name, "")).strip().lower()
            return default if not raw else raw not in {"0", "false", "no", "off"}

        def number(name: str, default: float) -> float:
            raw = str(source.get(name, "")).strip()
            try:
                return float(raw) if raw else default
            except ValueError:
                return default

        return cls(
            floor_scenario=number("IOT_KNOWLEDGE_FLOOR_SCENARIO", DEFAULT_FLOOR_SCENARIO),
            floor_reference=number("IOT_KNOWLEDGE_FLOOR_REFERENCE", DEFAULT_FLOOR_REFERENCE),
            inject_top_n=int(number("IOT_KNOWLEDGE_INJECT_TOP_N", INJECT_TOP_N)),
            candidate_pool=int(number("IOT_KNOWLEDGE_CANDIDATE_POOL", CANDIDATE_POOL)),
            enabled=flag("IOT_KNOWLEDGE_ENABLED", True),
            rerank_enabled=flag("IOT_KNOWLEDGE_RERANK", False),
            rerank_below=number("IOT_KNOWLEDGE_RERANK_BELOW", DEFAULT_RERANK_BELOW),
            rerank_keep=int(number("IOT_KNOWLEDGE_RERANK_KEEP", DEFAULT_RERANK_KEEP)),
            rerank_floor=number("IOT_KNOWLEDGE_RERANK_FLOOR", DEFAULT_RERANK_FLOOR),
            windows_enabled=flag("IOT_KNOWLEDGE_WINDOWS", False),
        )

    def floor_for(self, kind: str) -> float:
        return self.floor_reference if kind == "reference" else self.floor_scenario


class KnowledgeProvider:
    """``providers["rag"]``: hybrid retrieval over the imported corpus.

    Contract required by ``pipeline._provider_chunks``: ``enabled`` plus
    ``retrieve(text, user_id=…, identity=…, limit=…)`` returning objects that carry
    ``text`` and ``source_id`` (the pipeline turns ``source_id`` into ``citations``).

    An empty or missing index leaves ``enabled`` False, so a checkout that never imported a
    corpus behaves exactly as before -- no prompt change, no error.
    """

    model_version = "knowledge-v1"

    def __init__(
        self,
        *,
        connection_factory: Callable[[], Any] | None = None,
        embedder: Callable[[Sequence[str]], Sequence[Sequence[float]]] | None = None,
        settings: KnowledgeSettings | None = None,
        index: repository.KnowledgeIndex | None = None,
        matrix_path: str | os.PathLike[str] | None = None,
        reranker: Any = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._embedder = embedder
        self._settings = settings or KnowledgeSettings.from_env()
        self._matrix_path = matrix_path
        self._reranker = reranker
        self._index: repository.KnowledgeIndex | None = index
        self._loaded_at = 0.0
        self._diagnostic = ""

    # -- lifecycle -----------------------------------------------------------------
    def refresh(self) -> repository.KnowledgeIndex:
        if self._connection_factory is None:
            return self._index or repository.KnowledgeIndex()
        try:
            with self._connection_factory() as conn:
                self._index = repository.load_index(conn, matrix_path=self._matrix_path)
            self._loaded_at = time.time()
            self._diagnostic = ""
        except Exception as exc:  # pragma: no cover - a broken/absent database
            self._index = repository.KnowledgeIndex()
            self._diagnostic = f"knowledge_index_unavailable:{type(exc).__name__}"
        return self._index

    @property
    def index(self) -> repository.KnowledgeIndex:
        if self._index is None:
            self.refresh()
        return self._index or repository.KnowledgeIndex()

    @property
    def enabled(self) -> bool:
        return bool(self._settings.enabled and self._embedder is not None and len(self.index) > 0)

    def stats(self) -> dict[str, Any]:
        index = self.index
        return {
            "enabled": self.enabled,
            "chunks": len(index),
            "mode": index.mode,
            "floor_scenario": self._settings.floor_scenario,
            "floor_reference": self._settings.floor_reference,
            "diagnostic": self._diagnostic,
            "rerank_enabled": bool(self._settings.rerank_enabled),
            "reranker": self._reranker_name(),
            "rerank_diagnostic": self._rerank_diagnostic(),
        }

    def _reranker_name(self) -> str:
        # "off" is the honest answer whenever stage 2 will not run, even if an object was built:
        # the lifespan builds from the same variable that enables it, and reporting a class name
        # that never executes is how a disabled second stage looks enabled.
        if self._reranker is None or not self._settings.rerank_enabled:
            return "off"
        return type(self._reranker).__name__

    def _rerank_diagnostic(self) -> str:
        """Why the second stage is not running, when the caller asked for it.

        Enabling ``IOT_KNOWLEDGE_RERANK=local`` without the 1.06 GB weights or without torch would
        otherwise look like a silent no-op -- the retrieval path simply stays on stage 1.
        """

        if not self._settings.rerank_enabled:
            return ""  # stage 2 is off by choice: there is nothing to diagnose
        if self._reranker is None:
            return "reranker_not_built"
        diagnose = getattr(self._reranker, "diagnose", None)
        if callable(diagnose):
            return str(diagnose() or "")
        if getattr(self._reranker, "available", True) is False:
            return str(getattr(self._reranker, "last_error", "") or "reranker_unavailable")
        return ""

    def floor_for(self, kind: str) -> float:
        return self._settings.floor_for(kind)

    # -- retrieval -----------------------------------------------------------------
    def _row(self, position: int) -> MemoryChunk:
        """Build one chunk on demand.

        Precomputing a MemoryChunk (and a 512-float tuple) for every row would cost hundreds
        of MB at the 100k-chunk scale the mmap path exists for; only the candidate pool is
        materialised, per question.
        """

        index = self.index
        if index.matrix is not None:
            embedding = tuple(float(value) for value in np.asarray(index.matrix[position]).tolist())
        else:  # pragma: no cover - matrices are always loaded
            embedding = None
        # ``embedding`` is the *probe's* vector when the chunk has one, so score_chunk()'s cosine
        # is question-vs-question; the statement is still used for the small lexical term.
        probe = index.probes[position] if position < len(index.probes) else None
        return MemoryChunk(
            chunk_id=index.chunk_ids[position],
            owner_user_id=None,
            text=index.texts[position],
            source_id=index.source_ids[position],
            source_kind="knowledge",
            embedding=embedding,
            probe=probe,
        )

    def _embed_queries(self, queries: Sequence[str]) -> list[Sequence[float]]:
        if self._embedder is None:
            return []
        try:
            return [list(vector) for vector in self._embedder(list(queries))]
        except Exception:  # pragma: no cover - provider failure degrades to no retrieval
            return []

    def _rank_question(self, question: str, vector: Sequence[float]) -> list[tuple[float, int]]:
        """(score, position) for the candidate pool of one question, best first.

        Cosine over the whole matrix picks the pool; the hybrid function then re-scores just
        those, so the Python cost stays proportional to the pool rather than the corpus.
        """

        index = self.index
        if index.matrix is None or len(index) == 0:
            return []
        query = np.asarray(vector, dtype=np.float32)
        if query.shape[0] != index.matrix.shape[1]:
            return []
        cosine = index.matrix @ query
        pool = min(self._settings.candidate_pool, len(index))
        if pool < len(index):
            positions = np.argpartition(-cosine, pool - 1)[:pool]
        else:
            positions = np.arange(len(index))
        # Max-window scoring applies to the *already selected* pool only. Letting windows choose the
        # pool as well was measured to drag unrelated chunks in (a dementia question pulled
        # "沟通的层次"), because a max over ~5x more vectors also creates ~5x more chances for a
        # spurious high match.
        scoring = cosine
        if self._settings.windows_enabled and index.window_matrix is not None and len(index.window_owner):
            window_cosine = index.window_matrix @ query
            best_window: dict[int, float] = {}
            for position, score in zip(index.window_owner, window_cosine.tolist()):
                if score > best_window.get(position, -1.0):
                    best_window[position] = float(score)
            scoring = cosine.copy()
            for position in positions:
                window_score = best_window.get(int(position))
                if window_score is not None and window_score > scoring[position]:
                    scoring[position] = window_score
        scored = [
            (
                score_chunk(
                    self._row(int(position)),
                    question,
                    vector,
                    embedding_override=float(scoring[int(position)]),
                ),
                int(position),
            )
            for position in positions
        ]
        return sorted(scored, key=lambda item: item[0], reverse=True)

    def rank(self, text: str, *, limit: int = 5) -> list[tuple[float, MemoryChunk]]:
        """Best candidates with scores, ignoring the floors.

        Used by ``scripts/calibrate-knowledge.py`` to measure the hit and miss bands the
        floors are derived from; retrieval itself never bypasses a floor.
        """

        questions = split_queries(text) or ([text.strip()] if text.strip() else [])
        vectors = self._embed_queries(questions)
        if not vectors:
            return []
        best: dict[int, float] = {}
        for question, vector in zip(questions, vectors):
            for score, position in self._rank_question(question, vector):
                best[position] = max(best.get(position, 0.0), score)
        ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)[:limit]
        return [(score, self._row(position)) for position, score in ranked]

    def retrieve(
        self,
        text: str,
        *,
        user_id: str | None = None,
        identity: Any = None,
        limit: int | None = None,
        trace: dict[str, Any] | None = None,
    ) -> list[MemoryChunk]:
        """Chunks relevant to ``text``, per question, above the kind's floor.

        The corpus is shared, non-personal knowledge, so ``user_id`` and ``identity`` filter
        nothing here; they stay in the signature because the pipeline passes them and a
        future per-user knowledge domain must not need a new contract.

        ``trace`` is filled with the stage-2 decision (``reranked`` and ``rerank_scores`` keyed by
        chunk id) for diagnostics -- the calibration script reads the reranker's own bands from it,
        because those, not the hybrid scores, are what admit a candidate once stage 2 has run.
        """

        if trace is not None:
            trace.clear()
            trace.update({"reranked": False, "rerank_scores": {}})

        index = self.index
        if not self.enabled or index.matrix is None or len(index) == 0:
            return []

        questions = split_queries(text) or ([text.strip()] if text.strip() else [])
        vectors = self._embed_queries(questions)
        if not vectors:
            return []

        budget = limit if limit is not None else self._settings.inject_top_n
        # Stage 1: hybrid ranking, merged across the sub-questions of the turn.
        pooled = self.rank(text, limit=max(self._settings.candidate_pool, budget))
        if not pooled:
            return []

        # Stage 2: when the best hybrid score is not convincing, let the reranker read the pool.
        # It fixes "the answer is here but ranked below weaker matches" and also suppresses the
        # occasional weak match that clears the floor; both were measured at this corpus size.
        reranked = False
        selected = pooled
        rerank_scores: dict[str, float] = {}
        if (
            self._settings.rerank_enabled
            and self._reranker is not None
            and pooled[0][0] < self._settings.rerank_below
        ):
            texts = [chunk.text for _score, chunk in pooled]
            # Prefer a reranker that reports how relevant each passage is: the admission test then
            # uses that signal instead of the hybrid score that already failed to rank the answer.
            scorer = getattr(self._reranker, "rank", None)
            scored = scorer(questions[0], texts) if callable(scorer) else None
            if scored is None:
                picked = self._reranker.choose(questions[0], texts)
                keep = max(1, self._settings.rerank_keep)
                if picked is not None:
                    reranked = True
                    # Only the head of the new ordering is trusted; how deep that goes is
                    # `rerank_keep`, not the reranker's own opinion (a fixed three inside the
                    # cross-encoder once dropped a correct chunk that sat at rank four).
                    selected = [(pooled[index][0], pooled[index][1]) for index in picked[:keep]]
            else:
                reranked = True
                rerank_scores = {pooled[index][1].chunk_id: float(score) for index, score in scored}
                keep = max(1, self._settings.rerank_keep)
                selected = [
                    (pooled[index][0], pooled[index][1])
                    for index, _score in scored[:keep]
                ]
        if trace is not None:
            trace.update({"reranked": reranked, "rerank_scores": rerank_scores})

        chosen: list[MemoryChunk] = []
        for score, chunk in selected:
            if reranked:
                # Once the reranker has read the question and the passage together, *its* verdict is
                # the admission test -- the hybrid floor is the signal that failed to rank the answer
                # in the first place, and using it here re-admitted a true negative (measured 0.6226).
                # A reranker that only picks indices (the LLM one) gets the looser sanity bound
                # instead, so nothing can be picked out of nowhere.
                if rerank_scores:
                    if rerank_scores.get(chunk.chunk_id, float("-inf")) < self._settings.rerank_floor:
                        continue
                elif score < RERANK_SANITY_FLOOR:
                    continue
            elif score < self._settings.floor_for(_kind_of(self, chunk)):
                continue
            chosen.append(chunk)
            if len(chosen) >= max(1, budget):
                break
        return chosen

    def chunk_detail(self, chunk: MemoryChunk) -> dict[str, Any]:
        """Kind and page for a returned chunk, for reports and calibration output."""

        index = self.index
        try:
            position = index.chunk_ids.index(chunk.chunk_id)
        except ValueError:  # pragma: no cover - chunk came from another index
            return {}
        return {"kind": index.kinds[position], "page": index.pages[position], "chunk_id": chunk.chunk_id}


def create_knowledge_provider(
    connection_factory: Callable[[], Any] | None = None,
    embedder: Callable[[Sequence[str]], Sequence[Sequence[float]]] | None = None,
    *,
    matrix_path: str | os.PathLike[str] | None = None,
    settings: KnowledgeSettings | None = None,
    reranker: Any = None,
) -> KnowledgeProvider:
    provider = KnowledgeProvider(
        connection_factory=connection_factory,
        embedder=embedder,
        matrix_path=matrix_path,
        settings=settings,
        reranker=reranker,
    )
    if connection_factory is not None:
        provider.refresh()
    return provider
