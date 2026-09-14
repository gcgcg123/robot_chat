"""Resident faster-whisper provider with explicit CUDA degradation semantics.

The service is deliberately independent from FastAPI.  A route or bounded
worker can own one instance and call :meth:`transcribe`; model construction is
lazy and the same model object is reused for subsequent turns.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable


ModelFactory = Callable[[str, str, str], Any]


def select_asr_runtime(cuda_available: bool) -> tuple[str, str]:
    """Return the first runtime to try for automatic device selection."""

    return ("cuda", "float16") if cuda_available else ("cpu", "int8")


def _detect_cuda() -> bool:
    """Best-effort CUDA probe that never imports a heavyweight model module."""

    try:
        import ctranslate2

        return bool(ctranslate2.get_cuda_device_count() > 0)
    except Exception:
        return False


def _is_cuda_failure(error: BaseException) -> bool:
    """Classify errors for which retrying on CPU is safe and useful."""

    message = str(error).lower()
    markers = (
        "cuda",
        "cudnn",
        "cublas",
        "out of memory",
        "not enough memory",
        "device not found",
        "no kernel image",
    )
    return any(marker in message for marker in markers)


def _default_factory(model_path: str, device: str, compute_type: str) -> Any:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - exercised by deployment
        raise RuntimeError("faster-whisper is not installed") from exc
    return WhisperModel(model_path, device=device, compute_type=compute_type)


class AsrService:
    """A lazily loaded, thread-safe faster-whisper model provider.

    ``device="auto"`` probes CTranslate2 and initially selects CUDA/FP16 when
    available.  A CUDA/cuDNN/OOM failure during load or inference causes one
    explicit retry on CPU/int8.  File, format, and tokenizer errors propagate
    unchanged instead of being mislabeled as GPU failures.
    """

    def __init__(
        self,
        model_path: str | Path,
        model_factory: ModelFactory | None = None,
        device: str = "auto",
        compute_type: str = "auto",
    ) -> None:
        self.model_path = str(model_path)
        self._factory = model_factory or _default_factory
        requested = device.lower().strip()
        if requested not in {"auto", "cuda", "cpu"}:
            raise ValueError("device must be auto, cuda, or cpu")
        self._requested_device = requested
        self._requested_compute_type = compute_type.lower().strip()
        self._model: Any | None = None
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
                "loaded": self._model is not None,
            }

    def _initial_runtime(self) -> tuple[str, str]:
        if self._requested_device == "auto":
            device, compute = select_asr_runtime(_detect_cuda())
        else:
            device = self._requested_device
            compute = "float16" if device == "cuda" else "int8"
        if self._requested_compute_type != "auto":
            compute = self._requested_compute_type
        return device, compute

    def _load(self, device: str, compute_type: str) -> Any:
        model = self._factory(self.model_path, device, compute_type)
        self._model, self._device, self._compute_type = model, device, compute_type
        self._status = "ready" if device == "cuda" or self._requested_device != "cuda" else "ready"
        self._last_error = None
        return model

    def _ensure_loaded(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model
            device, compute_type = self._initial_runtime()
            try:
                return self._load(device, compute_type)
            except Exception as error:
                if device != "cuda" or not _is_cuda_failure(error):
                    self._status, self._last_error = "error", str(error)
                    raise
                self._last_error = str(error)
                model = self._load("cpu", "int8")
                self._status = "degraded"
                return model

    @staticmethod
    def _coerce_audio(audio: Any) -> Any:
        """Convert a NormalizedAudio PCM payload to a float32 waveform."""

        pcm = getattr(audio, "pcm16", None)
        if pcm is None:
            return audio
        try:
            import numpy as np

            return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception:
            # Keep the original object for test doubles and custom factories.
            return audio

    def _run(self, model: Any, audio: Any, language: str | None) -> tuple[str, Any]:
        segments, info = model.transcribe(self._coerce_audio(audio), language=language, vad_filter=True)
        text = "".join(getattr(segment, "text", str(segment)) for segment in segments).strip()
        return text, info

    def transcribe(self, audio: Any, language: str | None = None) -> dict[str, Any]:
        """Transcribe one normalized audio item and return observable metadata."""

        started = time.perf_counter()
        with self._lock:
            model = self._ensure_loaded()
            try:
                text, info = self._run(model, audio, language)
            except Exception as error:
                if self._device != "cuda" or not _is_cuda_failure(error):
                    self._status, self._last_error = "error", str(error)
                    raise
                # Drop the failed GPU object before a single CPU retry.
                self._model = None
                self._last_error = str(error)
                model = self._load("cpu", "int8")
                text, info = self._run(model, audio, language)
                self._status = "degraded"
            latency_ms = int((time.perf_counter() - started) * 1000)
            language_value = getattr(info, "language", None)
            probability = getattr(info, "language_probability", None)
            result = {
                "text": text,
                "language": language_value,
                "language_probability": probability,
                "model_path": self.model_path,
                "status": self._status,
                "device": self._device,
                "compute_type": self._compute_type,
                "latency_ms": latency_ms,
            }
            return result
