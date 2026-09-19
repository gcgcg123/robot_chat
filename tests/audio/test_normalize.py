import io
import wave

import pytest

from services.audio.normalize import NormalizedAudio, normalize_audio


def _wav(samples: list[int], rate: int = 8000, channels: int = 1) -> bytes:
    payload = b"".join(int(sample).to_bytes(2, "little", signed=True) for sample in samples)
    stream = io.BytesIO()
    with wave.open(stream, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(payload)
    return stream.getvalue()


def test_normalize_wav_resamples_to_mono_16khz():
    audio = normalize_audio(_wav([1000, -1000] * 4000, rate=8000), "audio/wav")

    assert isinstance(audio, NormalizedAudio)
    assert audio.sample_rate == 16000
    assert audio.duration_ms == 1000
    assert len(audio.pcm16) == 32000


def test_normalize_rejects_unknown_mime_without_using_filename():
    with pytest.raises(ValueError, match="unsupported_audio_format"):
        normalize_audio(b"RIFF-not-a-real-file", "application/octet-stream")


def test_normalize_rejects_empty_audio():
    with pytest.raises(ValueError, match="empty_audio"):
        normalize_audio(b"", "audio/wav")

