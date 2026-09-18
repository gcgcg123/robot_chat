param(
    [ValidateSet("sensevoice", "whisper", "both")]
    [string]$Model = "sensevoice",
    # Optional Hugging Face mirror, e.g. https://hf-mirror.com. Defaults to HF_ENDPOINT.
    [string]$Endpoint = "",
    # Advanced overrides; normally derived from -Model.
    [string]$RepoId = "",
    [string]$TargetDir = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "launcher-common.ps1")

# Two ASR backends are supported. SenseVoice is the default: 228 MB instead of
# 1.5 GB, ~0.5 s per utterance on a CPU-only laptop, and it covers Cantonese.
$catalog = @{
    "sensevoice" = @{
        RepoId    = "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
        TargetDir = "models\asr\sensevoice-small"
        Files     = @("model.int8.onnx", "tokens.txt")
        Primary   = "model.int8.onnx"
        Runtime   = "sherpa-onnx (ASR_PROVIDER=sensevoice)"
    }
    "whisper" = @{
        RepoId    = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
        TargetDir = "models\asr\whisper-large-v3-turbo-ct2"
        Files     = @("config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json")
        Primary   = "model.bin"
        Runtime   = "faster-whisper (ASR_PROVIDER=whisper)"
    }
}

# Download one file set. Prefers the Hugging Face CLI next to the interpreter and
# falls back to the Python API, so a missing hf.exe is not fatal.
function Get-ModelFiles {
    param([string]$Repository, [string]$Destination, [string[]]$FileNames, [string]$Python)

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    if ($Endpoint) { $env:HF_ENDPOINT = $Endpoint }

    if ($Python) {
        $scriptsDir = Join-Path (Split-Path -Parent $Python) "Scripts"
        foreach ($cliName in @("hf.exe", "huggingface-cli.exe")) {
            $cli = Join-Path $scriptsDir $cliName
            if (Test-Path -LiteralPath $cli) {
                Write-Host "  using $cliName" -ForegroundColor DarkGray
                # Pass the explicit file list: without it the CLI mirrors the whole
                # repository, which for SenseVoice means 1.1 GB instead of 228 MB.
                & $cli download $Repository @FileNames --local-dir $Destination
                if ($LASTEXITCODE -eq 0) { return }
                Write-Host "  $cliName failed; falling back to the Python API" -ForegroundColor Yellow
            }
        }
    }

    if (-not $Python) { throw "No Python interpreter found. Set IOT_PYTHON or install Python." }

    # Values travel through the environment so PowerShell never has to quote them.
    $env:IOT_HF_REPO = $Repository
    $env:IOT_HF_DIR = $Destination
    $env:IOT_HF_FILES = ($FileNames -join ",")
    & $Python -c "import os; from huggingface_hub import hf_hub_download; [hf_hub_download(repo_id=os.environ['IOT_HF_REPO'], filename=f, local_dir=os.environ['IOT_HF_DIR']) for f in os.environ['IOT_HF_FILES'].split(',')]"
    if ($LASTEXITCODE -ne 0) { throw "Model download failed: $Repository" }
}

$wanted = if ($Model -eq "both") { @("sensevoice", "whisper") } else { @($Model) }
$python = Resolve-ProjectPython -ProjectRoot $projectRoot -Quiet
if ($python) { Write-Host "interpreter: $python" -ForegroundColor DarkGray }
if ($Endpoint) { Write-Host "endpoint   : $Endpoint" -ForegroundColor DarkGray }

foreach ($name in $wanted) {
    $entry = $catalog[$name]
    $repository = if ($RepoId -and $wanted.Count -eq 1) { $RepoId } else { $entry.RepoId }
    $destination = if ($TargetDir -and $wanted.Count -eq 1) { $TargetDir } else { Join-Path $projectRoot $entry.TargetDir }

    Write-Host "`n[$name] $repository" -ForegroundColor Cyan
    Get-ModelFiles -Repository $repository -Destination $destination -FileNames $entry.Files -Python $python

    $missing = @($entry.Files | Where-Object { -not (Test-Path -LiteralPath (Join-Path $destination $_)) })
    if ($missing.Count -gt 0) { throw "Downloaded $name model is incomplete: $($missing -join ', ')" }

    $primary = Join-Path $destination $entry.Primary
    $hash = (Get-FileHash $primary -Algorithm SHA256).Hash
    Write-Host "  -> $destination" -ForegroundColor Green
    Write-Host "  $($entry.Primary) SHA-256: $hash"
    Write-Host "  runtime: $($entry.Runtime)"
}

Write-Host "`nReview each model card and license before redistribution. Weights remain ignored by Git." -ForegroundColor DarkGray
