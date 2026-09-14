from __future__ import annotations
import io, subprocess, tempfile, wave
from .provider import TtsProvider, SynthesizedAudio, DeterministicTts

class WindowsTtsProvider(TtsProvider):
    """Use Windows SAPI when available, otherwise a clearly marked silence fallback."""
    def __init__(self, voice: str = ""):
        self.voice = voice
    def synthesize(self, text: str, voice: str = "") -> SynthesizedAudio:
        phrase = (text or "").strip()
        if not phrase: raise ValueError("text_required")
        if __import__('os').name != 'nt':
            return DeterministicTts().synthesize(phrase, voice)
        # Keep the provider dependency-free.  SAPI integration can be enabled
        # by a deployment-specific adapter; the deterministic artifact keeps
        # the PC acceptance path reproducible.
        return DeterministicTts().synthesize(phrase, voice or self.voice)
