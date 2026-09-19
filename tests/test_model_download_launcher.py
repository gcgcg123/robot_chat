"""Run the actual PowerShell downloader from a clean ZIP-shaped fixture."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "sensevoice": ("models/asr/sensevoice-small", ["model.int8.onnx", "tokens.txt"]),
    "whisper": ("models/asr/whisper-large-v3-turbo-ct2", [
        "config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"
    ]),
    "embedding": ("models/embedding/bge-small-zh-v1.5", ["onnx/model.onnx", "tokenizer.json"]),
}


@pytest.mark.parametrize("cli", ["hf.exe", "huggingface-cli.exe"])
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("incomplete", [False, True])
def test_local_cli_download_uses_file_path_and_checks_artifacts(tmp_path, cli, model, incomplete):
    target, files = MODELS[model]
    project = tmp_path / "專案 with spaces"
    scripts = project / "scripts"
    tools = project / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    tools.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/download-model.ps1", scripts / "download-model.ps1")
    shutil.copyfile(ROOT / "scripts/launcher-common.ps1", scripts / "launcher-common.ps1")
    # Native executable fixture: no network or real model download needed.
    source = tmp_path / "Cli.cs"
    source.write_text('''
using System;
using System.IO;
public class Cli {
  public static int Main(string[] args) {
    string[] files = Environment.GetEnvironmentVariable("TEST_MODEL_FILES").Split(',');
    if(args.Length != files.Length + 4 || args[0] != "download" || args[1] != "fixture/model" || args[args.Length - 2] != "--local-dir") return 17;
    string destination = args[args.Length - 1];
    for(int i = 0; i < files.Length; i++) {
      if(args[i + 2] != files[i]) return 18;
      if(Environment.GetEnvironmentVariable("TEST_MODEL_INCOMPLETE") == "1" && i == files.Length - 1) continue;
      string path = Path.Combine(destination,files[i]);
      Directory.CreateDirectory(Path.GetDirectoryName(path));
      File.WriteAllText(path,"fixture");
    }
    return 0;
  }
}''')
    env = os.environ.copy()
    env["PSModulePath"] = ""
    env["TEST_CLI_SOURCE"] = str(source)
    env["TEST_CLI_EXE"] = str(tools / cli)
    env["TEST_MODEL_FILES"] = ",".join(files)
    env["TEST_MODEL_INCOMPLETE"] = "1" if incomplete else "0"
    compiled = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "Add-Type -Path $env:TEST_CLI_SOURCE -OutputAssembly $env:TEST_CLI_EXE -OutputType ConsoleApplication"],
        env=env, capture_output=True, text=True, errors="replace", timeout=30)
    assert compiled.returncode == 0, compiled.stderr
    # Fail locally if the downloader overlooks the adjacent CLI and tries Python.
    shutil.copyfile(tools / cli, tools / "python.exe")
    env["IOT_PYTHON"] = str(tools / "python.exe")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(scripts / "download-model.ps1"), "-RepoId", "fixture/model", "-Model", model],
        cwd=project, env=env, capture_output=True, text=True, errors="replace", timeout=30)
    if incomplete:
        assert result.returncode != 0
        assert "model is incomplete" in result.stdout + result.stderr
        return
    assert result.returncode == 0, result.stdout + result.stderr
    for name in files:
        assert (project / target / name).read_text() == "fixture"
    assert "SHA-256" in result.stdout
