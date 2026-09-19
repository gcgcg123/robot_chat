"""Select the ASR backend from configuration.

``ASR_PROVIDER`` picks the implementation and each backend keeps its own model
path, so switching between them needs no code change:

* ``whisper`` (default) -- faster-whisper / CTranslate2, ``ASR_MODEL_PATH``
* ``sensevoice``        -- sherpa-onnx ONNX, ``ASR_SENSEVOICE_MODEL_PATH``

Both backends expose the same ``transcribe`` / ``snapshot`` / ``status``
contract, so the worker, the ``/api/transcribe`` route and
``/api/system/status`` are backend-agnostic.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from services.audio.asr import AsrService
from services.audio.sensevoice import SenseVoiceAsrService

ROOT = Path(__file__).resolve().parents[2]

DEFAULT_WHISPER_MODEL = "models/asr/whisper-large-v3-turbo-ct2"
DEFAULT_SENSEVOICE_MODEL = "models/asr/sensevoice-small"

SENSEVOICE_ALIASES = {"sensevoice", "sense_voice", "sense-voice"}

_FALSEY = {"0", "false", "no", "off"}


def resolve_model_path(value: str) -> str:
    """Resolve a configured model path against the project root."""

    path = Path(value)
    return str(path if path.is_absolute() else (ROOT / path).resolve())


def _flag(env: dict, name: str, default: bool) -> bool:
    raw = str(env.get(name, "")).strip().lower()
    if not raw:
        return default
    return raw not in _FALSEY


def _positive_int(env: dict, name: str) -> int | None:
    raw = str(env.get(name, "")).strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def describe_asr_backend(env: dict | None = None) -> dict[str, str]:
    """Report the configured backend and model path without building anything.

    Cheap and lock-free, so ``/health`` can call it on every probe even while a
    transcription is in flight.
    """

    env = os.environ if env is None else env
    backend = str(env.get("ASR_PROVIDER", "whisper")).strip().lower()
    if backend in SENSEVOICE_ALIASES:
        return {
            "backend": "sensevoice",
            "model_path": str(env.get("ASR_SENSEVOICE_MODEL_PATH") or DEFAULT_SENSEVOICE_MODEL),
        }
    return {
        "backend": "whisper",
        "model_path": str(env.get("ASR_MODEL_PATH") or DEFAULT_WHISPER_MODEL),
    }


def create_asr_service(providers: dict | None = None, env: dict | None = None) -> Any:
    """Build the configured ASR backend.

    ``env`` defaults to ``os.environ`` and exists so the selection logic can be
    unit tested without mutating process state.
    """

    providers = providers or {}
    env = os.environ if env is None else env
    backend = str(env.get("ASR_PROVIDER", "whisper")).strip().lower()

    if backend in SENSEVOICE_ALIASES:
        return SenseVoiceAsrService(
            resolve_model_path(str(env.get("ASR_SENSEVOICE_MODEL_PATH") or DEFAULT_SENSEVOICE_MODEL)),
            device=str(env.get("ASR_DEVICE", "auto")),
            num_threads=_positive_int(env, "ASR_CPU_THREADS"),
            use_itn=_flag(env, "ASR_USE_ITN", True),
            language=str(env.get("ASR_SENSEVOICE_LANGUAGE", "")),
        )

    return AsrService(
        resolve_model_path(str(env.get("ASR_MODEL_PATH") or DEFAULT_WHISPER_MODEL)),
        model_factory=providers.get("asr_model_factory"),
        device=str(env.get("ASR_DEVICE", "auto")),
        compute_type=str(env.get("ASR_COMPUTE_TYPE", "auto")),
    )
