from dataclasses import dataclass

import pytest

from services.audio.asr import AsrService, select_asr_runtime


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

