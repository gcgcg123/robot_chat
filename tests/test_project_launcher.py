from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
WINDOWS_POWERSHELL = shutil.which("powershell")


def run_script(
    script: str,
    *arguments: str,
    env: dict[str, str] | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL, "PowerShell is required for the Windows launcher"
    command = [
        POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / script),
        *arguments,
    ]
    if capture:
        return subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=60,
        )
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
    )


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def test_launcher_parses_in_windows_powershell_51():
    assert WINDOWS_POWERSHELL, "Windows PowerShell is required by the .bat launchers"
    command = [
        WINDOWS_POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(ROOT / "scripts" / "start-project.ps1"),
        "-CheckOnly",
        "-SkipSecret",
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, (result.stderr or "") + (result.stdout or "")


def test_noninteractive_setup_encrypts_secret_instead_of_storing_plaintext():
    with tempfile.TemporaryDirectory() as temp_dir:
        secret_file = Path(temp_dir) / "deepseek.key"
        environment = os.environ.copy()
        environment["DEEPSEEK_API_KEY"] = "test-key-not-real"

        result = run_script(
            "setup-project.ps1",
            "-NonInteractive",
            "-SkipInstall",
            "-SecretFile",
            str(secret_file),
            env=environment,
        )

        assert result.returncode == 0, result.stderr + result.stdout
        assert secret_file.exists()
        assert "test-key-not-real" not in secret_file.read_text(encoding="utf-8")


def test_noninteractive_setup_rejects_whitespace_secret():
    with tempfile.TemporaryDirectory() as temp_dir:
        secret_file = Path(temp_dir) / "deepseek.key"
        environment = os.environ.copy()
        environment["DEEPSEEK_API_KEY"] = "   "

        result = run_script(
            "setup-project.ps1",
            "-NonInteractive",
            "-SkipInstall",
            "-SecretFile",
            str(secret_file),
            env=environment,
        )

        assert result.returncode != 0
        assert not secret_file.exists()


def test_check_only_reports_ready_project_without_starting_processes():
    with tempfile.TemporaryDirectory() as temp_dir:
        result = run_script(
            "start-project.ps1",
            "-CheckOnly",
            "-SkipSecret",
            "-RuntimeDir",
            str(Path(temp_dir) / "runtime"),
            "-LogDir",
            str(Path(temp_dir) / "logs"),
        )

        assert result.returncode == 0, result.stderr + result.stdout
        report = json.loads(result.stdout.strip().splitlines()[-1])
        assert report["ready"] is True
        assert Path(report["project_root"]).resolve() == ROOT.resolve()
        assert report["python_ready"] is True


def test_start_health_heartbeat_and_stop_round_trip():
    port = free_port()
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_dir = Path(temp_dir) / "runtime"
        log_dir = Path(temp_dir) / "logs"
        data_dir = Path(temp_dir) / "data"
        from services.storage.database import open_database
        from services.storage.migrations import migrate
        from services.security.auth import create_session
        with open_database(data_dir / "emotional_robot.sqlite3") as conn:
            migrate(conn)
            device_token = create_session(conn, "sim-device", "device", device_id="sim-device")
        environment = os.environ.copy()
        environment["DEEPSEEK_API_KEY"] = "test-key-not-real"
        environment["IOT_ADMIN_PASSWORD"] = "test-password"

        started = run_script(
            "start-project.ps1",
            "-NoBrowser",
            "-Port",
            str(port),
            "-RuntimeDir",
            str(runtime_dir),
            "-LogDir",
            str(log_dir),
            "-DataDir",
            str(data_dir),
            "-DeviceToken",
            device_token,
            env=environment,
            capture=False,
        )
        try:
            assert started.returncode == 0, started.stderr + started.stdout
            deadline = time.time() + 10
            health = None
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                        health = json.loads(response.read().decode("utf-8"))
                    break
                except OSError:
                    time.sleep(0.2)
            assert health and health["status"] == "ok"
            login_request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/auth/login",
                data=json.dumps({"actor_id": "test-admin", "password": "test-password"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(login_request, timeout=2) as login_response:
                session_cookie = login_response.headers["Set-Cookie"].split(";", 1)[0]
            devices = []
            deadline = time.time() + 10
            while time.time() < deadline:
                devices_request = urllib.request.Request(f"http://127.0.0.1:{port}/api/devices", headers={"Cookie": session_cookie})
                with urllib.request.urlopen(devices_request, timeout=1) as response:
                    devices = json.loads(response.read().decode("utf-8"))
                if any(item["device_id"] == "sim-device" and item["status"] == "online" for item in devices):
                    break
                time.sleep(0.2)
            assert any(item["device_id"] == "sim-device" and item["status"] == "online" for item in devices)
        finally:
            stopped = run_script(
                "stop-project.ps1",
                "-RuntimeDir",
                str(runtime_dir),
                capture=False,
            )
        assert stopped.returncode == 0, stopped.stderr + stopped.stdout
        assert not (runtime_dir / "service.json").exists()
        assert not (runtime_dir / "heartbeat.json").exists()


def test_stop_ignores_tampered_process_state_instead_of_killing_unrelated_process():
    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_dir = Path(temp_dir)
        state_file = runtime_dir / "service.json"
        state_file.write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "start_time_ticks": 0,
                    "executable": "not-the-real-executable",
                    "role": "service",
                    "command_contains": "services.dialogue.app:app",
                }
            ),
            encoding="utf-8",
        )

        result = run_script("stop-project.ps1", "-RuntimeDir", str(runtime_dir))

        assert result.returncode == 0, result.stderr + result.stdout
        assert not state_file.exists()
        assert os.getpid() > 0


