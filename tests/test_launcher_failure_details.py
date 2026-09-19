import json
import os
from tests.test_project_launcher import run_script, free_port


def test_failed_heartbeat_rejects_old_receipt_and_preserves_exit_reason(tmp_path):
    runtime = tmp_path / "runtime"
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "heartbeat-status.json").write_text(json.dumps({
        "run_id": "old-run", "state": "ready", "time": "2099-01-01T00:00:00+00:00"
    }))
    env = os.environ.copy()
    env["IOT_TESTING"] = "1"
    env["IOT_ADMIN_PASSWORD"] = "test-only"
    token = "invalid-fixture-token"
    try:
        result = run_script("start-project.ps1", "-NoBrowser", "-Port", str(free_port()),
                            "-RuntimeDir", str(runtime), "-LogDir", str(logs),
                            "-DataDir", str(tmp_path / "資料 with spaces"),
                            "-DeviceToken", token, env=env)
        assert result.returncode != 0
        diagnostic = json.loads((logs / "startup-error.json").read_text())
        assert diagnostic["reason"] in {"process_exited", "process_inspection_failed"}
        assert diagnostic["run_id"] != "old-run"
        status = json.loads((logs / "heartbeat-status.json").read_text())
        assert status["state"] == "error" and status["http_status"] == 401
        assert token not in result.stdout + result.stderr + (logs / "startup-error.json").read_text()
    finally:
        run_script("stop-project.ps1", "-RuntimeDir", str(runtime))

