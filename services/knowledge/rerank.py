from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Sequence

# Measured: with 341 chunks the hybrid scores of correct and wrong chunks overlap heavily
# ("奶奶不肯吃饭怎么劝？" scored its answer 0.62 while an unrelated 拓展学习 section scored
# 0.6241), and the answer was simply ranked below weaker matches. A cross-encoder would fix it
# but means another model download for everyone who runs this project, so the reranker reuses
# the LLM the project already talks to and only runs when retrieval is not confident.
DEFAULT_RERANK_BELOW = 0.70
DEFAULT_RERANK_POOL = 20
DEFAULT_RERANK_KEEP = 3
# Stage-2 admission is decided by the reranker's own score once it has run: the hybrid floor
# describes question-vs-statement similarity and is exactly the signal that failed on the hard
# questions, so using it to admit a reranked pick let a true negative scoring 0.6226 back in.
# Cross-encoder logits are calibrated separately -- measured on this corpus, passages that answer
# the question score around -2 to -3 and passages that do not score -8 to -10 (A9.7.15).
DEFAULT_RERANK_FLOOR = -5.0

PROMPT = (
    "你是檢索重排器。下面是使用者的問題，以及檢索到的候選段落。請挑出**真的能回答這個問題**的段落，"
    "只輸出 JSON：{{\"best\":[序號,...]}}，最多 {keep} 個，依相關度排序；若沒有一段能回答，就輸出 "
    "{{\"best\":[]}}。不要解釋、不要輸出其他文字。\n\n問題：{question}\n\n{candidates}"
)


