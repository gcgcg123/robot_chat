from __future__ import annotations
import os
import subprocess
import tempfile
import wave
from .provider import TtsProvider, SynthesizedAudio, DeterministicTts

class WindowsTtsProvider(TtsProvider):
    """Windows SAPI provider with an explicit deterministic fallback off Windows."""
    def __init__(self, voice: str = ""):
        self.voice = voice

    def status(self) -> dict:
        return {
            "provider": "windows-sapi",
            "available": os.name == "nt",
            "voice": self.voice,
            "options": [
                {"id": "", "label": "系统默认"},
                {"id": "female", "label": "女声（可配置为林志玲音色）"},
                {"id": "male", "label": "男声（可配置为懒洋洋音色）"},
            ],
        }

    def synthesize(self, text: str, voice: str = "") -> SynthesizedAudio:
        phrase = (text or "").strip()
        if not phrase: raise ValueError("text_required")
        if os.name != "nt":
            return DeterministicTts().synthesize(phrase, voice)
        selected_voice = {
            "female": os.getenv("IOT_TTS_VOICE_FEMALE", ""),
            "male": os.getenv("IOT_TTS_VOICE_MALE", ""),
        }.get(voice, voice or self.voice)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as output:
            path = output.name
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$v=$args[1]; if ($v) {$s.SelectVoice($v)}; "
            "$s.SetOutputToWaveFile($args[0]); "
            "$s.Speak([Console]::In.ReadToEnd()); $s.Dispose()"
        )
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script, path, selected_voice],
                input=phrase, text=True, capture_output=True, check=False,
            )
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or "sapi_failed")
            with wave.open(path, "rb") as wav:
                if wav.getsampwidth() != 2:
                    raise RuntimeError("sapi_non_pcm16")
                pcm = wav.readframes(wav.getnframes())
                rate, channels = wav.getframerate(), wav.getnchannels()
            return SynthesizedAudio(pcm, rate, channels, round(len(pcm) * 1000 / (rate * channels * 2)), "windows-sapi")
        finally:
            try: os.unlink(path)
            except OSError: pass
