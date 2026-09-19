"""Local Chinese text embeddings through ONNX (BAAI/bge-small-zh-v1.5).

Why ONNX instead of sentence-transformers: that package pulls PyTorch (~2.5 GB)
onto a CPU-only laptop.  This project already has ``onnxruntime`` (faster-whisper
uses it for VAD) and ``tokenizers``, so the whole embedding layer costs one extra
model file and **no new dependency**.

BGE models are trained with CLS pooling and are used L2-normalised, so cosine
similarity is a plain dot product.  Retrieval *queries* get the model's
instruction prefix while documents (here: memory statements) do not -- that is
how bge-*-v1.5 was trained.

The provider is deliberately optional: if the model files are missing the object
reports ``available is False`` and the caller falls back to the lexical scorer,
so a machine that never downloaded the model still runs.
"""
from __future__ import annotations

import math
import os
import threading
from pathlib import Path
from typing import Any, Sequence

DEFAULT_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
DEFAULT_MAX_LENGTH = 128
MODEL_FILE = "onnx/model.onnx"
TOKENIZER_FILE = "tokenizer.json"
# Relative to the checkout root (see create_embedding_provider); the single place
# that names the on-disk location, so tests can find it on any machine.
DEFAULT_MODEL_PATH = "models/embedding/bge-small-zh-v1.5"


def l2_normalise(values: Sequence[float]) -> list[float]:
    """Scale a vector to unit length (zero vectors are returned unchanged)."""

    norm = math.sqrt(sum(float(v) * float(v) for v in values))
    if norm == 0.0:
        return [0.0 for _ in values]
    return [float(v) / norm for v in values]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity; both sides are expected to be L2-normalised already."""

    if not left or not right or len(left) != len(right):
        return 0.0
    return float(sum(a * b for a, b in zip(left, right)))


def resolve_model_files(model_path: str | Path) -> tuple[Path, Path]:
    """Locate the ONNX graph and its tokenizer inside a model directory."""

    root = Path(model_path)
    graph = root / MODEL_FILE
    tokenizer = root / TOKENIZER_FILE
    if not graph.exists():
        raise FileNotFoundError(f"embedding model not found: {graph}")
    if not tokenizer.exists():
        raise FileNotFoundError(f"embedding tokenizer not found: {tokenizer}")
    return graph, tokenizer


class OnnxEmbeddingProvider:
    """Lazily loaded, thread-safe BGE embedder.

    ``available`` is decided at construction from the filesystem so callers can
    cheaply check whether embedding-based retrieval is possible before touching
    the model.
    """

    def __init__(
        self,
        model_path: str | Path,
        *,
        max_length: int = DEFAULT_MAX_LENGTH,
        query_prefix: str = DEFAULT_QUERY_PREFIX,
        intra_threads: int | None = None,
    ) -> None:
        self.model_path = str(model_path)
        self._max_length = max(8, int(max_length))
        self._query_prefix = query_prefix
        self._intra_threads = intra_threads or max(1, (os.cpu_count() or 2) // 2)
        self._session: Any | None = None
        self._tokenizer: Any | None = None
        self._input_names: tuple[str, ...] = ()
        self._dimension: int | None = None
        self._last_error: str | None = None
        self._lock = threading.RLock()
        try:
            self._graph, self._tokenizer_path = resolve_model_files(model_path)
            self._available = True
        except FileNotFoundError as exc:
            self._graph, self._tokenizer_path = Path(model_path) / MODEL_FILE, Path(model_path) / TOKENIZER_FILE
            self._available = False
            self._last_error = str(exc)

    @property
    def available(self) -> bool:
        return self._available

    @property
    def dimension(self) -> int | None:
        return self._dimension

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "backend": "onnx-bge-zh",
                "model_path": self.model_path,
                "available": self._available,
                "loaded": self._session is not None,
                "dimension": self._dimension,
                "max_length": self._max_length,
                "last_error": self._last_error,
            }

    def _ensure_loaded(self) -> tuple[Any, Any]:
        with self._lock:
            if self._session is not None and self._tokenizer is not None:
                return self._session, self._tokenizer
            if not self._available:
                raise RuntimeError(self._last_error or "embedding model unavailable")
            try:
                import onnxruntime
                from tokenizers import Tokenizer

                options = onnxruntime.SessionOptions()
                options.intra_op_num_threads = self._intra_threads
                options.log_severity_level = 3
                session = onnxruntime.InferenceSession(str(self._graph), options, providers=["CPUExecutionProvider"])
                tokenizer = Tokenizer.from_file(str(self._tokenizer_path))
                tokenizer.enable_truncation(max_length=self._max_length)
                self._session, self._tokenizer = session, tokenizer
                self._input_names = tuple(item.name for item in session.get_inputs())
                self._last_error = None
            except Exception as exc:  # keep the failure observable instead of crashing the app
                self._available = False
                self._last_error = f"{type(exc).__name__}: {exc}"
                raise
            return self._session, self._tokenizer

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of raw documents (no instruction prefix)."""

        items = [("" if text is None else str(text)).strip() for text in texts]
        if not items:
            return []
        session, tokenizer = self._ensure_loaded()
        import numpy as np

        encodings = tokenizer.encode_batch(items)
        width = max(1, max(len(encoding.ids) for encoding in encodings))
        ids = np.zeros((len(encodings), width), dtype=np.int64)
        mask = np.zeros((len(encodings), width), dtype=np.int64)
        for row, encoding in enumerate(encodings):
            length = len(encoding.ids)
            ids[row, :length] = encoding.ids
            mask[row, :length] = encoding.attention_mask

        feeds: dict[str, Any] = {}
        if "input_ids" in self._input_names:
            feeds["input_ids"] = ids
        if "attention_mask" in self._input_names:
            feeds["attention_mask"] = mask
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(ids)

        hidden = session.run(None, feeds)[0]
        if hidden.ndim == 3:
            pooled = hidden[:, 0, :]  # CLS pooling, as the BGE pooling config specifies
        else:
            pooled = hidden  # some exports already apply pooling
        vectors = [[float(value) for value in row] for row in pooled]
        self._dimension = len(vectors[0]) if vectors else None
        return [l2_normalise(vector) for vector in vectors]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed memory statements (documents side of the BGE recipe)."""

        return self.embed(texts)

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed stored probes: they are questions, so they take the prefix too.

        Mixing this up is subtle and costly -- embedding probes as documents while
        embedding the user's question as a query dropped an exact-question match
        from 1.000 to 0.736, which is right at the relevance floor.
        """

        if not self._query_prefix:
            return self.embed(texts)
        return self.embed([f"{self._query_prefix}{text}" for text in texts])

    def embed_query(self, text: str) -> list[float]:
        """Embed a user question, with the instruction prefix BGE expects."""

        prefixed = f"{self._query_prefix}{text}" if self._query_prefix else text
        vectors = self.embed([prefixed])
        return vectors[0] if vectors else []


def create_embedding_provider(model_path: str | Path | None = None) -> OnnxEmbeddingProvider:
    """Build the configured embedding provider (never raises on a missing model)."""

    path = model_path or os.getenv("IOT_EMBEDDING_MODEL_PATH") or DEFAULT_MODEL_PATH
    root = Path(__file__).resolve().parents[2]
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = root / resolved
    return OnnxEmbeddingProvider(resolved, max_length=int(os.getenv("IOT_EMBEDDING_MAX_LENGTH", str(DEFAULT_MAX_LENGTH))))
