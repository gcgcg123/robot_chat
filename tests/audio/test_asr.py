from dataclasses import dataclass

import pytest

from services.audio.asr import AsrService, _decode_options, probe_cuda_runtime, select_asr_runtime


@dataclass
class Segment:
    text: str


@dataclass
class Info:
    language: str = "zh"
    language_probability: float = 0.95


class FakeModel:
    def __init__(self, text="你好"):
        self.text = text
        self.calls = 0

    def transcribe(self, _audio, language=None, **_kwargs):
        self.calls += 1
        return iter([Segment(self.text)]), Info(language or "zh")


def test_select_asr_runtime_is_explicit():
    assert select_asr_runtime(False) == ("cpu", "int8")
    assert select_asr_runtime(True) == ("cuda", "float16")


def test_service_reuses_model_between_transcriptions():
    loads = []

    def factory(path, device, compute_type):
        loads.append((path, device, compute_type))
        return FakeModel()

    service = AsrService("model", factory, device="cpu")
    audio = object()

    first = service.transcribe(audio, "zh")
    second = service.transcribe(audio, "zh")

    assert first["text"] == second["text"] == "你好"
    assert first["status"] == second["status"] == "ready"
    assert len(loads) == 1
    assert loads[0] == ("model", "cpu", "int8")


def test_cuda_failure_retries_once_on_cpu_int8():
    loads = []

    def factory(_path, device, compute_type):
        loads.append((device, compute_type))
        if device == "cuda":
            raise RuntimeError("CUDA out of memory")
        return FakeModel()

    service = AsrService("model", factory, device="cuda")
    result = service.transcribe(object(), None)

    assert result["status"] == "degraded"
    assert result["device"] == "cpu"
    assert result["compute_type"] == "int8"
    assert loads == [("cuda", "float16"), ("cpu", "int8")]


def test_non_runtime_failure_does_not_fallback_to_cpu():
    loads = []

    def factory(_path, device, compute_type):
        loads.append((device, compute_type))
        raise FileNotFoundError("model.bin missing")

    service = AsrService("model", factory, device="cuda")
    with pytest.raises(FileNotFoundError):
        service.transcribe(object(), None)

    assert loads == [("cuda", "float16")]


def test_probe_cuda_runtime_reports_missing_device():
    assert probe_cuda_runtime(0) == (False, "no_cuda_device")


def test_decode_options_default_to_low_latency(monkeypatch):
    for name in (
        "ASR_BEAM_SIZE",
        "ASR_TEMPERATURE",
        "ASR_CONDITION_ON_PREVIOUS_TEXT",
        "ASR_WITHOUT_TIMESTAMPS",
        "ASR_VAD_FILTER",
    ):
        monkeypatch.delenv(name, raising=False)

    options = _decode_options()

    assert options["beam_size"] == 1
    assert options["temperature"] == 0.0
    assert options["condition_on_previous_text"] is False
    assert options["without_timestamps"] is True
    assert options["vad_filter"] is True


def test_decode_options_honour_environment_overrides(monkeypatch):
    monkeypatch.setenv("ASR_BEAM_SIZE", "5")
    monkeypatch.setenv("ASR_TEMPERATURE", "0.4")
    monkeypatch.setenv("ASR_VAD_FILTER", "0")

    options = _decode_options()

    assert options["beam_size"] == 5
    assert options["temperature"] == 0.4
    assert options["vad_filter"] is False


def test_decode_options_ignore_malformed_environment_values(monkeypatch):
    monkeypatch.setenv("ASR_BEAM_SIZE", "many")
    monkeypatch.setenv("ASR_TEMPERATURE", "warm")

    options = _decode_options()

    assert options["beam_size"] == 1
    assert options["temperature"] == 0.0


def test_snapshot_reports_the_gpu_decision(monkeypatch):
    monkeypatch.setenv("ASR_DEVICE", "cpu")
    service = AsrService("model", lambda *_args: FakeModel(), device="cpu")

    snapshot = service.snapshot()

    assert snapshot["requested_device"] == "cpu"
    assert snapshot["cuda_available"] is False
    assert snapshot["cuda_device_count"] == 0
    assert snapshot["runtime_reason"] == "forced_by_config"
    assert snapshot["decode_options"]["beam_size"] == 1


def test_transcribe_forwards_the_tuned_decode_options():
    seen = {}

    class RecordingModel:
        def transcribe(self, _audio, language=None, **kwargs):
            seen.update(kwargs)
            return iter([Segment("hi")]), Info()

    service = AsrService("model", lambda *_args: RecordingModel(), device="cpu")
    service.transcribe(object(), "zh")

    assert seen["beam_size"] == 1
    assert seen["temperature"] == 0.0
    assert seen["condition_on_previous_text"] is False
    assert seen["without_timestamps"] is True
    assert seen["vad_filter"] is True

