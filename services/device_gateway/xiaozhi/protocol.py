"""The xiaozhi text protocol (protocol version 1), as plain data.

Implemented from the upstream server's behaviour rather than by importing it:
``core/handle/textHandler/*`` and ``core/utils/textUtils.py`` define

* uplink  ``hello`` / ``listen`` / ``abort`` / ``iot`` / ``mcp`` / ``ping``
* downlink ``hello`` / ``stt`` / ``tts`` / ``llm`` / ``iot`` / ``pong``

Pure functions only, so the wire format is unit-testable without a socket.
"""
from __future__ import annotations

import json
from typing import Any

from services.emoji import EMOJI_TO_EMOTION, emotion_from_emoji

PROTOCOL_VERSION = 1

UPLINK_TYPES = frozenset({"hello", "listen", "abort", "iot", "mcp", "ping", "server"})
LISTEN_STATES = frozenset({"start", "stop", "detect"})
LISTEN_MODES = frozenset({"auto", "manual", "realtime"})

TTS_STATES = frozenset({"start", "sentence_start", "stop"})

#: Emotion vocabulary the ESP firmware understands.  Kept in lock-step with the
#: emoji table so the two can never drift: every name here is reachable by
#: sending the paired emoji, and ``SetEmotion`` knows every one of them
#: (``main/display/emoji_collection.cc`` has an image for each).
DEVICE_EMOTIONS = frozenset(EMOJI_TO_EMOTION.values())

#: Project emotion (+ risk level) -> (emotion, emoji) understood by the firmware.
_EMOTION_TABLE = {
    ("negative", "urgent"): ("shocked", "😱"),
    ("negative", "attention"): ("sad", "😔"),
    ("negative", ""): ("sad", "😔"),
    ("positive", ""): ("happy", "🙂"),
    ("neutral", ""): ("neutral", "😶"),
}


class ProtocolError(ValueError):
    """Raised for malformed or unsupported uplink messages."""


def parse_text(raw: str | bytes) -> dict[str, Any]:
    """Decode one uplink JSON message and validate its envelope."""
    try:
        body = json.loads(raw)
    except (TypeError, ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError("invalid_json") from exc
    if not isinstance(body, dict):
        raise ProtocolError("message_must_be_object")
    message_type = str(body.get("type", "")).strip()
    if not message_type:
        raise ProtocolError("message_type_required")
    if message_type not in UPLINK_TYPES:
        raise ProtocolError(f"unsupported_message_type:{message_type}")
    return body


def parse_listen(body: dict[str, Any]) -> tuple[str, str, str]:
    """Return ``(state, mode, text)`` for a ``listen`` message."""
    state = str(body.get("state", "")).strip()
    if state not in LISTEN_STATES:
        raise ProtocolError(f"unsupported_listen_state:{state or 'missing'}")
    mode = str(body.get("mode", "")).strip()
    if mode and mode not in LISTEN_MODES:
        raise ProtocolError(f"unsupported_listen_mode:{mode}")
    text = str(body.get("text", "") or "").strip()
    return state, mode, text


def map_emotion(emotion: str, risk_level: str = "") -> tuple[str, str]:
    """Map a project emotion to the ``(emotion, emoji)`` pair the firmware renders."""
    level = risk_level if risk_level in {"attention", "urgent"} else ""
    if level:
        mapped = _EMOTION_TABLE.get((emotion, level)) or _EMOTION_TABLE.get(("negative", level))
        if mapped:
            return mapped
    return _EMOTION_TABLE.get((emotion, ""), ("neutral", "😶"))


def welcome_message(
    session_id: str,
    *,
    sample_rate: int = 24000,
    frame_duration_ms: int = 60,
    channels: int = 1,
    audio_format: str = "opus",
) -> dict[str, Any]:
    return {
        "type": "hello",
        "version": PROTOCOL_VERSION,
        "transport": "websocket",
        "session_id": session_id,
        "audio_params": {
            "format": audio_format,
            "sample_rate": sample_rate,
            "channels": channels,
            "frame_duration": frame_duration_ms,
        },
    }


def stt_message(text: str, session_id: str) -> dict[str, Any]:
    return {"type": "stt", "text": text, "session_id": session_id}


def tts_message(state: str, session_id: str, text: str | None = None) -> dict[str, Any]:
    if state not in TTS_STATES:
        raise ProtocolError(f"unsupported_tts_state:{state}")
    message: dict[str, Any] = {"type": "tts", "state": state, "session_id": session_id}
    if text is not None:
        message["text"] = text
    return message


def llm_message(emotion: str, session_id: str, *, text: str = "", risk_level: str = "") -> dict[str, Any]:
    """Emotion hint sent before playback so the LCD can switch expression."""
    name, emoji = map_emotion(emotion, risk_level)
    return {"type": "llm", "emotion": name, "text": text or emoji, "session_id": session_id}


def face_message(
    session_id: str,
    *,
    reply_text: str = "",
    fallback_emotion: str = "",
    risk_level: str = "",
) -> dict[str, Any]:
    """The one ``llm`` message that decides the device's expression.

    Upstream reads the expression off the model's own reply -- the emoji it was
    told to prefix -- which is why the face moves with the *conversation* rather
    than with three coarse sentiment labels.  Two deliberate differences:

    * no emoji in the reply means we use the caller's verdict on the user's mood
      instead of upstream's blind ``🙂/happy`` (a cheerful face over a sad turn
      is worse than a neutral one, and the model does occasionally forget);
    * a risk-flagged turn (``attention``/``urgent``) overrides the model's emoji
      outright, because the face is a safety signal before it is decoration.
    """
    if risk_level in {"attention", "urgent"}:
        name, emoji = map_emotion(fallback_emotion or "negative", risk_level)
        return {"type": "llm", "emotion": name, "text": emoji, "session_id": session_id}
    found = emotion_from_emoji(reply_text)
    if found is not None:
        emoji, name = found
        return {"type": "llm", "emotion": name, "text": emoji, "session_id": session_id}
    name, emoji = map_emotion(fallback_emotion or "neutral")
    return {"type": "llm", "emotion": name, "text": emoji, "session_id": session_id}


def iot_message(commands: list[dict[str, Any]], session_id: str) -> dict[str, Any]:
    return {"type": "iot", "commands": commands, "session_id": session_id}


def pong_message(session_id: str) -> dict[str, Any]:
    return {"type": "pong", "session_id": session_id}


def error_message(reason: str, session_id: str) -> dict[str, Any]:
    """Non-standard but explicitly documented: never silently drop a failure."""
    return {"type": "error", "reason": reason, "session_id": session_id}


def dumps(message: dict[str, Any]) -> str:
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))
