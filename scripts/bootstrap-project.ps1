param(
    [switch]$Docker,
    [switch]$NoBrowser,
    [switch]$SkipModelDownload,
    [switch]$SkipUpstream,
    # Never create .venv. Used by the test suite and by anyone who deliberately
    # wants the dependencies in the interpreter that PATH names.
    [switch]$NoVenv,
    # Optional dependency sets. Both features fail loudly when their packages are
    # absent (502/503 with an actionable message) instead of degrading silently, so
    # neither is ever installed implicitly. Whisper is implied by -AsrModel.
    [switch]$WithVoiceprint,
    [switch]$WithWhisper,
    # Optional knowledge-base reranker: 1.06 GB of weights plus torch, and the second stage is off
    # by default because it measured slower than it is worth here (A9.7.15). Never implicit.
    [switch]$WithRerank,
    # Skip importing data/RAG_data/*.md into the local knowledge base.
    [switch]$SkipKnowledge,
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
$resolution = Resolve-ProjectPythonDetail -ProjectRoot $projectRoot
$python = $resolution.Path
if (-not $python) {
    throw "No Python interpreter found. Install Python 3.10 or newer, or set IOT_PYTHON in .env, then run this script again."
}

# A path that came from PATH is a guess about the machine, not a decision. Installing
# ~110 MB of dependencies into whatever Python happens to be first on PATH is how a
# machine ends up with a project that cannot start -- and this project has already
# failed exactly that way once ("No module named uvicorn" after PATH named a Python
# that had none of them). When nothing more specific was configured, make the
# checkout self-contained instead. IOT_PYTHON (environment or .env) and
# configs/launcher.json are explicit choices and are never overridden.
if ($resolution.Source -eq "path" -and -not $NoVenv) {
    $venvDir = Join-Path $projectRoot ".venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    if ((Test-Path -LiteralPath $venvDir) -and -not (Test-Path -LiteralPath $venvPython)) {
        throw "The .venv directory exists but contains no interpreter ($venvPython). Delete '$venvDir' and run this script again."
    }
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
        if ($LASTEXITCODE -ne 0) {
            throw "Python 3.10 or newer is required to create .venv, but '$python' is older. Install a newer Python, or point IOT_PYTHON in .env at an environment that already has the dependencies."
        }
        Write-Host "Creating the project virtual environment: $venvDir" -ForegroundColor Cyan
        & $python -m venv $venvDir
        # A Microsoft Store alias or a partial install can report success without
        # producing an interpreter, so the file on disk is what decides.
        if (-not (Test-Path -LiteralPath $venvPython)) {
            throw "Could not create .venv with '$python'. Install the full Python distribution (not the Microsoft Store alias), or set IOT_PYTHON in .env to an environment that already has the dependencies."
        }
    }
    $python = $venvPython
}
Write-Host "interpreter: $python [$($resolution.Source)]" -ForegroundColor DarkGray

& $python -m pip install --upgrade pip
& $python -m pip install -r (Join-Path $projectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "Python dependency installation failed." }

# Optional extras. requirements.txt stays at ~110 MB; each feature below brings its own
# file so the size is a per-feature decision instead of a default. Neither is inferred
# quietly: Whisper follows -AsrModel, voiceprint needs -WithVoiceprint.
if ($WithWhisper -or $AsrModel -ne "sensevoice") {
    Write-Host "Installing the optional Whisper ASR backend..." -ForegroundColor Cyan
    & $python -m pip install -r (Join-Path $projectRoot "requirements-asr-whisper.txt")
    if ($LASTEXITCODE -ne 0) { throw "Whisper backend installation failed." }
}
if ($WithVoiceprint) {
    Write-Host "Installing the optional speaker-recognition packages (torch dominates; ~730 MB)..." -ForegroundColor Cyan
    & $python -m pip install -r (Join-Path $projectRoot "requirements-voiceprint.txt")
    if ($LASTEXITCODE -ne 0) { throw "Speaker-recognition dependency installation failed." }
} else {
    Write-Host "note: speaker recognition is not installed; enrollment will answer 503 until you run:" -ForegroundColor DarkGray
    Write-Host "        powershell -ExecutionPolicy Bypass -File scripts\bootstrap-project.ps1 -WithVoiceprint" -ForegroundColor DarkGray
}
# The knowledge-base reranker needs torch + transformers, and neither is in requirements.txt.
# Installing the packages is not enough to turn the feature on -- IOT_KNOWLEDGE_RERANK stays 0 --
# so this only happens when someone asks for it by name.
if ($WithRerank) {
    Write-Host "Installing the optional reranker packages (torch + transformers; ~600 MB)..." -ForegroundColor Cyan
    & $python -m pip install -r (Join-Path $projectRoot "requirements-rerank.txt")
    if ($LASTEXITCODE -ne 0) { throw "Reranker dependency installation failed." }
}

# .env is gitignored, so a fresh clone does not have one. Create it from the
# tracked example; the launcher still overrides everything it needs.
$envFile = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    Copy-Item -LiteralPath (Join-Path $projectRoot ".env.example") -Destination $envFile
    Write-Host "Created .env from .env.example." -ForegroundColor Cyan
}

