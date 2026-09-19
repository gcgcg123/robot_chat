"""SenseVoice-Small ASR provider backed by sherpa-onnx (ONNX, no PyTorch).

Why this exists: the Whisper large-v3-turbo checkpoint needs ~35 s per turn on
this project's CPU-only target because every request encodes a full 30-second
window.  SenseVoice-Small is non-autoregressive (one forward pass, no decoder
loop), ships as a ~228 MB int8 ONNX export, and is trained on zh / yue / en --
the three enrollment languages -- so Cantonese does not have to be traded away
to get interactive latency.

The public surface mirrors :class:`services.audio.asr.AsrService`
(``transcribe`` / ``snapshot`` / ``status``) so the bounded worker, the
``/api/transcribe`` route and ``/api/system/status`` need no changes.
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from services.audio.asr import cuda_device_count, probe_cuda_runtime

SAMPLE_RATE = 16000
FEATURE_DIM = 80

# SenseVoice prefixes the transcript with markers such as <|zh|><|NEUTRAL|><|Speech|>.
_TAGS = re.compile(r"<\|[^|]*\|>")

# Codes accepted by SenseVoice; the enrollment table already uses these.
LANGUAGE_CODES = ("zh", "en", "yue", "ja", "ko", "auto", "nospeech")


def strip_sensevoice_tags(text: str) -> str:
    """Remove SenseVoice's language/emotion/event markers from a transcript."""

    return _TAGS.sub("", text or "").strip()


def normalize_language(language: str | None) -> str:
    """Map an enrollment language hint onto a SenseVoice language code.

    Unknown or missing hints fall back to ``""``, which lets SenseVoice detect
    the language itself instead of forcing a wrong one.
    """

    value = (language or "").strip().lower()
    return value if value in LANGUAGE_CODES else ""


def _to_float32(audio: Any) -> Any:
    """Convert a NormalizedAudio PCM payload (or array) to a float32 waveform."""

    pcm = getattr(audio, "pcm16", None)
    if pcm is None:
        return audio
    import numpy as np

    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def resolve_model_files(model_path: str | Path) -> tuple[Path, Path, str]:
    """Locate the ONNX graph, its tokens file, and the quantisation label.

    ``model_path`` may be either a directory holding ``tokens.txt`` plus one of
    ``model.int8.onnx`` / ``model.onnx``, or a direct path to the ``.onnx`` file
    (whose sibling ``tokens.txt`` is then used).
    """

    root = Path(model_path)
    if root.is_file() and root.suffix == ".onnx":
        directory, graph = root.parent, root
    elif root.is_dir():
        directory = root
        graph = next(
            (candidate for candidate in (directory / "model.int8.onnx", directory / "model.onnx") if candidate.exists()),
            directory / "model.int8.onnx",
        )
    else:
        directory, graph = root, root / "model.int8.onnx"

    tokens = directory / "tokens.txt"
    if not graph.exists():
        raise FileNotFoundError(f"sensevoice model not found: {graph}")
    if not tokens.exists():
        raise FileNotFoundError(f"sensevoice tokens not found: {tokens}")
    quantisation = "int8" if "int8" in graph.name else "float32"
    return graph, tokens, quantisation


