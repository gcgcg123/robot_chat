"""Server-side endpoint detection for the ESP audio stream.

The ESP streams continuously once it is listening, so the *server* has to decide
when an utterance ended -- there is no browser "stop recording" button to lean
on.  ``EnergyVad`` does that with a sliding-minimum noise estimate: over the last
few seconds the quietest frame is almost certainly background, so
``gate = clamp(min(recent rms) * ratio, threshold, threshold * cap)``.

Both bounds matter.  The lower bound keeps a quiet room from being gated out at
all; the upper bound means a long uninterrupted sentence can never raise the
gate above its own level, so speech cannot switch itself off (only background
louder than ``3x`` the configured threshold would, and that is reported as a
misconfiguration rather than hidden).

Why not a neural VAD: the project has no VAD weights on disk, and shipping an
untested model path would be worse than an honest energy detector.  The
interface below is the seam -- ``create_vad`` is the only place that needs to
change when silero-vad ONNX weights are added (see ``docs/ESP32_ESP_INTEGRATION_PLAN.md``).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from services.device_gateway.xiaozhi import pcm

#: How far back the noise estimate looks. 3 s covers the pause between two
#: sentences while still tracking a changing room.
NOISE_WINDOW_MS = 3000


@dataclass(frozen=True)
class VadDecision:
    """Outcome of feeding one audio frame."""

    speaking: bool
    triggered: bool
    ended: bool
    reason: str
    rms: float
    voiced_ms: int


class EnergyVad:
    """Adaptive-RMS endpoint detector.

    ``rms_threshold`` is the floor for the gate; ``noise_floor * noise_ratio``
    can raise it, but never above ``rms_threshold * max_gate_multiple``.
    """

    def __init__(
        self,
        *,
        frame_duration_ms: int = 60,
        silence_ms: int = 800,
        min_speech_ms: int = 240,
        max_utterance_ms: int = 20000,
        rms_threshold: float = 380.0,
        noise_ratio: float = 2.5,
        max_gate_multiple: float = 3.0,
    ):
        self.frame_duration_ms = frame_duration_ms
        self.silence_ms = silence_ms
        self.min_speech_ms = min_speech_ms
        self.max_utterance_ms = max_utterance_ms
        self.rms_threshold = rms_threshold
        self.noise_ratio = noise_ratio
        self.max_gate_multiple = max_gate_multiple
        self._window_size = max(1, NOISE_WINDOW_MS // max(1, frame_duration_ms))
        self.reset()

    def reset(self) -> None:
        self._restart_utterance()
        self._window: deque[float] = deque(maxlen=self._window_size)

    def _restart_utterance(self) -> None:
        """Start a fresh utterance window, keeping the room's noise estimate.

        ``reset`` also forgets the noise floor, which is right for a new session and wrong here: the
        estimate is what keeps a loud room from re-triggering on its own noise.
        """

        self.speaking = False
        self._voiced_ms = 0
        self._silence_ms = 0
        self._utterance_ms = 0
        self._had_speech = False

    @property
    def noise_floor(self) -> float:
        return min(self._window) if self._window else 0.0

    @property
    def gate(self) -> float:
        adaptive = self.noise_floor * self.noise_ratio
        return min(max(self.rms_threshold, adaptive), self.rms_threshold * self.max_gate_multiple)

    def feed(self, frame: bytes) -> VadDecision:
        level = pcm.rms(frame)
        self._window.append(level)
        self._utterance_ms += self.frame_duration_ms
        loud = level >= self.gate
        triggered = False

        if loud:
            self._voiced_ms += self.frame_duration_ms
            self._silence_ms = 0
            if not self.speaking and self._voiced_ms >= self.min_speech_ms:
                self.speaking = True
                self._had_speech = True
                triggered = True
        else:
            self._silence_ms += self.frame_duration_ms

        if self.speaking and self._silence_ms >= self.silence_ms:
            return VadDecision(True, triggered, True, "silence", level, self._voiced_ms)
        if self._utterance_ms >= self.max_utterance_ms:
            if self.speaking or self._had_speech:
                # Cut a monologue rather than buffering without bound.
                return VadDecision(self.speaking, triggered, True, "max_length", level, self._voiced_ms)
            # Twenty seconds of listening with nothing above the gate is **not an utterance**.
            # Ending it here is what turned a silent room into a turn: measured 2026-10-06, four
            # "utterances" of exactly 20040 ms with rms 10-35 and a "." transcript, each one sent to
            # ASR, scored against the speaker's voiceprint and logged as ``unknown`` -- which is
            # indistinguishable from "the voiceprint does not recognise me", and is what made a
            # *successful* enrollment look like a failed one.
            self._restart_utterance()
            return VadDecision(False, False, False, "", level, 0)
        return VadDecision(self.speaking, triggered, False, "", level, self._voiced_ms)

    def flush(self) -> VadDecision:
        """Force the current utterance to end (``listen stop`` / explicit cut)."""
        decision = VadDecision(self.speaking, False, self._had_speech, "flush", self.gate, self._voiced_ms)
        return decision


def create_vad(settings) -> tuple[object, dict]:
    """Build the configured VAD and describe what was actually built.

    Returns ``(vad, status)``; the status dict is surfaced on ``/api/esp/status``
    so the dashboard never has to guess which detector is running.
    """
    provider = getattr(settings, "vad_provider", "energy") or "energy"
    if provider == "energy":
        vad = EnergyVad(
            frame_duration_ms=settings.frame_duration_ms,
            silence_ms=settings.vad_silence_ms,
            min_speech_ms=settings.vad_min_speech_ms,
            max_utterance_ms=settings.vad_max_utterance_ms,
            rms_threshold=settings.vad_rms_threshold,
        )
        return vad, {"provider": "energy", "requested": provider, "degraded": False, "reason": ""}
    return EnergyVad(
        frame_duration_ms=settings.frame_duration_ms,
        silence_ms=settings.vad_silence_ms,
        min_speech_ms=settings.vad_min_speech_ms,
        max_utterance_ms=settings.vad_max_utterance_ms,
        rms_threshold=settings.vad_rms_threshold,
    ), {
        "provider": "energy",
        "requested": provider,
        "degraded": True,
        "reason": f"vad_provider_not_implemented:{provider}; falling back to energy",
    }
