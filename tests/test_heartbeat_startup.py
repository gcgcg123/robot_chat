import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("responses,expected", [([200], "ready"), ([503, 200], "ready"), ([401], "error")])
def test_heartbeat_records_delivery_or_reason_without_leaking_token(tmp_path, responses, expected):
    codes = list(responses)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            code = codes.pop(0) if len(codes) > 1 else codes[0]
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true,"device":{"device_id":"sim-device"}}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = tmp_path / "心跳 status.json"
    env = os.environ.copy()
    env["HTTP_PROXY"] = "http://127.0.0.1:1"
    env["ALL_PROXY"] = "http://127.0.0.1:1"
    env["NO_PROXY"] = ""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "simulator.heartbeat", "--once", "--token", "fixture-private-token",
             "--url", f"http://127.0.0.1:{server.server_port}/api/device/heartbeat",
             "--status-file", str(report), "--run-id", "this-run"],
            cwd=ROOT, env=env, capture_output=True, text=True, errors="replace", timeout=20)
        assert report.exists(), result.stderr
        status = json.loads(report.read_text())
        assert status["state"] == expected
        assert status["run_id"] == "this-run"
        assert status["time"]
        assert (result.returncode == 0) == (expected == "ready")
        if expected == "error":
            assert status["http_status"] == 401
        assert "fixture-private-token" not in result.stdout + result.stderr + report.read_text()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

