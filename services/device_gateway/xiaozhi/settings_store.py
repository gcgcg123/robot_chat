"""Read/write the ESP settings block in ``.env``.

The project's convention (see README) is that ``.env`` is the live configuration
and ``.env.example`` is only an install template, so the dashboard's onboarding
page edits ``.env`` -- not a second settings store.  The writer is
comment-preserving and only ever touches keys it knows about, because clobbering
an unrelated line in someone's ``.env`` would be worse than not having the
feature.

Secrets are deliberately **not** editable here: ``IOT_ESP_TOKEN_SECRET`` follows
the same rule as the DeepSeek key and the admin password (never written to a
file the dashboard can reach).
"""
from __future__ import annotations

import os
from pathlib import Path

#: Dashboard setting name -> environment key.
EDITABLE_KEYS: dict[str, str] = {
    "enabled": "IOT_ESP_ENABLED",
    "path": "IOT_ESP_PATH",
    "require_token": "IOT_ESP_REQUIRE_TOKEN",
    "allowed_devices": "IOT_ESP_ALLOWED_DEVICES",
    "default_user_id": "IOT_ESP_DEFAULT_USER",
    "sample_rate": "IOT_ESP_SAMPLE_RATE",
    "frame_duration_ms": "IOT_ESP_FRAME_DURATION_MS",
    "uplink_sample_rate": "IOT_ESP_UPLINK_SAMPLE_RATE",
    "ota_enabled": "IOT_OTA_ENABLED",
    "ota_base_url": "IOT_OTA_BASE_URL",
    "vad_provider": "IOT_VAD_PROVIDER",
    "vad_silence_ms": "IOT_VAD_SILENCE_MS",
    "vad_min_speech_ms": "IOT_VAD_MIN_SPEECH_MS",
    "vad_max_utterance_ms": "IOT_VAD_MAX_UTTERANCE_MS",
    "vad_rms_threshold": "IOT_VAD_RMS_THRESHOLD",
    "tts_enabled": "IOT_ESP_TTS_ENABLED",
    "llm_stream": "IOT_ESP_LLM_STREAM",
    "tts_max_chars": "IOT_ESP_TTS_MAX_CHARS",
    "tts_min_chars": "IOT_ESP_TTS_MIN_CHARS",
    "wake_word_hold_seconds": "IOT_ESP_WAKE_WORD_HOLD_SECONDS",
    "standby_seconds": "IOT_ESP_STANDBY_SECONDS",
    "idle_timeout_seconds": "IOT_ESP_IDLE_TIMEOUT_SECONDS",
    "standby_notice": "IOT_ESP_STANDBY_NOTICE",
    "tools_enabled": "IOT_ESP_TOOLS",
    "tool_timeout_seconds": "IOT_ESP_TOOL_TIMEOUT_SECONDS",
    "wake_words": "IOT_ESP_WAKE_WORDS",
    "voiceprint_enabled": "IOT_ESP_VOICEPRINT",
}

#: Reported but never written by the API.
READ_ONLY_KEYS: dict[str, str] = {
    "token_secret": "IOT_ESP_TOKEN_SECRET",
    "opus_dll": "IOT_OPUS_DLL",
}

MANAGED_KEYS = set(EDITABLE_KEYS.values()) | set(READ_ONLY_KEYS.values())

#: Banner for the managed block.  New keys join the existing block instead of
#: appending another copy of this line, so repeated dashboard edits cannot grow
#: the file sideways.
BLOCK_HEADER = "# --- ESP32 真機接入（由 Dashboard 寫入）---"


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        values[key.strip()] = raw.strip()
    return values


def effective_env(path: Path) -> dict[str, str]:
    """``.env`` overlaid onto the process environment (process wins, matching boot order)."""
    merged = dict(parse_env_file(path))
    merged.update({key: value for key, value in os.environ.items() if key in MANAGED_KEYS})
    return merged


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    """Apply ``KEY -> VALUE`` updates, preserving comments, order and other keys."""
    unknown = set(updates) - MANAGED_KEYS
    if unknown:
        raise ValueError(f"unsupported_env_keys:{','.join(sorted(unknown))}")
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    remaining = dict(updates)
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
                continue
        output.append(line)
    if remaining:
        appended = [f"{key}={value}" for key, value in remaining.items()]
        # Keep the managed block in one place: splice the new keys in beside the
        # existing banner rather than appending a second banner at the end.
        header_index = next(
            (index for index, line in enumerate(output) if line.strip() == BLOCK_HEADER),
            None,
        )
        if header_index is None:
            if output and output[-1].strip():
                output.append("")
            output.append(BLOCK_HEADER)
            output.extend(appended)
        else:
            output[header_index + 1 : header_index + 1] = appended
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(output) + "\n", encoding="utf-8")
    temporary.replace(path)


def to_env_value(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value)
    return str(value)


def export_env(updates: dict[str, str]) -> None:
    """Reflect freshly written values into the live process environment.

    Boot order is ``.env`` -> process env -> :class:`EspSettings`, and
    :func:`effective_env` deliberately lets the process win.  Without this
    write-through a dashboard edit would only take effect after a restart,
    which would contradict the API's ``restart_required: False``.
    """
    for key, value in updates.items():
        if key in MANAGED_KEYS:
            os.environ[key] = value
