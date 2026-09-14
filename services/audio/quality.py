from __future__ import annotations

import math


def check_quality(audio, enrollment: bool = False) -> dict:
    pcm = audio.pcm16 or b""
    if not pcm: return {"accepted": False, "reason": "empty_audio", "speech_duration_ms": 0, "clipping_ratio": 0.0, "rms": 0}
    samples = [int.from_bytes(pcm[i:i+2], "little", signed=True) for i in range(0, len(pcm)-1, 2)]
    rms = round(math.sqrt(sum(x*x for x in samples) / max(1, len(samples))))
    clipping = sum(1 for x in samples if abs(x) >= 32760) / max(1, len(samples))
    if clipping > 0.01: reason = "clipping"
    elif rms < 150: reason = "no_speech"
    else: reason = None
    speech = 0 if reason else audio.duration_ms
    minimum = 3000 if enrollment else 1
    accepted = reason is None and speech >= minimum
    if not accepted and reason is None: reason = "too_short"
    return {"accepted": accepted, "reason": reason, "speech_duration_ms": speech, "clipping_ratio": round(clipping, 4), "rms": rms}
