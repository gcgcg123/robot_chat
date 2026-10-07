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
    #: Whether ``synthesize`` produces audible speech, as opposed to a silent placeholder.
    #:
    #: Declared per provider rather than probed at runtime, because ``/api/esp/status`` has to
    #: answer without making a network request (edge-tts) or spawning anything. The base class
    #: assumes True, so a provider that forgets to declare only risks a *pessimistic* diagnostic
    #: being wrong, not a silent device being reported as audible -- which is the failure this
    #: exists for: ``WindowsTtsProvider`` emits pure silence and still answered produces_audio=true.
    produces_audio: bool = True

    def synthesize(self, text: str, voice: str = "") -> SynthesizedAudio:
        raise NotImplementedError


class DeterministicTts(TtsProvider):
    """Offline test provider. It intentionally emits silence, never claims speech quality."""

    produces_audio = False

    def synthesize(self, text: str, voice: str = "") -> SynthesizedAudio:
        duration_ms = max(120, min(5000, len(text.strip()) * 55))
        samples = int(16000 * duration_ms / 1000)
        return SynthesizedAudio(b"\x00\x00" * samples, 16000, 1, duration_ms, "deterministic-silence")