class LlmReranker:
    """Listwise rerank of the candidate pool using the chat endpoint.

    ``post`` performs the HTTP call and returns the assistant's text; it is injected so the
    reranker can be tested without a network and reused by the calibration script.
    """

    def __init__(
        self,
        post: Callable[[str, int], str],
        *,
        keep: int = DEFAULT_RERANK_KEEP,
        excerpt: int = 150,
    ) -> None:
        self._post = post
        self._keep = max(1, keep)
        self._excerpt = excerpt
        self.last_error = ""

    def _messages(self, question: str, texts: Sequence[str]) -> str:
        lines = [
            f"[{index}] {' '.join(text.split())[: self._excerpt]}"
            for index, text in enumerate(texts)
        ]
        return PROMPT.format(keep=self._keep, question=question, candidates="\n".join(lines))

    def _parse(self, content: str, width: int) -> list[int] | None:
        match = re.search(r"\{.*\}", content or "", re.S)
        if not match:
            return None
        try:
            best = json.loads(match.group(0)).get("best", [])
        except json.JSONDecodeError:
            self.last_error = "bad_json"
            return None
        picked: list[int] = []
        for value in best if isinstance(best, list) else []:
            try:
                index = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= index < width and index not in picked:
                picked.append(index)
        return picked[: self._keep]

    def choose(self, question: str, texts: Sequence[str]) -> list[int] | None:
        """Indices of the candidates that answer ``question``; None when the call failed.

        The endpoint's model spends output budget on reasoning before answering (measured: with a
        small budget the content came back empty and finish_reason was "length"), so a response
        with no JSON is retried against a smaller pool rather than treated as "nothing answers".
        Returning ``[]`` is a real decision and is never retried.
        """

        if not texts:
            return []
        widths = [len(texts), max(4, len(texts) // 2), 4]
        tried: set[int] = set()
        for width in widths:
            if width in tried:
                continue
            tried.add(width)
            subset = list(range(min(width, len(texts))))
            try:
                content = self._post(
                    self._messages(question, [texts[index] for index in subset]),
                    1500 + 120 * len(subset),
                )
            except Exception as exc:  # pragma: no cover - network/API failure
                self.last_error = f"{type(exc).__name__}: {exc}"
                return None
            picked = self._parse(content, len(subset))
            if picked is not None:
                self.last_error = ""
                return [subset[index] for index in picked]
            self.last_error = "no_json"
        return None


def create_llm_reranker(env: dict | None = None, *, keep: int = DEFAULT_RERANK_KEEP) -> LlmReranker | None:
    """Build a reranker from the environment, or None when no API key is configured.

    Returns None rather than raising: a checkout without a key must still start and retrieve,
    just without the second stage.
    """

    source = os.environ if env is None else env
    if not str(source.get("DEEPSEEK_API_KEY", "")).strip():
        return None
    base = str(source.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")
    model = str(source.get("DEEPSEEK_MODEL", "deepseek-flash"))
    url = base + "/chat/completions"

    def post(prompt: str, max_tokens: int) -> str:
        import httpx

        with httpx.Client(timeout=60, trust_env=False) as client:
            response = client.post(
                url,
                headers={"Authorization": "Bearer " + str(source["DEEPSEEK_API_KEY"])},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": max_tokens,
                },
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"].get("content") or ""

    return LlmReranker(post, keep=keep)


DEFAULT_LOCAL_DIR = Path("models") / "rerank" / "bge-reranker-base"
# 512 is the model's own limit and costs ~12 s for 20 candidates on 4 CPU cores against ~8 s at
# 256, so 256 is the default; IOT_KNOWLEDGE_RERANK_MAX_LENGTH overrides it (A9.7.15 records what
# the longer window does to the ranking).
DEFAULT_RERANK_MAX_LENGTH = 256


class TorchCrossEncoderReranker:
    """Local cross-encoder (BAAI/bge-reranker-base) scoring (question, passage) pairs.

    This is the fix for the failure the embedding stage cannot repair: the right passage exists in
    the pool but ranks 12th-15th by cosine, so the *ranking signal* -- not the pipeline -- is the
    bottleneck. A cross-encoder reads the question and the passage together, which separates those
    cases; measured on this corpus it lifts the scanned book from 12/14 to 13/14 answered questions
    and the whole set from 26/28 to 27/28 when stage 2 admits on its own score
    (docs/RAG_KNOWLEDGE_PLAN.md A9.7.15). It costs ~8 s per uncertain turn on 4 CPU cores, which is
    why it stays off by default.

    Loaded lazily and never a hard requirement: `torch` is an optional dependency of this project
    (requirements-voiceprint.txt) and the weights are a separate 1.06 GB download, so a checkout
    without them keeps retrieving -- `available` is False and /health reports the diagnostic.
    """

    def __init__(
        self,
        model_dir: str | Path | None = None,
        *,
        device: str = "cpu",
        batch: int = 8,
        max_length: int = DEFAULT_RERANK_MAX_LENGTH,
    ) -> None:
        self.model_dir = Path(model_dir) if model_dir else DEFAULT_LOCAL_DIR
        if not self.model_dir.is_absolute():
            self.model_dir = Path(__file__).resolve().parents[2] / self.model_dir
        self._device = device
        self._batch = max(1, batch)
        self._max_length = max_length
        self._tokenizer = None
        self._model = None
        self.last_error = ""

    @property
    def available(self) -> bool:
        return self._load() is not None

    @property
    def max_length(self) -> int:
        return self._max_length

    def diagnose(self) -> str:
        """Why this reranker cannot score, or "" -- without loading 1 GB of weights.

        ``/health`` calls this, and a health endpoint that spends 14 s loading a model the first
        time it is polled is worse than the problem it reports. Only the cheap prerequisites
        (optional packages present, weights on disk) are checked here.
        """

        if self._model is not None:
            return ""
        try:
            import torch  # noqa: F401
            import transformers  # noqa: F401
        except ImportError as exc:
            return f"optional_dependencies_missing:{exc.name}"
        if not (self.model_dir / "config.json").is_file():
            return f"model_missing:{self.model_dir}"
        return ""

    def _load(self):
        if self._model is not None:
            return self._model
        reason = self.diagnose()
        if reason:
            self.last_error = reason
            return None
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        try:
            self._tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir), local_files_only=True)
            model = AutoModelForSequenceClassification.from_pretrained(str(self.model_dir), local_files_only=True)
        except Exception as exc:  # pragma: no cover - broken download
            self.last_error = f"load_failed:{type(exc).__name__}"
            return None
        model.eval()
        model.to(self._device)
        self._model = model
        self.last_error = ""
        return model

    def scores(self, question: str, texts: Sequence[str]) -> list[float] | None:
        """Relevance score per passage (a cross-encoder logit; higher is better)."""

        model = self._load()
        if model is None:
            return None
        import torch

        results: list[float] = []
        try:
            with torch.inference_mode():
                for start in range(0, len(texts), self._batch):
                    batch = texts[start:start + self._batch]
                    inputs = self._tokenizer(
                        [question] * len(batch),
                        list(batch),
                        padding=True,
                        truncation=True,
                        max_length=self._max_length,
                        return_tensors="pt",
                    ).to(self._device)
                    logits = model(**inputs).logits.view(-1).float().tolist()
                    results.extend(float(value) for value in logits)
        except Exception as exc:  # pragma: no cover - inference failure
            self.last_error = f"inference_failed:{type(exc).__name__}"
            return None
        self.last_error = ""
        return results

    def rank(self, question: str, texts: Sequence[str]) -> list[tuple[int, float]] | None:
        """``(index, relevance)`` pairs, best first; None when scoring failed.

        The scores are returned alongside the order because stage 2 admits on them: the ordering
        alone cannot express "the best of these is still irrelevant".
        """

        if not texts:
            return []
        values = self.scores(question, texts)
        if values is None:
            return None
        order = sorted(range(len(values)), key=lambda index: values[index], reverse=True)
        return [(index, float(values[index])) for index in order]

    def choose(self, question: str, texts: Sequence[str]) -> list[int] | None:
        """Candidates ordered by relevance, best first.

        The whole ordering is returned, not a fixed number: how many survive is the caller's
        budget (``KnowledgeSettings.rerank_keep``), and hard-coding it here once silently cut the
        answer at rank 4 while the caller asked for five.
        """

        ranked = self.rank(question, texts)
        return None if ranked is None else [index for index, _score in ranked]


def create_reranker(env: dict | None = None) -> Any:
    """Pick the reranker named by IOT_KNOWLEDGE_RERANK: 'local' (default), 'llm', or off/0."""

    source = os.environ if env is None else env
    choice = str(source.get("IOT_KNOWLEDGE_RERANK", "local")).strip().lower()
    if choice in {"", "0", "off", "false", "no", "none"}:
        return None
    if choice in {"llm", "api"}:
        return create_llm_reranker(source)
    try:
        max_length = int(str(source.get("IOT_KNOWLEDGE_RERANK_MAX_LENGTH", "")).strip() or DEFAULT_RERANK_MAX_LENGTH)
    except ValueError:
        max_length = DEFAULT_RERANK_MAX_LENGTH
    return TorchCrossEncoderReranker(
        source.get("IOT_KNOWLEDGE_RERANK_MODEL") or None,
        max_length=max(32, max_length),
    )
