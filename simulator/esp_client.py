"""Fake ESP32 client for the xiaozhi protocol -- exercises the server without hardware.

Two ways to drive it:

1. Audio mode -- stream a WAV file as Opus frames, the way a real board does::

       python simulator/esp_client.py --wav tests/data/hello.wav --save-wav out.wav

2. Text mode -- skip ASR by sending ``listen`` with ``state=detect``::

       python simulator/esp_client.py --say "你好，今天有點累"

With ``--ota`` the client first asks the server where to connect, so it also
validates the onboarding path a freshly flashed board takes.  Pass
``--expect-audio`` to fail (non-zero exit) when the server returns no Opus
frames, which is how a silent TTS provider gets caught instead of looking like a
protocol problem.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.error
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.device_gateway.xiaozhi import pcm as pcm_tools  # noqa: E402
from services.device_gateway.xiaozhi.opus_codec import OpusDecoder, OpusEncoder, opus_status  # noqa: E402

UPLINK_RATE = 16000
DOWNLINK_RATE = 24000
FRAME_MS = 60


def build_hello(device_id: str, sample_rate: int = DOWNLINK_RATE) -> dict:
    return {
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "features": {"mcp": False, "aec": False},
        "audio_params": {"format": "opus", "sample_rate": sample_rate, "channels": 1, "frame_duration": FRAME_MS},
        "device": {"id": device_id},
    }


def fetch_ota(ota_url: str, device_id: str, board_model: str = "esp-client-sim", version: str = "0.0.0", timeout: float = 10.0) -> dict:
    request = urllib.request.Request(
        ota_url,
        data=b"{}",
        method="POST",
        headers={
            "device-id": device_id,
            "client-id": device_id,
            "device-model": board_model,
            "device-version": version,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        raise SystemExit(f"OTA 失敗 HTTP {exc.code}: {body}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"OTA 連線失敗: {exc}")


def read_wav_uplink(path: Path) -> bytes:
    """Read a WAV, convert to mono 16 kHz PCM16 (the firmware's uplink format)."""
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise SystemExit("只支援 16-bit WAV")
    if channels > 1:
        frames = pcm_tools.to_mono(frames, channels)
    if rate != UPLINK_RATE:
        frames = pcm_tools.resample(frames, rate, UPLINK_RATE)
    return frames


def write_wav(path: Path, payload: bytes, sample_rate: int = DOWNLINK_RATE) -> None:
    path.write_bytes(pcm_tools.to_wav(payload, sample_rate))


def _connect_kwargs(headers: dict) -> dict:
    """``websockets`` renamed extra_headers -> additional_headers in v14."""
    try:
        import websockets

        major = int(getattr(websockets, "__version__", "0").split(".")[0] or 0)
    except (ImportError, ValueError):
        major = 0
    if major >= 14:
        return {"additional_headers": headers}
    return {"extra_headers": headers}


async def run(args: argparse.Namespace) -> int:
    status = opus_status()
    if not status["available"]:
        print(f"[!] Opus 不可用：{status['reason']}")
        return 2

    websocket_url, token = args.ws, args.token
    if args.ota:
        payload = fetch_ota(args.ota, args.device_id)
        websocket_url = payload.get("websocket", {}).get("url") or websocket_url
        token = payload.get("websocket", {}).get("token") or token
        print(f"[OTA] websocket={websocket_url} token={'有' if token else '無'} firmware={payload.get('firmware', {}).get('version')}")
    if not websocket_url:
        print("[!] 需要 --ws 或 --ota 其中之一")
        return 2

    import websockets

    headers = {"device-id": args.device_id, "client-id": args.device_id}
    if token:
        headers["authorization"] = f"Bearer {token}"

    downlink = bytearray()
    messages: list[dict] = []
    done = asyncio.Event()

    async with websockets.connect(websocket_url, **_connect_kwargs(headers)) as socket:
        print(f"[WS] 已連線 {websocket_url}（device-id={args.device_id}）")
        await socket.send(json.dumps(build_hello(args.device_id), ensure_ascii=False))

        async def receiver() -> None:
            decoder = OpusDecoder(DOWNLINK_RATE, FRAME_MS)
            while True:
                try:
                    message = await socket.recv()
                except Exception:
                    done.set()
                    return
                if isinstance(message, (bytes, bytearray)):
                    downlink.extend(decoder.decode(bytes(message)))
                    continue
                try:
                    body = json.loads(message)
                except ValueError:
                    print(f"[recv] {message}")
                    continue
                messages.append(body)
                kind = body.get("type")
                if kind == "hello":
                    print(f"[握手] session_id={body.get('session_id')} audio_params={body.get('audio_params')}")
                elif kind == "stt":
                    print(f"[STT ] {body.get('text')}")
                elif kind == "llm":
                    print(f"[情緒] {body.get('emotion')}")
                elif kind == "tts":
                    state = body.get("state")
                    text = body.get("text") or ""
                    print(f"[TTS ] {state}{(' · ' + text) if text else ''}")
                    if state == "stop":
                        done.set()
                else:
                    print(f"[recv] {body}")

        task = asyncio.create_task(receiver())
        await asyncio.sleep(0.3)

        if args.wav:
            uplink = read_wav_uplink(Path(args.wav))
            packets = OpusEncoder(UPLINK_RATE, FRAME_MS).encode(uplink)
            print(f"[上行] {len(uplink)} bytes PCM16 -> {len(packets)} 個 Opus 包（{pcm_tools.duration_ms(uplink, UPLINK_RATE)} ms）")
            await socket.send(json.dumps({"type": "listen", "state": "start", "mode": "manual"}, ensure_ascii=False))
            for packet in packets:
                await socket.send(packet)
                await asyncio.sleep(FRAME_MS / 1000 * 0.25)
            await socket.send(json.dumps({"type": "listen", "state": "stop", "mode": "manual"}, ensure_ascii=False))

        if args.say:
            print(f"[上行] 文字模式：{args.say}")
            await socket.send(json.dumps({"type": "listen", "state": "detect", "mode": "auto", "text": args.say}, ensure_ascii=False))

        try:
            await asyncio.wait_for(done.wait(), timeout=args.timeout)
        except asyncio.TimeoutError:
            print(f"[!] {args.timeout}s 內沒有收到 tts stop")
        task.cancel()

    if args.save_pcm and downlink:
        Path(args.save_pcm).write_bytes(bytes(downlink))
        print(f"[下行] 已寫入 {args.save_pcm}（{len(downlink)} bytes）")
    if args.save_wav and downlink:
        write_wav(Path(args.save_wav), bytes(downlink))
        print(f"[下行] 已寫入 {args.save_wav}（{pcm_tools.duration_ms(bytes(downlink), DOWNLINK_RATE)} ms）")

    audio_frames = 1 if downlink else 0
    tts_states = [m.get("state") for m in messages if m.get("type") == "tts"]
    print(f"[摘要] 訊息 {len(messages)} 條，tts 狀態 {tts_states}，下行音訊 {'有' if audio_frames else '無'}")
    if args.expect_audio and not downlink:
        print("[!] 期待下行音訊但沒有收到 —— 檢查 IOT_TTS_PROVIDER（目前若是 windows/deterministic 只會產生靜音）")
        return 3
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="假 ESP32 用戶端（小智 websocket 協議）")
    parser.add_argument("--device-id", default="esp-client-sim")
    parser.add_argument("--ota", help="OTA 位址，例如 http://192.168.1.5:8080/xiaozhi/ota/")
    parser.add_argument("--ws", help="直接指定 WebSocket 位址")
    parser.add_argument("--token", default="", help="裝置 token（IOT_ESP_REQUIRE_TOKEN=1 時需要）")
    parser.add_argument("--wav", help="上行音訊 WAV（會自動轉為 16k 單聲道）")
    parser.add_argument("--say", help="文字模式，跳過 ASR")
    parser.add_argument("--save-pcm", help="把收到的下行音訊存成原始 PCM16")
    parser.add_argument("--save-wav", help="把收到的下行音訊存成 WAV")
    parser.add_argument("--timeout", type=float, default=40.0)
    parser.add_argument("--expect-audio", action="store_true", help="沒收到下行音訊就回傳非零")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
