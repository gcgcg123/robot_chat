from __future__ import annotations
import os
from .provider import TtsProvider, DeterministicTts
from .windows import WindowsTtsProvider


def create_tts_provider() -> TtsProvider:
    """Build the configured TTS provider.

    ``IOT_TTS_PROVIDER`` values:

    * ``edge``   -- Microsoft Edge online neural TTS (real speech, needs network
                    and ffmpeg).  Required for the ESP32 to actually be audible.
    * ``windows``/``sapi`` -- Windows SAPI shim; currently still silence.
    * anything else -- ``DeterministicTts`` silence, for offline tests.

    The default stays ``windows`` so an existing install behaves exactly as
    before; ``/api/esp/status`` reports ``produces_audio=false`` whenever the
    active provider only emits silence, so a "connected but mute" device is
    diagnosed instead of mysterious.
    """
    provider = os.getenv("IOT_TTS_PROVIDER", "windows").strip().lower()
    if provider == "edge":
        from .edge import EdgeTtsProvider

        voices = {
            code: os.getenv(f"IOT_TTS_VOICE_{code.split('-')[0].upper()}", "").strip()
            for code in ("yue-HK", "zh-CN", "en-US")
        }
        return EdgeTtsProvider(
            voice=os.getenv("IOT_TTS_EDGE_VOICE", "zh-CN-XiaoxiaoNeural").strip() or "zh-CN-XiaoxiaoNeural",
            rate=os.getenv("IOT_TTS_EDGE_RATE", "").strip(),
            volume=os.getenv("IOT_TTS_EDGE_VOLUME", "").strip(),
            ffmpeg=os.getenv("IOT_FFMPEG", "").strip(),
            voices_by_language={code: value for code, value in voices.items() if value},
        )
    if provider in {"windows", "sapi"}:
        return WindowsTtsProvider(os.getenv("IOT_TTS_VOICE", ""))
    return DeterministicTts()
