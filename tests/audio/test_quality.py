from services.audio.normalize import NormalizedAudio
from services.audio.quality import check_quality


def test_quality_rejects_silence_with_measured_metrics():
    audio = NormalizedAudio(pcm16=b"\x00\x00" * 16000, sample_rate=16000, duration_ms=1000)

    result = check_quality(audio, enrollment=False)

    assert result["accepted"] is False
    assert result["reason"] == "no_speech"
    assert result["speech_duration_ms"] == 0
    assert result["rms"] == 0


def test_quality_rejects_clipped_audio():
    audio = NormalizedAudio(pcm16=(32767).to_bytes(2, "little", signed=True) * 16000, sample_rate=16000, duration_ms=1000)

    result = check_quality(audio, enrollment=False)

    assert result["accepted"] is False
    assert result["reason"] == "clipping"
    assert result["clipping_ratio"] == 1.0

