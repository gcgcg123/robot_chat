"""Real speech through Microsoft Edge's online TTS, decoded to PCM16 locally.

Why this provider exists: the previous default emitted silence, so a device
could complete the whole protocol and still play nothing -- a failure that looks
exactly like a broken audio path.  Edge TTS gives good Mandarin/Cantonese/English
voices with no API key, which makes the ESP loop genuinely audible.

Two implementation details matter:

* ``edge_tts`` is async-only, and ``synthesize`` is called from *sync* code that
  may itself be running inside an event loop (the simulator WebSocket does
  exactly that).  A dedicated worker thread owning its own loop keeps the call
  legal from every context instead of blowing up with "loop already running".
* Edge returns MP3; Opus needs PCM16.  ffmpeg is already a documented
  prerequisite of this project (``E:\\tools\\ffmpeg\\bin``), so it is used rather
  than adding a pure-Python MP3 decoder.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from .provider import SynthesizedAudio, TtsProvider

DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
DEFAULT_SAMPLE_RATE = 24000
SYNTHESIS_TIMEOUT_SECONDS = 25

#: Language code -> Edge voice, mirroring ``services/enrollment/languages.py``.
DEFAULT_VOICE_BY_LANGUAGE = {
    "yue-HK": "zh-HK-HiuMaanNeural",
    "zh-CN": "zh-CN-XiaoxiaoNeural",
    "en-US": "en-US-AriaNeural",
}

_FFMPEG_CANDIDATES = (
    Path("E:/tools/ffmpeg/bin/ffmpeg.exe"),
    Path("C:/ffmpeg/bin/ffmpeg.exe"),
    Path("/usr/bin/ffmpeg"),
    Path("/usr/local/bin/ffmpeg"),
)


class TtsDependencyError(RuntimeError):
    """The provider cannot run: a dependency (edge-tts or ffmpeg) is missing."""


def resolve_ffmpeg() -> str:
    override = os.getenv("IOT_FFMPEG", "").strip()
    if override and Path(override).is_file():
        return override
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in _FFMPEG_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    raise TtsDependencyError("ffmpeg_not_found: set IOT_FFMPEG or add ffmpeg to PATH")


class _LoopThread:
    """A daemon thread that owns one event loop for all async TTS calls."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve, name="edge-tts-loop", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coroutine, timeout: float):
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        return future.result(timeout=timeout)


_loop_thread: _LoopThread | None = None
_loop_lock = threading.Lock()


def _shared_loop() -> _LoopThread:
    global _loop_thread
    with _loop_lock:
        if _loop_thread is None or not _loop_thread._thread.is_alive():  # noqa: SLF001 - guarded singleton
            _loop_thread = _LoopThread()
        return _loop_thread


class EdgeTtsProvider(TtsProvider):
    """Online neural TTS (edge-tts) with local MP3 -> PCM16 decoding."""

    name = "edge"

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        rate: str = "",
        volume: str = "",
        ffmpeg: str = "",
        voices_by_language: dict[str, str] | None = None,
    ) -> None:
        self.voice = voice or DEFAULT_VOICE
        self.sample_rate = sample_rate
        self.rate = rate
        self.volume = volume
        self._ffmpeg = ffmpeg
        self.voices_by_language = dict(voices_by_language or DEFAULT_VOICE_BY_LANGUAGE)

    # ------------------------------------------------------------------ helpers

    def resolve_voice(self, voice: str) -> str:
        """``voice`` may be a language code, a literal Edge voice name, or empty."""
        candidate = (voice or "").strip()
        if not candidate:
            return self.voice
        if candidate in self.voices_by_language:
            return self.voices_by_language[candidate]
        return candidate

    def _binary(self) -> str:
        return self._ffmpeg or resolve_ffmpeg()

    @staticmethod
    def _synthesize_mp3(text: str, voice: str, rate: str, volume: str) -> bytes:
        try:
            import edge_tts
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise TtsDependencyError("edge_tts_not_installed: pip install edge-tts") from exc

        async def collect() -> bytes:
            communicate = edge_tts.Communicate(text, voice, rate=rate or "+0%", volume=volume or "+0%")
            chunks = bytearray()
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    chunks.extend(chunk["data"])
            return bytes(chunks)

        return _shared_loop().run(collect(), SYNTHESIS_TIMEOUT_SECONDS)

    def _decode(self, mp3: bytes) -> bytes:
        binary = self._binary()
        with tempfile.TemporaryDirectory(prefix="iot-tts-") as workdir:
            source = Path(workdir) / "segment.mp3"
            target = Path(workdir) / "segment.pcm"
            source.write_bytes(mp3)
            command = [
                binary, "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source),
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ac", "1", "-ar", str(self.sample_rate),
                str(target),
            ]
            try:
                completed = subprocess.run(command, capture_output=True, timeout=SYNTHESIS_TIMEOUT_SECONDS, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise TtsDependencyError(f"ffmpeg_decode_failed:{exc}") from exc
            if completed.returncode != 0 or not target.is_file():
                message = completed.stderr.decode("utf-8", "ignore").strip()[:200]
                raise TtsDependencyError(f"ffmpeg_decode_failed:{message}")
            return target.read_bytes()

    # ------------------------------------------------------------------ contract

    def synthesize(self, text: str, voice: str = "") -> SynthesizedAudio:
        phrase = (text or "").strip()
        if not phrase:
            raise ValueError("text_required")
        resolved = self.resolve_voice(voice)
        mp3 = self._synthesize_mp3(phrase, resolved, self.rate, self.volume)
        if not mp3:
            raise TtsDependencyError("edge_tts_returned_no_audio")
        pcm16 = self._decode(mp3)
        if not pcm16:
            raise TtsDependencyError("decoded_audio_empty")
        duration_ms = round(len(pcm16) / 2 / self.sample_rate * 1000)
        return SynthesizedAudio(pcm16, self.sample_rate, 1, duration_ms, f"edge:{resolved}")

    def probe(self) -> dict:
        """Report readiness without synthesising anything (used by ``/api/esp/status``)."""
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            return {"ready": False, "reason": "edge_tts_not_installed"}
        try:
            binary = self._binary()
        except TtsDependencyError as exc:
            return {"ready": False, "reason": str(exc)}
        return {"ready": True, "reason": "", "ffmpeg": binary, "voice": self.voice}
