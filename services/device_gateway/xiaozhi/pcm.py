"""PCM16 helpers for the ESP transport.

Deliberately separate from :mod:`services.audio.normalize`: that module owns the
HTTP upload path (WAV in, 16 kHz out) and must not grow ESP-specific concerns
such as 24 kHz downlink framing.  Pure stdlib, so importing this module never
costs a dependency.
"""
from __future__ import annotations

import array
import io
import wave

_SAMPLE_WIDTH = 2


def _samples(pcm16: bytes) -> array.array:
    usable = len(pcm16) - (len(pcm16) % _SAMPLE_WIDTH)
    values = array.array("h")
    values.frombytes(pcm16[:usable])
    return values


def rms(pcm16: bytes) -> float:
    """Root-mean-square amplitude of a PCM16 buffer, in int16 units."""
    values = _samples(pcm16)
    if not values:
        return 0.0
    total = 0
    for value in values:
        total += value * value
    return (total / len(values)) ** 0.5


def sample_count(pcm16: bytes) -> int:
    return len(pcm16) // _SAMPLE_WIDTH


def duration_ms(pcm16: bytes, sample_rate: int) -> int:
    return round(sample_count(pcm16) / max(1, sample_rate) * 1000)


def to_wav(pcm16: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Wrap raw PCM16 in a WAV container (the shape ``normalize_audio`` accepts)."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(_SAMPLE_WIDTH)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm16)
    return buffer.getvalue()


def from_wav(payload: bytes) -> tuple[bytes, int, int]:
    """Return ``(pcm16, sample_rate, channels)`` for a WAV payload."""
    with wave.open(io.BytesIO(payload), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != _SAMPLE_WIDTH:
        raise ValueError("unsupported_sample_width")
    if channels > 1:
        frames = to_mono(frames, channels)
    return frames, rate, 1


def to_mono(pcm16: bytes, channels: int) -> bytes:
    if channels <= 1:
        return pcm16
    values = _samples(pcm16)
    out = array.array("h")
    for index in range(0, len(values) - channels + 1, channels):
        total = 0
        for offset in range(channels):
            total += values[index + offset]
        out.append(int(total / channels))
    return out.tobytes()


def resample(pcm16: bytes, source_rate: int, target_rate: int) -> bytes:
    """Linear-interpolation resampler.

    Opus needs the sample rate the device negotiated, while the TTS provider
    decides its own output rate, so a conversion is unavoidable.  Linear
    interpolation is enough for 16 kHz ↔ 24 kHz speech and needs no SciPy.
    """
    if source_rate == target_rate or not pcm16:
        return pcm16
    values = _samples(pcm16)
    if not values:
        return b""
    target_count = max(1, round(len(values) * target_rate / source_rate))
    out = array.array("h", bytes(target_count * _SAMPLE_WIDTH))
    last = len(values) - 1
    for index in range(target_count):
        position = index * source_rate / target_rate
        left = int(position)
        if left >= last:
            out[index] = values[last]
            continue
        fraction = position - left
        out[index] = int(values[left] * (1 - fraction) + values[left + 1] * fraction)
    return out.tobytes()
