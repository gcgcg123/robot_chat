"""Send a simulator heartbeat until the ESP hardware is available."""
from __future__ import annotations

import argparse
import time
from urllib.parse import urlparse
import httpx


def send(url: str, device_id: str, firmware_version: str, token: str = "", csrf: str = "") -> dict:
    payload = {"device_id": device_id, "firmware": firmware_version, "capabilities": ["audio_in", "audio_out"], "ip": "simulator", "is_simulator": True, "user_id": "sim-user"}
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if csrf:
        headers["X-CSRF-Token"] = csrf
    response = httpx.post(url, json=payload, headers=headers, timeout=10,trust_env=False,)
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
    parser.add_argument("--local-data-dir", default="", help="Manage the local PC simulator session; loopback only")
    args = parser.parse_args()
    if args.local_data_dir and urlparse(args.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Managed local credentials may only be sent to loopback")
    while True:
        if args.local_data_dir:
            from simulator.local_session import ensure_local_token
            args.token = ensure_local_token(args.local_data_dir, args.device)
        print(send(args.url, args.device, args.firmware, args.token, args.csrf))
        if args.once:
            return
        time.sleep(max(1, args.interval))


if __name__ == "__main__":
    main()