# The Xiaozhi tree is an OPTIONAL reference: nothing in services/, simulator/ or
# tests/ imports it. Downloading it must therefore never abort the install --
# HTTPS access to github.com is reset on many networks (e.g. behind the GFW),
# while SSH often still works.
$upstreamPath = Join-Path $projectRoot "upstream\xiaozhi-esp32-server"
$upstreamUrl = "https://github.com/xinnan-tech/xiaozhi-esp32-server.git"
if (Test-Path -LiteralPath (Join-Path $upstreamPath ".git")) {
    Write-Host "Xiaozhi reference already present." -ForegroundColor DarkGray
} elseif ($SkipUpstream) {
    Write-Host "Skipping the optional Xiaozhi reference tree (-SkipUpstream)." -ForegroundColor DarkGray
} else {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if (-not $git) {
        Write-Host "WARNING: git not found; skipping the optional Xiaozhi reference tree." -ForegroundColor Yellow
    } else {
        # A previous failed clone leaves a non-empty directory that blocks git clone.
        if (Test-Path -LiteralPath $upstreamPath) { Remove-Item -Recurse -Force $upstreamPath -ErrorAction SilentlyContinue }
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $upstreamPath) | Out-Null
        & $git.Source clone --depth 1 $upstreamUrl $upstreamPath
        if ($LASTEXITCODE -ne 0) {
            Write-Host ""
            Write-Host "WARNING: could not download the Xiaozhi reference tree." -ForegroundColor Yellow
            Write-Host "         It is optional and nothing in this project imports it," -ForegroundColor Yellow
            Write-Host "         so the installation continues." -ForegroundColor Yellow
            Write-Host "         If you want it later, HTTPS to github.com is often reset; try SSH:" -ForegroundColor Yellow
            Write-Host "           git clone --depth 1 git@github.com:xinnan-tech/xiaozhi-esp32-server.git `"$upstreamPath`"" -ForegroundColor Yellow
            Write-Host ""
        }
    }
}

# Download the configured ASR model. SenseVoice is the default backend: it is
# 228 MB instead of 1.5 GB and roughly 60x faster on a CPU-only machine.
# The embedding model is not a backend choice: long-term memory needs it, so it
# is always fetched alongside whichever ASR backend was picked.
$modelProbes = @{
    "sensevoice" = "models\asr\sensevoice-small\model.int8.onnx"
    "whisper"    = "models\asr\whisper-large-v3-turbo-ct2\model.bin"
    "embedding"  = "models\embedding\bge-small-zh-v1.5\onnx\model.onnx"
    "voiceprint" = "models\voiceprint\ecapa-voxceleb\embedding_model.ckpt"
    "rerank"     = "models\rerank\bge-reranker-base\model.safetensors"
}
$asrModels = if ($AsrModel -eq "both") { @("sensevoice", "whisper") } else { @($AsrModel) }
$wantedModels = @($asrModels) + @("embedding")
# Only fetch the 85 MB speaker model when the feature was actually installed; speechbrain
# downloads it lazily anyway, so fetching it for someone who cannot use it is waste.
if ($WithVoiceprint) { $wantedModels += "voiceprint" }
# Same reasoning for the 1.06 GB reranker, which is additionally off by default at runtime.
if ($WithRerank) { $wantedModels += "rerank" }
foreach ($wantedModel in $wantedModels) {
    $probe = Join-Path $projectRoot $modelProbes[$wantedModel]
    if (-not $SkipModelDownload -and -not (Test-Path -LiteralPath $probe)) {
        Write-Host "Downloading the $wantedModel model..." -ForegroundColor Cyan
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "download-model.ps1") -Model $wantedModel
        if ($LASTEXITCODE -ne 0) { throw "$wantedModel model download failed." }
    }
}

# Local knowledge base (RAG). Imported only when a corpus is actually present, and only
# after the embedding model exists (the importer needs it). The import key is
# (file sha256, cleaner version), so re-running the installer is a no-op. A failure here is
# not fatal: the robot talks, remembers and answers without a corpus, it just cannot cite.
if (-not $SkipKnowledge) {
    $corpusDir = Join-Path $projectRoot "data\RAG_data"
    $corpusFiles = @(Get-ChildItem -Path $corpusDir -Recurse -Filter *.md -File -ErrorAction SilentlyContinue)
    if ($corpusFiles.Count -gt 0) {
        $dbSetting = Get-DotEnvValue -ProjectRoot $projectRoot -Name "DATABASE_PATH"
        if (-not $dbSetting) { $dbSetting = "IoTGroup5\emotional_robot.sqlite3" }
        $knowledgeDb = if ([System.IO.Path]::IsPathRooted($dbSetting)) { $dbSetting } else { Join-Path $projectRoot $dbSetting }
        Write-Host "Importing the local knowledge base ($($corpusFiles.Count) document(s))..." -ForegroundColor Cyan
        & $python (Join-Path $PSScriptRoot "import-knowledge.py") --database $knowledgeDb
        if ($LASTEXITCODE -ne 0) {
            Write-Host "WARNING: the knowledge base import failed; every other feature still works." -ForegroundColor Yellow
        }
    }
}

$secretPath = Join-Path $projectRoot "data\secrets\deepseek.key"
$adminPath = Join-Path $projectRoot "data\secrets\admin.password"
if (-not (Test-Path -LiteralPath $secretPath) -or -not (Test-Path -LiteralPath $adminPath)) {
    Write-Host "First run: enter the DeepSeek API key and Dashboard administrator password." -ForegroundColor Cyan
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "setup-project.ps1") -SkipInstall
    if ($LASTEXITCODE -ne 0) { throw "First-time secure setup failed." }
}

# Runtime data lives inside the checkout (.\IoTGroup5), never under %LOCALAPPDATA%.
if (-not $DataDir) {
    if ($env:IOT_DATA_DIR) { $DataDir = $env:IOT_DATA_DIR }
    else { $DataDir = Join-Path $projectRoot "IoTGroup5" }
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
