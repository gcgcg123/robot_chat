"""Exercise real BAT entrypoints with a child-script fixture, not source matching."""
import os
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("filename,script", [
    ("一鍵啟動.bat", "start-project.ps1"),
    ("一鍵安裝並啟動.bat", "bootstrap-project.ps1"),
])
@pytest.mark.parametrize("code", [0, 7])
def test_batch_isolates_module_path_forwards_flags_and_exit_code(tmp_path, filename, script, code):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(ROOT / filename, tmp_path / filename)
    (scripts / script).write_text(
        'param([switch]$NoBrowser)\n'
        'if (-not $NoBrowser) { exit 9 }\n'
        'Import-Module Microsoft.PowerShell.Security -ErrorAction Stop\n'
        'ConvertTo-SecureString "fixture" -AsPlainText -Force | Out-Null\n'
        f'exit {code}\n', encoding="utf-8")
    env = os.environ.copy()
    env["IOT_LAUNCHER_NO_PAUSE"] = "1"
    result = subprocess.run(
        f'cmd.exe /d /s /c ""{tmp_path / filename}" -NoBrowser"',
        cwd=tmp_path, env=env, capture_output=True, text=True, errors="replace", timeout=30)
    assert result.returncode == code, result.stdout + result.stderr
    assert "already present" not in result.stderr
