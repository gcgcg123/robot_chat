from __future__ import annotations

import os
import struct
import math


class VoiceprintProvider:
    model_version = "pc-baseline-v2"

    def embed(self, pcm16: bytes) -> list[float]:
        if not pcm16: raise ValueError("empty_audio")
        # CPU-safe deterministic baseline for the PC demo.  A 3D-Speaker
        # provider can be selected later without changing the API contract.
        import struct, math
        values = [x[0] / 32768.0 for x in struct.iter_unpack("<h", pcm16[: len(pcm16) - len(pcm16) % 2])]
        if not values: raise ValueError("empty_audio")
        mean = sum(values) / len(values)
        rms = math.sqrt(sum(x * x for x in values) / len(values))
        zcr = sum(1 for a, b in zip(values, values[1:]) if (a < 0) != (b < 0)) / max(1, len(values) - 1)
        return [mean, rms, zcr]


class EcapaVoiceprintProvider:
    """ECAPA-TDNN speaker embeddings with lazy model loading.

    The model is deliberately loaded only when enrollment or identification is
    used. This keeps health checks and text-only operation fast.
    """

    model_version = "ecapa-voxceleb-v1"

    def __init__(self, model_source: str | None = None, model_dir: str | None = None):
        self.model_source = model_source or os.getenv(
            "VOICEPRINT_MODEL_SOURCE", "speechbrain/spkrec-ecapa-voxceleb"
        )
        self.model_dir = model_dir or os.getenv(
            "VOICEPRINT_MODEL_DIR", "models/voiceprint/ecapa-voxceleb"
        )
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from speechbrain.inference.speaker import EncoderClassifier
            from speechbrain.utils.fetching import LocalStrategy
        except ImportError as exc:
            raise RuntimeError(
                "Real voiceprint requires speechbrain and torch. "
                "Install requirements.txt or set VOICEPRINT_PROVIDER=baseline for tests."
            ) from exc
        self._model = EncoderClassifier.from_hparams(
            source=self.model_source,
            savedir=self.model_dir,
            run_opts={"device": os.getenv("VOICEPRINT_DEVICE", "cpu")},
            local_strategy=LocalStrategy.COPY,
        )
        return self._model

    @staticmethod
    def _speech_samples(samples: list[float], sample_rate: int = 16000) -> list[float]:
        frame_size = max(1, sample_rate // 50)
        frames = [
            samples[index:index + frame_size]
            for index in range(0, len(samples), frame_size)
            if len(samples[index:index + frame_size]) >= frame_size // 2
        ]
        if not frames:
            return []
        energy = [math.sqrt(sum(value * value for value in frame) / len(frame)) for frame in frames]
        peak = max(energy) or 1.0
        active = [value >= max(0.008, peak * 0.12) for value in energy]
        active_indexes = [index for index, is_active in enumerate(active) if is_active]
        if not active_indexes:
            return samples
        start = max(0, active_indexes[0] - 2)
        end = min(len(frames), active_indexes[-1] + 3)
        trimmed = [value for frame in frames[start:end] for value in frame]
        rms = math.sqrt(sum(value * value for value in trimmed) / len(trimmed)) or 1.0
        gain = min(4.0, 0.12 / rms)
        return [max(-1.0, min(1.0, value * gain)) for value in trimmed]

    def embed(self, pcm16: bytes) -> list[float]:
        if not pcm16:
            raise ValueError("empty_audio")
        usable = pcm16[: len(pcm16) - len(pcm16) % 2]
        if not usable:
            raise ValueError("empty_audio")
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("Real voiceprint requires torch.") from exc
        samples = self._speech_samples(
            [value / 32768.0 for (value,) in struct.iter_unpack("<h", usable)]
        )
        if not samples:
            raise ValueError("empty_audio")
        sample_rate = 16000
        chunk_size = sample_rate * 3
        step = sample_rate * 2
        starts = list(range(0, max(1, len(samples) - chunk_size + 1), step))
        if not starts or starts[-1] + sample_rate > len(samples):
            starts.append(max(0, len(samples) - chunk_size))
        vectors = []
        with torch.inference_mode():
            model = self._load()
            for start in dict.fromkeys(starts):
                chunk = samples[start:start + chunk_size]
                if len(chunk) < sample_rate:
                    continue
                waveform = torch.tensor(chunk, dtype=torch.float32).unsqueeze(0)
                vector = model.encode_batch(waveform).squeeze().detach().cpu().tolist()
                if isinstance(vector, list) and vector:
                    vectors.append([float(value) for value in vector])
        if not vectors:
            raise ValueError("empty_embedding")
        values = [sum(vector[index] for vector in vectors) / len(vectors) for index in range(len(vectors[0]))]
        norm = math.sqrt(sum(value * value for value in values))
        if norm <= 1e-12:
            raise ValueError("empty_embedding")
        return [value / norm for value in values]


def create_voiceprint_provider(testing: bool = False):
    provider = os.getenv("VOICEPRINT_PROVIDER", "").strip().lower()
    if testing or provider == "baseline":
        return VoiceprintProvider()
    return EcapaVoiceprintProvider()
