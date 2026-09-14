"""Send a simulator heartbeat until the ESP hardware is available."""
from __future__ import annotations

import argparse
import time
import httpx


def send(url: str, device_id: str, firmware_version: str, token: str = "", csrf: str = "") -> dict:
    payload = {"device_id": device_id, "firmware": firmware_version, "capabilities": ["audio_in", "audio_out"], "ip": "simulator", "is_simulator": True, "user_id": "sim-user"}
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if csrf:
        headers["X-CSRF-Token"] = csrf
    response = httpx.post(url, json=payload, headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080/api/device/heartbeat")
    parser.add_argument("--device", default="sim-device")
    parser.add_argument("--device-id", dest="device", default=argparse.SUPPRESS)
    parser.add_argument("--firmware", default="simulator-0.1")
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--token", default="")
    parser.add_argument("--csrf", default="")
    args = parser.parse_args()
    while True:
        print(send(args.url, args.device, args.firmware, args.token, args.csrf))
        if args.once:
            return
        time.sleep(max(1, args.interval))


if __name__ == "__main__":
    main()