class SenseVoiceAsrService:
    """Lazily loaded SenseVoice recogniser with the same contract as AsrService.

    ``device="auto"`` reuses the shared CUDA probe: the GPU is used when it is
    genuinely usable, otherwise the CPU is chosen up front, and a CUDA failure
    during load still degrades to CPU instead of failing the request.
    """

    def __init__(
        self,
        model_path: str | Path,
        device: str = "auto",
        num_threads: int | None = None,
        use_itn: bool = True,
        language: str = "",
        debug: bool = False,
        recognizer_factory: "Callable[[Path, Path, str], Any] | None" = None,
    ) -> None:
        self.model_path = str(model_path)
        requested = device.lower().strip()
        if requested not in {"auto", "cuda", "cpu"}:
            raise ValueError("device must be auto, cuda, or cpu")
        self._requested_device = requested
        self._num_threads = num_threads if num_threads and num_threads > 0 else max(1, os.cpu_count() or 1)
        self._use_itn = use_itn
        self._default_language = normalize_language(language)
        self._debug = debug
        self._factory = recognizer_factory or self._default_factory

        self._cuda_device_count = cuda_device_count() if requested != "cpu" else 0
        if requested == "auto":
            self._cuda_usable, self._runtime_reason = probe_cuda_runtime(self._cuda_device_count)
        elif requested == "cuda":
            self._cuda_usable, self._runtime_reason = True, "forced_by_config"
        else:
            self._cuda_usable, self._runtime_reason = False, "forced_by_config"

        self._recognizer: Any | None = None
        self._device: str | None = None
        self._compute_type: str | None = None
        self._status = "unavailable"
        self._last_error: str | None = None
        self._lock = threading.RLock()

    @property
    def status(self) -> str:
        return self._status

    def snapshot(self) -> dict[str, Any]:
        """Return provider state suitable for health/readiness reporting."""

        with self._lock:
            return {
                "status": self._status,
                "model_path": self.model_path,
                "device": self._device,
                "compute_type": self._compute_type,
                "last_error": self._last_error,
                "loaded": self._recognizer is not None,
                "requested_device": self._requested_device,
                "cuda_device_count": self._cuda_device_count,
                "cuda_available": self._cuda_usable,
                "runtime_reason": self._runtime_reason,
                "backend": "sensevoice",
                "decode_options": {
                    "num_threads": self._num_threads,
                    "use_itn": self._use_itn,
                    "language": self._default_language or "auto",
                },
            }

    def _initial_provider(self) -> str:
        if self._requested_device == "auto":
            return "cuda" if self._cuda_usable else "cpu"
        return self._requested_device

    def _default_factory(self, graph: Path, tokens: Path, provider: str) -> Any:
        import sherpa_onnx

        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(graph),
            tokens=str(tokens),
            num_threads=self._num_threads,
            sample_rate=SAMPLE_RATE,
            feature_dim=FEATURE_DIM,
            decoding_method="greedy_search",
            language=self._default_language,
            use_itn=self._use_itn,
            provider=provider,
            debug=self._debug,
        )

    def _build(self, provider: str) -> Any:
        graph, tokens, quantisation = resolve_model_files(self.model_path)
        recognizer = self._factory(graph, tokens, provider)
        self._recognizer, self._device, self._compute_type = recognizer, provider, quantisation
        self._status = "ready"
        self._last_error = None
        return recognizer

    def _ensure_loaded(self) -> Any:
        with self._lock:
            if self._recognizer is not None:
                return self._recognizer
            provider = self._initial_provider()
            try:
                recognizer = self._build(provider)
                print(
                    f"[asr] sensevoice provider={provider} compute_type={self._compute_type} "
                    f"reason={self._runtime_reason} cuda_devices={self._cuda_device_count} "
                    f"threads={self._num_threads}",
                    flush=True,
                )
                return recognizer
            except Exception as error:
                if provider != "cuda":
                    self._status, self._last_error = "error", str(error)
                    raise
                self._last_error = str(error)
                recognizer = self._build("cpu")
                self._status = "degraded"
                self._runtime_reason = f"cuda_failed:{self._last_error}"
                print(f"[asr] sensevoice cuda load failed, degraded to cpu: {self._last_error}", flush=True)
                return recognizer

    def _decode(self, recognizer: Any, samples: Any, language: str | None) -> str:
        stream = recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, samples)
        recognizer.decode_stream(stream)
        result = stream.result
        self._last_language = getattr(result, "lang", None) or language
        return strip_sensevoice_tags(getattr(result, "text", ""))

    def transcribe(self, audio: Any, language: str | None = None) -> dict[str, Any]:
        """Transcribe one normalized audio item and return observable metadata."""

        started = time.perf_counter()
        requested_language = normalize_language(language) or self._default_language
        with self._lock:
            recognizer = self._ensure_loaded()
            try:
                text = self._decode(recognizer, _to_float32(audio), requested_language)
            except Exception as error:
                if self._device != "cuda":
                    self._status, self._last_error = "error", str(error)
                    raise
                self._recognizer = None
                self._last_error = str(error)
                recognizer = self._build("cpu")
                text = self._decode(recognizer, _to_float32(audio), requested_language)
                self._status = "degraded"
            latency_ms = int((time.perf_counter() - started) * 1000)
            return {
                "text": text,
                "language": getattr(self, "_last_language", None) or requested_language or None,
                "language_probability": None,
                "model_path": self.model_path,
                "status": self._status,
                "device": self._device,
                "compute_type": self._compute_type,
                "backend": "sensevoice",
                "latency_ms": latency_ms,
            }
