from __future__ import annotations

from dataclasses import dataclass
import wave
import io


@dataclass(frozen=True)
class SynthesizedAudio:
    pcm16: bytes
    sample_rate: int
    channels: int
    duration_ms: int
    provider: str


class TtsProvider:
    def synthesize(self, text: str, voice: str = "") -> SynthesizedAudio:
        raise NotImplementedError


class DeterministicTts(TtsProvider):
    """Offline test provider. It intentionally emits silence, never claims speech quality."""
    def synthesize(self, text: str, voice: str = "") -> SynthesizedAudio:
        duration_ms = max(120, min(5000, len(text.strip()) * 55))
        samples = int(16000 * duration_ms / 1000)
        return SynthesizedAudio(b"\x00\x00" * samples, 16000, 1, duration_ms, "deterministic-silence")
