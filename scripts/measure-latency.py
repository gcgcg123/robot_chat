"""Where does a turn's latency actually go? Measure it, do not guess.

The felt latency of one voice turn is

    VAD end-of-speech -> ASR -> LLM first sentence -> TTS first chunk -> ffmpeg

and only two of those are under this project's control.  This script prints
every term against the *real* endpoint and the *real* models, so "it feels slow"
can be answered with numbers instead of opinion, and so a change that claims to
help can be checked.

    python scripts/measure-latency.py            # everything available
    python scripts/measure-latency.py --llm      # one section

Notes:
* The DeepSeek key is read the way the launcher reads it (hex-encoded DPAPI blob
  written by ``setup-project.ps1``) and is never printed.
* The ASR section needs ``sherpa_onnx``, which lives in the interpreter the
  service runs on (Anaconda), not in the test venv -- it is skipped when absent.
* The tool names are put through the project's own sanitiser: the firmware's
  dotted paths (``self.audio_speaker.set_volume``) are rejected by the model API
  with HTTP 400, which is exactly how tool calling can look implemented and
  never work.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import os
import subprocess
import sys
import tempfile
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

QUESTION = "珠海的天气怎么样？"
DEVICE_TOOLS = (
    "self.get_device_status",
    "self.audio_speaker.set_volume",
    "self.screen.set_theme",
    "self.led_strip.set_brightness",
)


# --------------------------------------------------------------- the API key


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.c_void_p)]


def _unprotect(blob: bytes) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    # Explicit argtypes: the default c_int truncates the 64-bit pointer DPAPI
    # returns, and LocalFree then refuses the freed address.
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(blob, len(blob))
    source = _Blob(len(blob), ctypes.cast(buffer, ctypes.c_void_p))
    target = _Blob()
    if not crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)):
        raise OSError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(ctypes.c_void_p(target.pbData))


def load_api_key() -> bool:
    """Mirror ``start-project.ps1``; report whether a key is available."""
    if os.getenv("DEEPSEEK_API_KEY"):
        return True
    for candidate in (ROOT / "data" / "secrets" / "deepseek.key", ROOT / "IoTGroup5" / "secrets" / "deepseek.key"):
        if not candidate.is_file():
            continue
        raw = candidate.read_text(encoding="utf-8").strip()
        try:
            plain = _unprotect(bytes.fromhex(raw)).decode("utf-16-le", errors="ignore").strip()
        except (ValueError, OSError):
            continue
        if plain:
            os.environ["DEEPSEEK_API_KEY"] = plain
            print(f"[key] 已解密 {candidate.name}（{len(plain)} 字符，不显示内容）")
            return True
    return False


# ------------------------------------------------------------------- sections


def section_llm(use_tools: bool) -> None:
    """Time to the first *sentence*, not to the whole completion.

    That is the number the device feels: speech can start as soon as one
    sentence exists, and waiting for the rest is the difference between
    streaming and not streaming.
    """
    from services.device_gateway.xiaozhi.mcp import DeviceTool, DeviceToolset
    from services.dialogue.deepseek import DeepSeekClient
    from services.dialogue.pipeline import process_text
    from services.tts.segments import SentenceStreamer

    toolset = DeviceToolset()
    for name in DEVICE_TOOLS:
        toolset.add(DeviceTool(name=name, description=f"设备的 {name} 能力",
                               input_schema={"type": "object", "properties": {"volume": {"type": "integer"}}}))
    tools = toolset.llm_tools() if use_tools else None

    label = "有工具（真机的常态）" if use_tools else "无工具（纯聊天）"
    marks: dict[str, float] = {}
    streamer = SentenceStreamer(max_chars=48, min_chars=6)
    t0 = time.perf_counter()

    def on_delta(delta: str) -> None:
        marks.setdefault("first_delta", time.perf_counter() - t0)
        for _sentence in streamer.feed(delta):
            marks.setdefault("first_sentence", time.perf_counter() - t0)

    result = process_text(
        QUESTION, user_id="measure", identity=None, session_id="measure",
        llm=DeepSeekClient(), tools=tools,
        tool_runner=(lambda name, args: "{}") if tools else None,
        on_delta=on_delta,
    )
    total = (time.perf_counter() - t0) * 1000
    model = result.model
    print(f"  {label}")
    if tools:
        sent = ", ".join(t["function"]["name"] for t in tools[:3])
        print(f"    发给模型的名字（已 sanitize）: {sent} …")
    else:
        print("    未提供工具")
    print(f"    status={model.get('status')} streamed={model.get('streamed')} 整轮={total:7.0f} ms")
    first_delta = marks.get("first_delta")
    first_sentence = marks.get("first_sentence")
    print(f"    首个 delta : {first_delta*1000:7.0f} ms" if first_delta else "    首个 delta :       —")
    print(f"    首句可合成 : {first_sentence*1000:7.0f} ms  ← 此刻就能开始合成" if first_sentence else "    首句可合成 :       —")
    print()


def section_tts() -> None:
    """Split one sentence's cost into edge-tts and ffmpeg."""
    try:
        import edge_tts
    except ImportError:
        print("  edge-tts 未安装，跳过\n")
        return
    from services.tts.edge import resolve_ffmpeg

    loop = asyncio.new_event_loop()
    threading.Thread(target=lambda: (asyncio.set_event_loop(loop), loop.run_forever()), daemon=True).start()

    for sentence in ("好的，音量已经调小了一点。", "我收到你的訊息，先陪你慢慢整理一下感受，不用急。"):
        async def collect(text=sentence):
            t0 = time.perf_counter()
            communicate = edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural")
            first, buffer = 0.0, bytearray()
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    first = first or (time.perf_counter() - t0)
                    buffer.extend(chunk["data"])
            return first, time.perf_counter() - t0, bytes(buffer)

        first, total, mp3 = asyncio.run_coroutine_threadsafe(collect(), loop).result(timeout=40)
        with tempfile.TemporaryDirectory(prefix="lat-") as workdir:
            source = Path(workdir) / "s.mp3"
            source.write_bytes(mp3)
            t0 = time.perf_counter()
            subprocess.run(
                [resolve_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                 "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", "24000", os.devnull],
                capture_output=True, check=False,
            )
            decode = time.perf_counter() - t0
        print(f"    {len(sentence):2d} 字：edge-tts 首块 {first*1000:6.0f} ms | 收齐 {total*1000:6.0f} ms | ffmpeg {decode*1000:5.0f} ms")
    print(f"    → 现状「收齐再解码」= 收齐 + ffmpeg；若边收边喂 ffmpeg，首块即可解码")
    print()


