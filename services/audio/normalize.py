from __future__ import annotations

from dataclasses import dataclass
import io
import wave

def _mono(frames: bytes, channels: int) -> bytes:
    if channels == 1: return frames
    out = bytearray()
    for i in range(0, len(frames), channels * 2):
        vals = [int.from_bytes(frames[i+j:i+j+2], "little", signed=True) for j in range(0, channels*2, 2)]
        out += int(sum(vals) / len(vals)).to_bytes(2, "little", signed=True)
    return bytes(out)

def _resample(frames: bytes, src: int, dst: int) -> bytes:
    if src == dst: return frames
    vals = [int.from_bytes(frames[i:i+2], "little", signed=True) for i in range(0, len(frames), 2)]
    target = max(1, round(len(vals) * dst / src)); out = bytearray()
    for i in range(target):
        idx = min(len(vals)-1, round(i * src / dst)); out += vals[idx].to_bytes(2, "little", signed=True)
    return bytes(out)


@dataclass(frozen=True)
class NormalizedAudio:
    pcm16: bytes
    sample_rate: int
    duration_ms: int


def normalize_audio(content: bytes, mime_type: str) -> NormalizedAudio:
    if not content: raise ValueError("empty_audio")
    if mime_type not in {"audio/wav", "audio/x-wav", "audio/wave"}: raise ValueError("unsupported_audio_format")
    try:
        with wave.open(io.BytesIO(content), "rb") as wav:
            channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError("unsupported_audio_format") from exc
    if width != 2: raise ValueError("unsupported_sample_width")
    if channels > 1: frames = _mono(frames, channels)
    if rate != 16000: frames = _resample(frames, rate, 16000)
    return NormalizedAudio(frames, 16000, round(len(frames) / 2 / 16000 * 1000))
