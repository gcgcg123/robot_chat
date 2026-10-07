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

    #: How long microphone frames are dropped after the robot stops speaking.
    #:
    #: The board's own voice comes back through its microphone (the firmware's AEC is not strong
    #: enough for a speaker at desk distance), and those frames used to be treated as the user
    #: talking. Measured 2026-10-06: a turn transcribed into a mangled version of the *robot's*
    #: previous sentence ("我叫刘亲琪，心心疼小半天。" -- 心疼 came from the reply) with a voiceprint
    #: score of -0.05, and an enrollment sample would have been poisoned with the robot's voice.
    #: Frames are dropped for the whole speaking state plus this tail window; ``0`` disables only
    #: the tail.
    mic_mute_after_playback_ms: int = 400

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

    #: Device-path acceptance threshold.  ``0`` defers to ``VOICEPRINT_THRESHOLD``.
    #:
    #: A board microphone at desk distance pulls *everyone's* score toward the middle, so the value
    #: that works on a PC headset does not transfer.  Measured 2026-10-06 on the real board
    #: (templates enrolled on that board, 3 sentences): the owner's own utterances scored
    #: 0.601/0.5965/0.6453/0.578/0.6296 and 0.16-0.52 when quiet or garbled, while **another person
    #: in the room scored 0.5965** -- inside the owner's own band.  That single number is why the
    #: default here is "record everything and calibrate", not a guessed constant.
    voiceprint_threshold: float = 0.0
    voiceprint_min_margin: float = 0.0

    #: How many *consecutive* utterances must match the same user before that match is acted on.
    #:
    #: 1 reproduces the old behaviour (one utterance is enough).  2 defeats a one-off chance match;
    #: it does **not** defeat a voice that matches consistently, so it is not a substitute for the
    #: threshold.  Measured 2026-10-06: the utterance that leaked the owner's name scored 0.5965 and
    #: was preceded by a genuine 0.601 from the owner, so both matched the same user -- a streak
    #: counter alone would not have stopped that one.
    voiceprint_confirm_turns: int = 1

    #: Cosine floor between the current utterance and the previous one in the same session.
    #:
    #: ``0`` only *records* the number (``continuity_score`` in the ``speaker_identified`` event);
    #: a positive value enforces it.  The idea is that two utterances from the same mouth are close
    #: in embedding space (measured 0.767-0.812 between consecutive enrollment sentences on this
    #: board) much more reliably than a template match separates people -- but the impostor's
    #: utterance was never recorded, so this stays measurement-only until the two-speaker
    #: calibration says where the floor belongs.
    voiceprint_continuity_floor: float = 0.0

    #: Device enrollment: how many sentences, and how alike they must be.
    #:
    #: More than the browser wizard's three, because each extra recording at the real distance is
    #: another chance that one template sits near where the person actually speaks.
    #: ``enroll_min_similarity`` rejects a sample that does not look like the ones already stored --
    #: enrollment records whatever reaches the microphone, and a second voice captured as sample 3
    #: then matches that person at verification time.  Measured cross-sample similarity for one
    #: person on this board: 0.767-0.812 (device, 2026-10-06) and 0.574-0.728 (browser), so the
    #: floor is set well below those: it is a gross-contamination detector, not a quality gate.
    enroll_samples: int = 5
    enroll_min_similarity: float = 0.5
    enroll_keep_previous: bool = False

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
            mic_mute_after_playback_ms=_positive_int(env, "IOT_ESP_MIC_MUTE_AFTER_PLAYBACK_MS", 400),
            standby_seconds=_positive_int(env, "IOT_ESP_STANDBY_SECONDS", 60),
            idle_timeout_seconds=_positive_int(env, "IOT_ESP_IDLE_TIMEOUT_SECONDS", 300),
            standby_notice=str(
                env.get("IOT_ESP_STANDBY_NOTICE", "我先待命啦，需要我的時候叫我一聲就好。") or ""
            ).strip(),
            tools_enabled=_flag(env, "IOT_ESP_TOOLS", True),
            tool_timeout_seconds=_positive_float(env, "IOT_ESP_TOOL_TIMEOUT_SECONDS", 12.0),
            wake_words=tuple(_csv(str(env.get("IOT_ESP_WAKE_WORDS", "")))) or cls.wake_words,
            voiceprint_enabled=_flag(env, "IOT_ESP_VOICEPRINT", True),
            voiceprint_threshold=_non_negative_float(env, "IOT_ESP_VOICEPRINT_THRESHOLD", 0.0),
            voiceprint_min_margin=_non_negative_float(env, "IOT_ESP_VOICEPRINT_MIN_MARGIN", 0.0),
            voiceprint_confirm_turns=_positive_int(env, "IOT_ESP_VOICEPRINT_CONFIRM_TURNS", 1),
            voiceprint_continuity_floor=_non_negative_float(env, "IOT_ESP_VOICEPRINT_CONTINUITY_FLOOR", 0.0),
            enroll_samples=_positive_int(env, "IOT_ESP_ENROLL_SAMPLES", 5),
            enroll_min_similarity=_non_negative_float(env, "IOT_ESP_ENROLL_MIN_SIMILARITY", 0.5),
            enroll_keep_previous=_flag(env, "IOT_ESP_ENROLL_KEEP_PREVIOUS", False),
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
            "mic_mute_after_playback_ms": self.mic_mute_after_playback_ms,
            "standby_seconds": self.standby_seconds,
            "idle_timeout_seconds": self.idle_timeout_seconds,
            "standby_notice": self.standby_notice,
            "tools_enabled": self.tools_enabled,
            "tool_timeout_seconds": self.tool_timeout_seconds,
            "wake_words": list(self.wake_words),
            "voiceprint_enabled": self.voiceprint_enabled,
            "voiceprint_threshold": self.voiceprint_threshold,
            "voiceprint_min_margin": self.voiceprint_min_margin,
            "voiceprint_confirm_turns": self.voiceprint_confirm_turns,
            "voiceprint_continuity_floor": self.voiceprint_continuity_floor,
            "enroll_samples": self.enroll_samples,
            "enroll_min_similarity": self.enroll_min_similarity,
            "enroll_keep_previous": self.enroll_keep_previous,
        }
