from dataclasses import dataclass
from pathlib import Path

import pytest

from services.audio.factory import create_asr_service, describe_asr_backend, resolve_model_path
from services.audio.sensevoice import (
    SenseVoiceAsrService,
    normalize_language,
    resolve_model_files,
    strip_sensevoice_tags,
)


# --------------------------------------------------------------------------- #
# helpers / pure functions
# --------------------------------------------------------------------------- #

def test_strip_sensevoice_tags_removes_all_markers():
    raw = "<|zh|><|NEUTRAL|><|Speech|><|withitn|>呢几个字都表达唔到我想讲嘅意思。"
    assert strip_sensevoice_tags(raw) == "呢几个字都表达唔到我想讲嘅意思。"
    assert strip_sensevoice_tags("<|HAPPY|>") == ""
    assert strip_sensevoice_tags("") == ""


def test_normalize_language_accepts_known_codes_and_falls_back_to_auto():
    assert normalize_language("yue") == "yue"
    assert normalize_language("ZH") == "zh"
    assert normalize_language("en") == "en"
    # an unknown or missing hint must not force a wrong language
    assert normalize_language("zh-CN") == ""
    assert normalize_language(None) == ""
    assert normalize_language("   ") == ""


def test_resolve_model_files_prefers_int8_and_reports_quantisation(tmp_path):
    (tmp_path / "model.onnx").write_bytes(b"fp32")
    (tmp_path / "model.int8.onnx").write_bytes(b"int8")
    (tmp_path / "tokens.txt").write_text("a 1\n", encoding="utf-8")

    graph, tokens, quantisation = resolve_model_files(tmp_path)

    assert graph.name == "model.int8.onnx"
    assert tokens.name == "tokens.txt"
    assert quantisation == "int8"


def test_resolve_model_files_accepts_a_direct_onnx_path(tmp_path):
    graph = tmp_path / "model.onnx"
    graph.write_bytes(b"fp32")
    (tmp_path / "tokens.txt").write_text("a 1\n", encoding="utf-8")

    resolved, _tokens, quantisation = resolve_model_files(graph)

    assert resolved == graph
    assert quantisation == "float32"


