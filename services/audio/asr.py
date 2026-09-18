"""Resident faster-whisper provider with explicit CUDA degradation semantics.

The service is deliberately independent from FastAPI.  A route or bounded
worker can own one instance and call :meth:`transcribe`; model construction is
lazy and the same model object is reused for subsequent turns.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Callable


ModelFactory = Callable[[str, str, str], Any]


def select_asr_runtime(cuda_available: bool) -> tuple[str, str]:
    """Return the first runtime to try for automatic device selection."""

    return ("cuda", "float16") if cuda_available else ("cpu", "int8")


def cuda_device_count() -> int:
    """Number of CUDA devices CTranslate2 can see (0 when unavailable)."""

    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def probe_cuda_runtime(device_count: int | None = None) -> tuple[bool, str]:
    """Decide whether the GPU is genuinely usable, not merely present.

    A device counter above zero only proves the driver is visible; loading a
    CUDA model additionally needs cuDNN/cuBLAS.  Asking CTranslate2 which
    compute types the CUDA backend supports performs that check up front, so a
    GPU that would fail at load time degrades to CPU immediately instead of
    after a slow failed load.
    """

    count = cuda_device_count() if device_count is None else device_count
    if count <= 0:
        return False, "no_cuda_device"
    try:
        import ctranslate2

        supported = set(ctranslate2.get_supported_compute_types("cuda"))
    except Exception as exc:
        return False, f"cuda_backend_unavailable:{type(exc).__name__}"
    if "float16" not in supported:
        return False, "cuda_fp16_unsupported"
    return True, "cuda_ready"


def _detect_cuda() -> bool:
    """Best-effort CUDA probe that never imports a heavyweight model module."""

    return probe_cuda_runtime()[0]


def _cpu_threads() -> int:
    """Resolve the CPU thread budget for CTranslate2 (0 means "untuned")."""

    raw = os.getenv("ASR_CPU_THREADS", "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return max(1, os.cpu_count() or 1)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _decode_options() -> dict[str, Any]:
    """Decode options tuned for interactive latency, mainly on CPU.

    faster-whisper defaults are accuracy-first and cost seconds per turn:
    ``beam_size=5`` plus a temperature fallback ladder of up to six retries.
    The defaults here favour responsiveness instead; every value remains
    overridable through the environment.
    """

    raw_beam = os.getenv("ASR_BEAM_SIZE", "").strip()
    beam_size = int(raw_beam) if raw_beam.isdigit() and int(raw_beam) > 0 else 1
    raw_temperature = os.getenv("ASR_TEMPERATURE", "").strip()
    try:
        temperature: float | list[float] = float(raw_temperature) if raw_temperature else 0.0
    except ValueError:
        temperature = 0.0
    return {
        "beam_size": beam_size,
        "temperature": temperature,
        "condition_on_previous_text": _env_flag("ASR_CONDITION_ON_PREVIOUS_TEXT", False),
        "without_timestamps": _env_flag("ASR_WITHOUT_TIMESTAMPS", True),
        "vad_filter": _env_flag("ASR_VAD_FILTER", True),
    }


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
    return WhisperModel(
        model_path,
        device=device,
        compute_type=compute_type,
        cpu_threads=_cpu_threads(),
        num_workers=1,
    )


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
        # Resolve the GPU decision up front so it is observable before the model loads.
        self._cuda_device_count = cuda_device_count() if requested != "cpu" else 0
        if requested == "auto":
            self._cuda_usable, self._runtime_reason = probe_cuda_runtime(self._cuda_device_count)
        elif requested == "cuda":
            self._cuda_usable, self._runtime_reason = True, "forced_by_config"
        else:
            self._cuda_usable, self._runtime_reason = False, "forced_by_config"
        self._decode_options = _decode_options()
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
                "backend": "whisper",
                "requested_device": self._requested_device,
                "cuda_device_count": self._cuda_device_count,
                "cuda_available": self._cuda_usable,
                "runtime_reason": self._runtime_reason,
                "decode_options": dict(self._decode_options),
            }

    def _initial_runtime(self) -> tuple[str, str]:
        if self._requested_device == "auto":
            device, compute = select_asr_runtime(self._cuda_usable)
        else:
            device = self._requested_device
            compute = "float16" if device == "cuda" else "int8"
        if self._requested_compute_type != "auto":
            compute = self._requested_compute_type
        return device, compute

    def _load(self, device: str, compute_type: str) -> Any:
        model = self._factory(self.model_path, device, compute_type)
        self._model, self._device, self._compute_type = model, device, compute_type
        self._status = "ready"
        self._last_error = None
        return model

    def _ensure_loaded(self) -> Any:
        with self._lock:
            if self._model is not None:
                return self._model
            device, compute_type = self._initial_runtime()
            try:
                model = self._load(device, compute_type)
                print(
                    f"[asr] device={device} compute_type={compute_type} "
                    f"reason={self._runtime_reason} cuda_devices={self._cuda_device_count}",
                    flush=True,
                )
                return model
            except Exception as error:
                if device != "cuda" or not _is_cuda_failure(error):
                    self._status, self._last_error = "error", str(error)
                    raise
                self._last_error = str(error)
                model = self._load("cpu", "int8")
                self._status = "degraded"
                self._runtime_reason = f"cuda_failed:{self._last_error}"
                print(f"[asr] cuda load failed, degraded to cpu/int8: {self._last_error}", flush=True)
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
        segments, info = model.transcribe(
            self._coerce_audio(audio),
            language=language,
            **self._decode_options,
        )
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
