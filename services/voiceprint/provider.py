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
        samples = [value / 32768.0 for (value,) in struct.iter_unpack("<h", usable)]
        waveform = torch.tensor(samples, dtype=torch.float32).unsqueeze(0)
        with torch.inference_mode():
            embedding = self._load().encode_batch(waveform).squeeze().detach().cpu().tolist()
        if not isinstance(embedding, list) or not embedding:
            raise ValueError("empty_embedding")
        values = [float(value) for value in embedding]
        norm = math.sqrt(sum(value * value for value in values))
        if norm <= 1e-12:
            raise ValueError("empty_embedding")
        return [value / norm for value in values]


def create_voiceprint_provider(testing: bool = False):
    provider = os.getenv("VOICEPRINT_PROVIDER", "").strip().lower()
    if testing or provider == "baseline":
        return VoiceprintProvider()
    return EcapaVoiceprintProvider()
