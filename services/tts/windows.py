"""Windows SAPI speech, falling back to a clearly marked silence placeholder.

Why SAPI is the default here rather than an online service: the reply text of an elderly user should
not have to leave the machine to be *spoken*, and a device that needs the internet to make a sound
is a device that goes mute in a care home with a flaky router. Measured on the reference laptop
(2026-10-06): a Chinese voice is installed by default (Microsoft Huihui, zh-CN), synthesis of a
sentence takes ~0.4-0.9 s including process start, and the result is 22050 Hz mono PCM16 which the
device player resamples to its 24 kHz Opus downlink.

Before this, ``synthesize`` returned ``DeterministicTts``' zeros while ``/api/esp/status`` claimed
``produces_audio: true`` -- which is exactly the wrong pair of answers to give someone debugging a
silent board. ``produces_audio`` is now an instance fact, set only after SAPI has proved usable.

``IOT_TTS_PROVIDER=edge`` still selects the online neural voice (nicer, needs network + ffmpeg).
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import tempfile
import wave
from .provider import TtsProvider, SynthesizedAudio, DeterministicTts

#: Script run once to find out what SAPI can do.  Kept as a literal so the probe and the synthesis
#: agree on how voices are described.
_PROBE = (
    "Add-Type -AssemblyName System.Speech;"
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "$s.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object {"
    " '{0}|{1}' -f $_.VoiceInfo.Name, $_.VoiceInfo.Culture.Name };"
    "$s.Dispose()"
)

#: Synthesis script, written to a temp .ps1 and invoked with -File: a ``param()`` block cannot be
#: passed through ``-Command`` (PowerShell parses the arguments as part of the command text, which
#: is how the first version of this file failed with "Unexpected token <temp path>").
_SYNTH = """param([string]$TextPath, [string]$OutPath, [string]$VoiceName)
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($VoiceName) { $synth.SelectVoice($VoiceName) }
$synth.SetOutputToWaveFile($OutPath)
$synth.Speak([System.IO.File]::ReadAllText($TextPath, [System.Text.Encoding]::UTF8))
$synth.Dispose()
"""

_CJK = re.compile(r"[\u3400-\u9fff]")
_POWERSHELL = os.environ.get("SystemRoot", r"C:\Windows") + r"\System32\WindowsPowerShell\v1.0\powershell.exe"


def _run(script: str, *arguments: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script, *arguments],
        capture_output=True,
        timeout=timeout,
    )


def _run_script(script_path: str, *arguments: str, timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script_path, *arguments],
        capture_output=True,
        timeout=timeout,
    )


class WindowsTtsProvider(TtsProvider):
    """SAPI when available, otherwise silence -- and it says which of the two it is."""

    def __init__(self, voice: str = ""):
        self.voice = voice
        self._voices: list[tuple[str, str]] | None = None
        self._reason = ""
        #: Whether this provider can put audible speech on the wire. False until SAPI has been found
        #: (or after a synthesis failure), so ``/api/esp/status`` never claims audio for a provider
        #: that only emits zeros -- which is what the old "silence fallback" did.
        self.produces_audio = False

    # -- capability -----------------------------------------------------------------

    def _installed(self) -> list[tuple[str, str]]:
        """``[(voice name, culture)]`` once, cached; empty when SAPI is unusable."""

        if self._voices is not None:
            return self._voices
        if os.name != "nt":
            self._reason = "not_windows"
            self._voices = []
            return self._voices
        try:
            result = _run(_PROBE, timeout=20)
        except Exception as exc:  # noqa: BLE001 - a missing SAPI is a diagnostic, not a crash
            self._reason = f"probe_failed:{type(exc).__name__}"
            self._voices = []
            return self._voices
        voices: list[tuple[str, str]] = []
        for line in (result.stdout or "").decode("utf-8", "replace").splitlines():
            if "|" in line:
                name, _, culture = line.strip().partition("|")
                if name and culture:
                    voices.append((name, culture))
        if not voices:
            self._reason = "no_sapi_voices"
        self._voices = voices
        # Capability, not "has spoken yet": /api/esp/status asks this before any turn has happened,
        # and answering False there would be as wrong as the True it used to answer for a provider
        # that only ever emitted zeros. A synthesis failure below still flips it back to False.
        self.produces_audio = bool(voices)
        return voices

    @property
    def diagnostic(self) -> str:
        """Why there is no audio, for ``/api/esp/status``."""

        self._installed()
        return self._reason

    def capability(self) -> bool:
        """Resolve and report whether speech is really available.

        ``produces_audio`` is an instance fact that stays ``False`` until the SAPI probe has run, so
        reading the attribute from the status endpoint answers "not yet", not "no" -- measured
        2026-10-06: two restarts of the same checkout reported ``produces_audio: False`` and ``True``
        with identical configuration, purely because a turn had happened in between.  The endpoint
        asks this instead.
        """

        self._installed()
        return self.produces_audio

    def _pick_voice(self, text: str, voice: str) -> str:
        """Explicit name / language env var / best match for the text, in that order."""

        if voice:
            return voice
        if self.voice:
            return self.voice
        japanese_or_chinese = bool(_CJK.search(text))
        wanted = os.environ.get("IOT_TTS_VOICE_ZH" if japanese_or_chinese else "IOT_TTS_VOICE_EN", "").strip()
        voices = self._installed()
        if wanted:
            for name, _culture in voices:
                if wanted.lower() in name.lower():
                    return name
        prefix = "zh" if japanese_or_chinese else "en"
        for name, culture in voices:
            if str(culture).lower().startswith(prefix):
                return name
        return ""

    # -- synthesis ------------------------------------------------------------------

    def synthesize(self, text: str, voice: str = "") -> SynthesizedAudio:
        phrase = (text or "").strip()
        if not phrase:
            raise ValueError("text_required")
        if not self._installed():
            return self._silence(phrase, voice or self.voice)
        try:
            artifact = self._speak(phrase, self._pick_voice(phrase, voice))
        except Exception as exc:  # noqa: BLE001 - the turn must still be answered
            self._reason = f"synthesis_failed:{type(exc).__name__}"
            self.produces_audio = False
            return self._silence(phrase, voice or self.voice)
        self._reason = ""
        self.produces_audio = True
        return artifact

    def _speak(self, phrase: str, voice_name: str) -> SynthesizedAudio:
        with tempfile.TemporaryDirectory(prefix="iot-sapi-") as folder:
            text_path = os.path.join(folder, "text.txt")
            wav_path = os.path.join(folder, "speech.wav")
            script_path = os.path.join(folder, "speak.ps1")
            with open(text_path, "w", encoding="utf-8") as handle:
                handle.write(phrase)
            with open(script_path, "w", encoding="utf-8") as handle:
                handle.write(_SYNTH)
            result = _run_script(script_path, text_path, wav_path, voice_name)
            if result.returncode != 0 or not os.path.isfile(wav_path):
                message = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip()
                raise RuntimeError(message[-200:] or f"exit_{result.returncode}")
            with wave.open(wav_path, "rb") as handle:
                rate = handle.getframerate()
                channels = handle.getnchannels()
                pcm16 = handle.readframes(handle.getnframes())
        if channels != 1:  # SAPI is mono for the installed desktop voices; refuse to guess
            raise RuntimeError(f"unexpected_channel_count:{channels}")
        duration_ms = round(len(pcm16) / 2 / max(1, rate) * 1000)
        return SynthesizedAudio(pcm16, rate, 1, duration_ms, f"sapi:{voice_name or 'default'}")

    def _silence(self, phrase: str, voice: str) -> SynthesizedAudio:
        return DeterministicTts().synthesize(phrase, voice)