def test_check_only_reports_missing_first_run_prerequisites():
    with tempfile.TemporaryDirectory() as temp_dir:
        environment = os.environ.copy()
        environment.pop("IOT_ADMIN_PASSWORD", None)
        result = run_script(
            "start-project.ps1",
            "-CheckOnly",
            "-SkipSecret",
            "-RuntimeDir",
            str(Path(temp_dir) / "runtime"),
            "-LogDir",
            str(Path(temp_dir) / "logs"),
            "-DataDir",
            str(Path(temp_dir) / "data"),
            env=environment,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        report = json.loads(result.stdout.strip().splitlines()[-1])
        assert report["admin_configured"] is False
        assert report["simulator_token_configured"] is False
        assert "admin_password_required" in report["diagnostics"]
        assert "simulator_token_required_for_heartbeat" in report["diagnostics"]


def test_check_only_does_not_require_simulator_token_when_heartbeat_disabled():
    with tempfile.TemporaryDirectory() as temp_dir:
        environment = os.environ.copy()
        environment.pop("IOT_ADMIN_PASSWORD", None)
        result = run_script(
            "start-project.ps1",
            "-CheckOnly",
            "-NoHeartbeat",
            "-SkipSecret",
            "-RuntimeDir",
            str(Path(temp_dir) / "runtime"),
            "-LogDir",
            str(Path(temp_dir) / "logs"),
            "-DataDir",
            str(Path(temp_dir) / "data"),
            env=environment,
        )
        assert result.returncode == 0, result.stderr + result.stdout
        report = json.loads(result.stdout.strip().splitlines()[-1])
        assert "simulator_token_required_for_heartbeat" not in report["diagnostics"]


def test_provision_simulator_creates_device_bound_token_without_logging_token():
    with tempfile.TemporaryDirectory() as temp_dir:
        data_dir = Path(temp_dir) / "data"
        result = run_script(
            "provision-simulator.ps1",
            "-DataDir",
            str(data_dir),
            "-DeviceId",
            "sim-test",
        )
        assert result.returncode == 0, result.stderr + result.stdout
        token_file = data_dir / "secrets" / "simulator.token"
        token = token_file.read_text(encoding="utf-8").strip()
        assert len(token) >= 32
        assert token not in result.stdout
        from services.storage.database import open_database
        # Validate the binding using the project hash helper instead of
        # persisting the bearer token in logs or metadata.
        from services.security.auth import _hash
        with open_database(data_dir / "emotional_robot.sqlite3") as conn:
            row = conn.execute("SELECT role, device_id, revoked_at FROM sessions WHERE token_hash=?", (_hash(token),)).fetchone()
        assert row and row["role"] == "device" and row["device_id"] == "sim-test" and row["revoked_at"] is None
