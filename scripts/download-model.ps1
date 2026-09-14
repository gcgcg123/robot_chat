param(
    [string]$RepoId = "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    [string]$TargetDir = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $TargetDir) {
    $TargetDir = Join-Path $projectRoot "models\asr\whisper-large-v3-turbo-ct2"
}
New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null

$localHf = Join-Path $projectRoot ".venv\Scripts\hf.exe"
$hf = if (Test-Path -LiteralPath $localHf) { $localHf } else { (Get-Command hf -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source }
if ($hf) {
    & $hf download $RepoId --local-dir $TargetDir
} else {
    $localLegacy = Join-Path $projectRoot ".venv\Scripts\huggingface-cli.exe"
    $legacy = if (Test-Path -LiteralPath $localLegacy) { $localLegacy } else { (Get-Command huggingface-cli -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source }
    if (-not $legacy) {
        throw "Hugging Face CLI is required. Install it in the project environment with: python -m pip install huggingface_hub"
    }
    & $legacy download $RepoId --local-dir $TargetDir
}
if ($LASTEXITCODE -ne 0) { throw "Model download failed." }

$required = @("config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json")
$missing = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $TargetDir $_)) })
if ($missing.Count -gt 0) { throw "Downloaded model is incomplete: $($missing -join ', ')" }

$hash = (Get-FileHash (Join-Path $TargetDir "model.bin") -Algorithm SHA256).Hash
Write-Host "Model downloaded to $TargetDir" -ForegroundColor Green
Write-Host "model.bin SHA-256: $hash"
Write-Host "Review the model card and license before redistribution. Weights remain ignored by Git."
