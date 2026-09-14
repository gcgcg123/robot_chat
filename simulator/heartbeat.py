"""Send a simulator heartbeat until the ESP hardware is available."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
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
    # Local backend traffic must not be sent through workstation proxy settings.
    local = urlparse(url).hostname in {"127.0.0.1", "localhost", "::1"}
    response = httpx.post(url, json=payload, headers=headers, timeout=5, trust_env=not local)
    response.raise_for_status()
    result = response.json()
    if result.get("ok") is not True or result.get("device", {}).get("device_id") != device_id:
        raise ValueError("Unexpected heartbeat response")
    return result


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
    parser.add_argument("--status-file", default="")
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()
    if args.local_data_dir and urlparse(args.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Managed local credentials may only be sent to loopback")
    def report(state, **fields):
        data = {"state": state, "time": datetime.now(timezone.utc).isoformat(),
                "run_id": args.run_id, "pid": os.getpid(), "device_id": args.device, **fields}
        if args.status_file:
            path = Path(args.status_file)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f".tmp-{os.getpid()}")
            temporary.write_text(json.dumps(data), encoding="utf-8")
            temporary.replace(path)
        print(json.dumps(data), file=sys.stderr if state in {"error", "retrying"} else sys.stdout, flush=True)

    failures = 0
    report("starting")
    while True:
        try:
            if args.local_data_dir:
                from simulator.local_session import ensure_local_token
                args.token = ensure_local_token(args.local_data_dir, args.device)
            send(args.url, args.device, args.firmware, args.token, args.csrf)
            failures = 0
            report("ready")
            if args.once:
                return
            time.sleep(max(1, args.interval))
        except Exception as exc:
            failures += 1
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            transient = isinstance(exc, httpx.TransportError) or (status is not None and status >= 500)
            retry = transient and failures < 3
            # Never include headers, bearer tokens or response bodies in diagnostics.
            report("retrying" if retry else "error", error=type(exc).__name__, http_status=status, attempt=failures)
            if not retry:
                raise SystemExit(1) from None
            time.sleep(failures)


if __name__ == "__main__":
    main()