def _render_wav(text: str, target: Path) -> bool:
    """Render ``text`` to a 16 kHz mono WAV through the project's TTS stack."""
    try:
        import edge_tts
    except ImportError:
        return False
    from services.tts.edge import resolve_ffmpeg

    async def collect() -> bytes:
        communicate = edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural")
        buffer = bytearray()
        async for chunk in communicate.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                buffer.extend(chunk["data"])
        return bytes(buffer)

    loop = asyncio.new_event_loop()
    try:
        mp3 = loop.run_until_complete(collect())
    finally:
        loop.close()
    if not mp3:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lat-") as workdir:
        source = Path(workdir) / "s.mp3"
        source.write_bytes(mp3)
        completed = subprocess.run(
            [resolve_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
             "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", str(target)],
            capture_output=True, check=False,
        )
    return completed.returncode == 0 and target.is_file()


def section_asr() -> None:
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        print("  sherpa_onnx 不在当前解释器（服务用 Anaconda 跑），跳过")
        print("  用服务的解释器运行即可：<anaconda>/python.exe scripts/measure-latency.py --asr\n")
        return
    from services.audio.factory import create_asr_service, describe_asr_backend
    from services.audio.normalize import normalize_audio

    os.environ.setdefault("ASR_PROVIDER", "sensevoice")
    os.environ.setdefault("ASR_SENSEVOICE_MODEL_PATH", "models/asr/sensevoice-small")
    print(f"  后端: {describe_asr_backend()}")
    service = create_asr_service()

    wav = ROOT / "runtime" / "asr-sample.wav"
    if not wav.is_file():
        print("  runtime/asr-sample.wav 不存在，用 edge-tts 现造一个…")
        if not _render_wav("帮我把音量调小一点", wav):
            print("  造样本失败（需要 edge-tts + ffmpeg），跳过\n")
            return
    payload = normalize_audio(wav.read_bytes(), "audio/wav")
    for index in range(3):
        t0 = time.perf_counter()
        result = service.transcribe(payload, "zh-CN")
        elapsed = (time.perf_counter() - t0) * 1000
        note = "（含模型加载，只在启动时发生一次）" if index == 0 else ""
        print(f"    {payload.duration_ms:5d} ms 音频 → {elapsed:6.0f} ms  {result['text']!r} {note}")
    print()


SECTIONS = {"llm": section_llm, "tts": section_tts, "asr": section_asr}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm", action="store_true", help="只测 LLM 首句延迟")
    parser.add_argument("--tts", action="store_true", help="只测 TTS 拆分")
    parser.add_argument("--asr", action="store_true", help="只测 ASR")
    args = parser.parse_args()
    selected = [name for name in SECTIONS if getattr(args, name)] or list(SECTIONS)

    if load_api_key() is False and "llm" in selected:
        print("[key] 未找到 DeepSeek 密钥，LLM 段会以 unavailable 结束\n")

    if "llm" in selected:
        print("=" * 74)
        print(f"LLM（问题：{QUESTION}）—— 关心的是「首句何时可合成」")
        print("=" * 74)
        section_llm(use_tools=True)
        section_llm(use_tools=False)
    if "tts" in selected:
        print("=" * 74)
        print("TTS 单句成本拆分")
        print("=" * 74)
        section_tts()
    if "asr" in selected:
        print("=" * 74)
        print("ASR（本地 SenseVoice）")
        print("=" * 74)
        section_asr()

    print("=" * 74)
    print("VAD 结束静音判定不在本脚本内：看 IOT_VAD_SILENCE_MS（默认 800 ms），")
    print("它就等于「用户闭嘴后还要等多久才送去识别」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
