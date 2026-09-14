from __future__ import annotations

class VoiceprintProvider:
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
