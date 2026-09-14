"""Run the actual PowerShell downloader from a clean ZIP-shaped fixture."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("cli", ["hf.exe", "huggingface-cli.exe"])
def test_local_cli_download_uses_file_path_and_checks_artifacts(tmp_path, cli):
    project = tmp_path / "專案 with spaces"
    scripts = project / "scripts"
    tools = project / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    tools.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/download-model.ps1", scripts / "download-model.ps1")
    # Native executable fixture: no network or real model download needed.
    source = tmp_path / "Cli.cs"
    source.write_text('''
using System;
using System.IO;
public class Cli {
  public static int Main(string[] args) {
    if(args.Length != 4 || args[0] != "download" || args[1] != "fixture/model" || args[2] != "--local-dir") return 17;
    Directory.CreateDirectory(args[3]);
    foreach(var name in new[]{"config.json","model.bin","preprocessor_config.json","tokenizer.json","vocabulary.json"})
      File.WriteAllText(Path.Combine(args[3],name),"fixture");
    return 0;
  }
}''')
    env = os.environ.copy()
    env["PSModulePath"] = ""
    env["TEST_CLI_SOURCE"] = str(source)
    env["TEST_CLI_EXE"] = str(tools / cli)
    compiled = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "Add-Type -Path $env:TEST_CLI_SOURCE -OutputAssembly $env:TEST_CLI_EXE -OutputType ConsoleApplication"],
        env=env, capture_output=True, text=True, errors="replace", timeout=30)
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(scripts / "download-model.ps1"), "-RepoId", "fixture/model"],
        cwd=project, env=env, capture_output=True, text=True, errors="replace", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (project / "models/asr/whisper-large-v3-turbo-ct2/model.bin").read_text() == "fixture"
    assert "SHA-256" in result.stdout