def test_resolve_model_files_rejects_incomplete_directories(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_model_files(tmp_path)


# --------------------------------------------------------------------------- #
# provider contract (fake recogniser, no sherpa-onnx needed)
# --------------------------------------------------------------------------- #

@dataclass
class _Result:
    text: str
    lang: str | None = None


class _Stream:
    def __init__(self, result):
        self.result = result
        self.rate = None
        self.samples = None

    def accept_waveform(self, rate, samples):
        self.rate, self.samples = rate, samples


class _Recognizer:
    def __init__(self, text="<|yue|><|Speech|>你好"):
        self.text = text
        self.streams = []

    def create_stream(self):
        stream = _Stream(_Result(self.text, "yue"))
        self.streams.append(stream)
        return stream

    def decode_stream(self, _stream):
        return None


def _service(tmp_path, recognizer=None, **kwargs):
    (tmp_path / "model.int8.onnx").write_bytes(b"x")
    (tmp_path / "tokens.txt").write_text("a 1\n", encoding="utf-8")
    seen = []

    def factory(graph, tokens, provider):
        seen.append((Path(graph).name, Path(tokens).name, provider))
        return recognizer if recognizer is not None else _Recognizer()

    service = SenseVoiceAsrService(tmp_path, device="cpu", recognizer_factory=factory, **kwargs)
    return service, seen


class _Audio:
    pcm16 = b"\x00\x01" * 800


def test_transcribe_matches_the_asr_service_contract(tmp_path):
    service, seen = _service(tmp_path)

    out = service.transcribe(_Audio(), "yue")

    for key in ("text", "language", "language_probability", "model_path", "status", "device", "compute_type", "latency_ms"):
        assert key in out, key
    assert out["text"] == "你好"            # tags stripped
    assert out["status"] == "ready"
    assert out["device"] == "cpu"
    assert out["compute_type"] == "int8"
    assert out["backend"] == "sensevoice"
    assert isinstance(out["latency_ms"], int)
    assert seen == [("model.int8.onnx", "tokens.txt", "cpu")]


def test_model_is_built_once_and_reused(tmp_path):
    recognizer = _Recognizer()
    service, seen = _service(tmp_path, recognizer=recognizer)

    service.transcribe(_Audio(), None)
    service.transcribe(_Audio(), None)

    assert len(seen) == 1
    assert len(recognizer.streams) == 2
    assert recognizer.streams[0].rate == 16000


def test_snapshot_exposes_the_gpu_decision(tmp_path):
    service, _seen = _service(tmp_path, num_threads=3)

    snapshot = service.snapshot()

    assert snapshot["backend"] == "sensevoice"
    assert snapshot["requested_device"] == "cpu"
    assert snapshot["cuda_available"] is False
    assert snapshot["cuda_device_count"] == 0
    assert snapshot["runtime_reason"] == "forced_by_config"
    assert snapshot["decode_options"] == {"num_threads": 3, "use_itn": True, "language": "auto"}
    assert snapshot["loaded"] is False


def test_cuda_failure_degrades_to_cpu(tmp_path):
    (tmp_path / "model.int8.onnx").write_bytes(b"x")
    (tmp_path / "tokens.txt").write_text("a 1\n", encoding="utf-8")
    providers = []

    def factory(_graph, _tokens, provider):
        providers.append(provider)
        if provider == "cuda":
            raise RuntimeError("cudnn not available")
        return _Recognizer()

    service = SenseVoiceAsrService(tmp_path, device="cuda", recognizer_factory=factory)
    out = service.transcribe(_Audio(), None)

    assert providers == ["cuda", "cpu"]
    assert out["status"] == "degraded"
    assert out["device"] == "cpu"


# --------------------------------------------------------------------------- #
# backend selection
# --------------------------------------------------------------------------- #

def test_factory_defaults_to_faster_whisper():
    service = create_asr_service(env={})

    assert type(service).__name__ == "AsrService"
    assert service.model_path.endswith("whisper-large-v3-turbo-ct2")


@pytest.mark.parametrize("value", ["sensevoice", "SenseVoice", "sense_voice", "sense-voice"])
def test_factory_selects_sensevoice_for_every_alias(value):
    service = create_asr_service(env={"ASR_PROVIDER": value})

    assert isinstance(service, SenseVoiceAsrService)
    assert service.model_path.endswith("sensevoice-small")


def test_factory_honours_sensevoice_model_path_and_flags(tmp_path):
    service = create_asr_service(env={
        "ASR_PROVIDER": "sensevoice",
        "ASR_SENSEVOICE_MODEL_PATH": str(tmp_path),
        "ASR_CPU_THREADS": "4",
        "ASR_USE_ITN": "0",
        "ASR_DEVICE": "cpu",
    })
    snapshot = service.snapshot()

    assert service.model_path == str(tmp_path)
    assert snapshot["decode_options"]["num_threads"] == 4
    assert snapshot["decode_options"]["use_itn"] is False


def test_factory_ignores_malformed_thread_count():
    service = create_asr_service(env={"ASR_PROVIDER": "sensevoice", "ASR_CPU_THREADS": "lots"})

    assert service.snapshot()["decode_options"]["num_threads"] >= 1


def test_resolve_model_path_is_absolute_and_anchored_at_the_project_root():
    resolved = resolve_model_path("models/asr/sensevoice-small")

    assert Path(resolved).is_absolute()
    assert resolved.endswith("models\\asr\\sensevoice-small") or resolved.endswith("models/asr/sensevoice-small")


def test_describe_asr_backend_reports_the_configured_backend_without_building():
    assert describe_asr_backend(env={}) == {
        "backend": "whisper",
        "model_path": "models/asr/whisper-large-v3-turbo-ct2",
    }
    assert describe_asr_backend(env={"ASR_PROVIDER": "sensevoice"}) == {
        "backend": "sensevoice",
        "model_path": "models/asr/sensevoice-small",
    }
    described = describe_asr_backend(env={"ASR_PROVIDER": "sensevoice", "ASR_SENSEVOICE_MODEL_PATH": "./custom/sv"})
    assert described["model_path"] == "./custom/sv"
