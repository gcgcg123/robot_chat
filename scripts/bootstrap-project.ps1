param(
    [switch]$Docker,
    [switch]$NoBrowser,
    [switch]$SkipModelDownload,
    [ValidateSet("sensevoice", "whisper", "both")]
    [string]$AsrModel = "sensevoice",
    [string]$DataDir
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
. (Join-Path $PSScriptRoot "launcher-common.ps1")

if ($Docker) {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) { throw "Docker Desktop is required for Docker mode. Install Docker Desktop, then run this script again." }
    $dockerEnv = Join-Path $projectRoot ".env.docker"
    if (-not (Test-Path -LiteralPath $dockerEnv)) {
        Copy-Item -LiteralPath (Join-Path $projectRoot ".env.docker.example") -Destination $dockerEnv
        throw "Created .env.docker from the example. Set IOT_ADMIN_PASSWORD and DEEPSEEK_API_KEY in that local file, then run again."
    }
    & $docker.Source compose up --build -d
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed to start the project." }
    Write-Host "Project started in Docker. Open http://127.0.0.1:8080/dashboard" -ForegroundColor Green
    exit 0
}

# Project Python interpreter, resolved so the bootstrap is not tied to one machine.
$python = Resolve-ProjectPython -ProjectRoot $projectRoot
Write-Host "interpreter: $python" -ForegroundColor DarkGray

& $python -m pip install --upgrade pip
& $python -m pip install -r (Join-Path $projectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }

# .env is gitignored, so a fresh clone does not have one. Create it from the
# tracked example; the launcher still overrides everything it needs.
$envFile = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $projectRoot ".env.example") -Destination $envFile
    Write-Host "Created .env from .env.example." -ForegroundColor Cyan
}

$upstreamPath = Join-Path $projectRoot "upstream\xiaozhi-esp32-server"
if (-not (Test-Path -LiteralPath (Join-Path $upstreamPath ".git"))) {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) { throw "Git is required to download the pinned Xiaozhi reference." }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $upstreamPath) | Out-Null
    & $git.Source clone --depth 1 https://github.com/xinnan-tech/xiaozhi-esp32-server.git $upstreamPath
    if ($LASTEXITCODE -ne 0) { throw "Unable to download the Xiaozhi reference repository." }
}

# Download the configured ASR model. SenseVoice is the default backend: it is
# 228 MB instead of 1.5 GB and roughly 60x faster on a CPU-only machine.
$modelProbes = @{
    "sensevoice" = "models\asr\sensevoice-small\model.int8.onnx"
    "whisper"    = "models\asr\whisper-large-v3-turbo-ct2\model.bin"
}
$wantedModels = if ($AsrModel -eq "both") { @("sensevoice", "whisper") } else { @($AsrModel) }
foreach ($wantedModel in $wantedModels) {
    $probe = Join-Path $projectRoot $modelProbes[$wantedModel]
    if (-not $SkipModelDownload -and -not (Test-Path -LiteralPath $probe)) {
        Write-Host "Downloading the $wantedModel ASR model..." -ForegroundColor Cyan
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "download-model.ps1") -Model $wantedModel
        if ($LASTEXITCODE -ne 0) { throw "$wantedModel model download failed." }
    }
}

$secretPath = Join-Path $projectRoot "data\secrets\deepseek.key"
$adminPath = Join-Path $projectRoot "data\secrets\admin.password"
if (-not (Test-Path -LiteralPath $secretPath) -or -not (Test-Path -LiteralPath $adminPath)) {
    Write-Host "First run: enter the DeepSeek API key and Dashboard administrator password." -ForegroundColor Cyan
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "setup-project.ps1") -SkipInstall
    if ($LASTEXITCODE -ne 0) { throw "First-time secure setup failed." }
}

if (-not $DataDir) {
    if ($env:LOCALAPPDATA) { $DataDir = Join-Path $env:LOCALAPPDATA "IoTGroup5" }
    else { $DataDir = Join-Path $projectRoot "data\runtime" }
}
$tokenPath = Join-Path $DataDir "secrets\simulator.token"
if (-not (Test-Path -LiteralPath $tokenPath)) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "provision-simulator.ps1") -DataDir $DataDir -DeviceId "sim-device"
    if ($LASTEXITCODE -ne 0) { throw "Simulator token provisioning failed." }
}

$startArgs = @("-DataDir", $DataDir)
if ($NoBrowser) { $startArgs += "-NoBrowser" }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "start-project.ps1") @startArgs
if ($LASTEXITCODE -ne 0) { throw "Project startup failed." }
