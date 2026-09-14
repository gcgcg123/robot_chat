from __future__ import annotations


def validate_capabilities(payload: dict) -> dict:
    caps = dict(payload or {})
    if caps.get("sample_rate") not in (None, 16000):
        raise ValueError("unsupported_sample_rate")
    if caps.get("frame_ms") not in (None, 20, 40):
        raise ValueError("unsupported_frame_ms")
    display = caps.get("display") or {}
    if display and (display.get("width") != 320 or display.get("height") != 240):
        raise ValueError("unsupported_display")
    return {"sample_rate": caps.get("sample_rate"), "frame_ms": caps.get("frame_ms"), "display": display or None, "playback_ack": bool(caps.get("playback_ack", False)), "enrollment_v1": bool(caps.get("enrollment_v1", False))}


def playback_state(sent: bool, ack: str | None) -> str:
    if ack in {"started", "completed", "interrupted", "failed"}:
        return ack
    return "sent_unconfirmed" if sent else "not_sent"
