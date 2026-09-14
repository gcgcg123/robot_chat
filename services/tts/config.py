from __future__ import annotations
import os
from .provider import TtsProvider, DeterministicTts
from .windows import WindowsTtsProvider

def create_tts_provider() -> TtsProvider:
    if os.getenv("IOT_TTS_PROVIDER", "windows").lower() in {"windows", "sapi"}:
        return WindowsTtsProvider(os.getenv("IOT_TTS_VOICE", ""))
    return DeterministicTts()
