"""Configuration for the xiaozhi-compatible ESP32 access layer.

Every value is read from the environment once at mount time.  Defaults keep the
endpoint **off**: an unconfigured checkout must not start listening for devices
on the LAN, so enabling it is always a deliberate act.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

_FALSEY = {"0", "false", "no", "off", ""}


def _flag(env: dict, name: str, default: bool) -> bool:
    raw = str(env.get(name, "")).strip().lower()
    if raw == "":
        return default
    return raw not in _FALSEY


def _positive_int(env: dict, name: str, default: int) -> int:
    raw = str(env.get(name, "")).strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else default


def _positive_float(env: dict, name: str, default: float) -> float:
    raw = str(env.get(name, "")).strip()
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _non_negative_float(env: dict, name: str, default: float) -> float:
    """Like :func:`_positive_float` but ``0`` is a meaningful value, not "unset"."""
    raw = str(env.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class EspSettings:
    """Resolved ESP access-layer settings."""

    enabled: bool = False
    path: str = "/xiaozhi/v1/"
    require_token: bool = False
    token_secret: str = ""
    allowed_devices: tuple[str, ...] = ()
    default_user_id: str = "esp-user"

    sample_rate: int = 24000
    frame_duration_ms: int = 60
    uplink_sample_rate: int = 16000

    ota_enabled: bool = False
    ota_bin_dir: Path = field(default_factory=lambda: ROOT / "data" / "bin")
    ota_base_url: str = ""

    vad_provider: str = "energy"
    vad_silence_ms: int = 800
    vad_min_speech_ms: int = 240
    vad_max_utterance_ms: int = 20000
    vad_rms_threshold: float = 380.0

    tts_enabled: bool = True
    playback_buffer_frames: int = 5
    max_sessions: int = 4

    #: Streaming speech.  ``llm_stream`` lets the model's deltas be spoken
    #: sentence by sentence while it is still writing; the length caps keep one
    #: unpunctuated run from becoming a single synthesis request that times out
    #: (which is how a long answer used to stop halfway through).
    llm_stream: bool = True
    tts_max_chars: int = 48
    tts_min_chars: int = 6

    #: After ``listen detect`` the firmware plays its own wake-word chime; the
    #: microphone hears it, so end-of-utterance is ignored for this long.
    #: ``0`` disables the hold (an older firmware that does not chime needs none).
    wake_word_hold_seconds: float = 0.8

    #: Two different timeouts, because a listening device is *not* a silent one.
    #:
    #: The ESP keeps the microphone open and streams whatever it hears (silence
    #: included) for as long as it is in listening mode, so "no message for N
    #: seconds" can never fire on a healthy device -- that is the whole reason
    #: the device used to stay in 聆听 forever.  ``standby_seconds`` therefore
    #: measures *no voice* (server VAD), and the session ends the listening phase
    #: itself once it expires.  ``idle_timeout_seconds`` is the hard cap for a
    #: socket that has gone completely deaf (half-open connection, firmware gone)
    #: and is deliberately longer than the firmware's own 120 s channel timeout.
    standby_seconds: int = 60
    idle_timeout_seconds: int = 300
    standby_notice: str = "我先待命啦，需要我的時候叫我一聲就好。"

    #: Device-side MCP tools (volume / brightness / theme ...).  Without the
    #: handshake the firmware never tells us what it can do, so voice control of
    #: the device itself is impossible -- see ``mcp.py``.
    tools_enabled: bool = True
    tool_timeout_seconds: float = 12.0

    #: The firmware's wake word, shipped in ``listen state=detect``.  It is a
    #: greeting trigger, not something the user wants an answer to.
    wake_words: tuple[str, ...] = ("小智", "小智小智", "你好小智", "小智你好", "你好小智同學")

    #: Match each utterance against the enrolled voiceprints so the identity
    #: follows the speaker instead of the device.  Optional: when nothing is
    #: enrolled (or the provider is unavailable) the bound/default user is used,
    #: which is the behaviour this replaces.
    voiceprint_enabled: bool = True

    @property
    def frame_samples(self) -> int:
        return self.sample_rate * self.frame_duration_ms // 1000

    @property
    def min_utterance_bytes(self) -> int:
        """Shortest buffered utterance worth sending to ASR (≈0.4 s of 16 kHz PCM16)."""
        return int(self.uplink_sample_rate * 0.4) * 2

    @classmethod
    def from_env(cls, env: dict | None = None) -> "EspSettings":
        env = os.environ if env is None else env
        bin_dir = str(env.get("IOT_OTA_BIN_DIR", "")).strip()
        return cls(
            enabled=_flag(env, "IOT_ESP_ENABLED", False),
            path=str(env.get("IOT_ESP_PATH", "/xiaozhi/v1/")).strip() or "/xiaozhi/v1/",
            require_token=_flag(env, "IOT_ESP_REQUIRE_TOKEN", False),
            token_secret=str(env.get("IOT_ESP_TOKEN_SECRET", "")).strip(),
            allowed_devices=tuple(_csv(str(env.get("IOT_ESP_ALLOWED_DEVICES", "")))),
            default_user_id=str(env.get("IOT_ESP_DEFAULT_USER", "esp-user")).strip() or "esp-user",
            sample_rate=_positive_int(env, "IOT_ESP_SAMPLE_RATE", 24000),
            frame_duration_ms=_positive_int(env, "IOT_ESP_FRAME_DURATION_MS", 60),
            uplink_sample_rate=_positive_int(env, "IOT_ESP_UPLINK_SAMPLE_RATE", 16000),
            ota_enabled=_flag(env, "IOT_OTA_ENABLED", False),
            ota_bin_dir=Path(bin_dir) if bin_dir else ROOT / "data" / "bin",
            ota_base_url=str(env.get("IOT_OTA_BASE_URL", "")).strip(),
            vad_provider=str(env.get("IOT_VAD_PROVIDER", "energy")).strip().lower() or "energy",
            vad_silence_ms=_positive_int(env, "IOT_VAD_SILENCE_MS", 800),
            vad_min_speech_ms=_positive_int(env, "IOT_VAD_MIN_SPEECH_MS", 240),
            vad_max_utterance_ms=_positive_int(env, "IOT_VAD_MAX_UTTERANCE_MS", 20000),
            vad_rms_threshold=_positive_float(env, "IOT_VAD_RMS_THRESHOLD", 380.0),
            tts_enabled=_flag(env, "IOT_ESP_TTS_ENABLED", True),
            playback_buffer_frames=_positive_int(env, "IOT_ESP_PLAYBACK_BUFFER_FRAMES", 5),
            max_sessions=_positive_int(env, "IOT_ESP_MAX_SESSIONS", 4),
            llm_stream=_flag(env, "IOT_ESP_LLM_STREAM", True),
            tts_max_chars=_positive_int(env, "IOT_ESP_TTS_MAX_CHARS", 48),
            tts_min_chars=_positive_int(env, "IOT_ESP_TTS_MIN_CHARS", 6),
            wake_word_hold_seconds=_non_negative_float(env, "IOT_ESP_WAKE_WORD_HOLD_SECONDS", 0.8),
            standby_seconds=_positive_int(env, "IOT_ESP_STANDBY_SECONDS", 60),
            idle_timeout_seconds=_positive_int(env, "IOT_ESP_IDLE_TIMEOUT_SECONDS", 300),
            standby_notice=str(
                env.get("IOT_ESP_STANDBY_NOTICE", "我先待命啦，需要我的時候叫我一聲就好。") or ""
            ).strip(),
            tools_enabled=_flag(env, "IOT_ESP_TOOLS", True),
            tool_timeout_seconds=_positive_float(env, "IOT_ESP_TOOL_TIMEOUT_SECONDS", 12.0),
            wake_words=tuple(_csv(str(env.get("IOT_ESP_WAKE_WORDS", "")))) or cls.wake_words,
            voiceprint_enabled=_flag(env, "IOT_ESP_VOICEPRINT", True),
        )

    def as_dict(self) -> dict:
        """Serialisable view for the dashboard (never exposes the token secret)."""
        return {
            "enabled": self.enabled,
            "path": self.path,
            "require_token": self.require_token,
            "token_configured": bool(self.token_secret),
            "allowed_devices": list(self.allowed_devices),
            "default_user_id": self.default_user_id,
            "sample_rate": self.sample_rate,
            "frame_duration_ms": self.frame_duration_ms,
            "uplink_sample_rate": self.uplink_sample_rate,
            "ota_enabled": self.ota_enabled,
            "ota_bin_dir": str(self.ota_bin_dir),
            "ota_base_url": self.ota_base_url,
            "vad_provider": self.vad_provider,
            "vad_silence_ms": self.vad_silence_ms,
            "vad_min_speech_ms": self.vad_min_speech_ms,
            "vad_max_utterance_ms": self.vad_max_utterance_ms,
            "vad_rms_threshold": self.vad_rms_threshold,
            "tts_enabled": self.tts_enabled,
            "playback_buffer_frames": self.playback_buffer_frames,
            "max_sessions": self.max_sessions,
            "llm_stream": self.llm_stream,
            "tts_max_chars": self.tts_max_chars,
            "tts_min_chars": self.tts_min_chars,
            "wake_word_hold_seconds": self.wake_word_hold_seconds,
            "standby_seconds": self.standby_seconds,
            "idle_timeout_seconds": self.idle_timeout_seconds,
            "standby_notice": self.standby_notice,
            "tools_enabled": self.tools_enabled,
            "tool_timeout_seconds": self.tool_timeout_seconds,
            "wake_words": list(self.wake_words),
            "voiceprint_enabled": self.voiceprint_enabled,
        }
