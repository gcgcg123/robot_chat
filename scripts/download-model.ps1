param(
    [ValidateSet("sensevoice", "whisper", "embedding", "voiceprint", "rerank", "both")]
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
    # Not an ASR backend: the long-term memory flywheel needs these vectors to
    # decide whether a stored memory is relevant to the current question. 90 MB
    # int8-free ONNX graph, run through onnxruntime (already present for VAD),
    # so no torch is installed for it.
    "embedding" = @{
        RepoId    = "Xenova/bge-small-zh-v1.5"
        TargetDir = "models\embedding\bge-small-zh-v1.5"
        Files     = @("onnx/model.onnx", "tokenizer.json")
        Primary   = "onnx/model.onnx"
        Runtime   = "onnxruntime (IOT_EMBEDDING_MODEL_PATH)"
    }
    # Speaker recognition: 85 MB of ECAPA-TDNN weights. Loaded by speechbrain, which
    # otherwise downloads them itself at the first enrollment -- a ~17 s stall (and a
    # hard failure with no Hugging Face access) in the middle of registering a user.
    # Useless without requirements-voiceprint.txt, so bootstrap fetches it only when
    # -WithVoiceprint was passed.
    "voiceprint" = @{
        RepoId    = "speechbrain/spkrec-ecapa-voxceleb"
        TargetDir = "models\voiceprint\ecapa-voxceleb"
        Files     = @("hyperparams.yaml", "embedding_model.ckpt", "mean_var_norm_emb.ckpt", "classifier.ckpt", "label_encoder.ckpt")
        Primary   = "embedding_model.ckpt"
        Runtime   = "speechbrain (VOICEPRINT_PROVIDER=ecapa)"
    }
    # Knowledge-base second stage (off unless IOT_KNOWLEDGE_RERANK=local). ~1.06 GB of PyTorch
    # weights, and on this network only ModelScope serves them (huggingface.co times out), so this
    # entry is fetched by download-rerank-model.py instead of huggingface_hub.
    "rerank" = @{
        RepoId    = "BAAI/bge-reranker-base"
        TargetDir = "models\rerank\bge-reranker-base"
        Files     = @("config.json", "model.safetensors", "sentencepiece.bpe.model", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json")
        Primary   = "model.safetensors"
        Runtime   = "transformers + torch (IOT_KNOWLEDGE_RERANK=local; torch is optional)"
    }
}

# Download one file set. Prefers the Hugging Face CLI next to the interpreter and
# falls back to the Python API, so a missing hf.exe is not fatal.
function Get-ModelFiles {
    param([string]$Repository, [string]$Destination, [string[]]$FileNames, [string]$Python)

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    if ($Endpoint) { $env:HF_ENDPOINT = $Endpoint }

    $scriptDirectories = @()
    if ($Python) {
        $pythonDirectory = Split-Path -Parent $Python
        $scriptDirectories += $pythonDirectory
        $scriptDirectories += (Join-Path $pythonDirectory "Scripts")
    }
    $scriptDirectories += (Join-Path $projectRoot ".venv\Scripts")
    foreach ($cliName in @("hf.exe", "huggingface-cli.exe")) {
        $cliCandidates = @($scriptDirectories | ForEach-Object { Join-Path $_ $cliName })
        $pathCommand = Get-Command $cliName -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($pathCommand) { $cliCandidates += $pathCommand.Source }
        foreach ($cli in ($cliCandidates | Select-Object -Unique)) {
            if (Test-Path -LiteralPath $cli) {
                Write-Host "  using $cliName" -ForegroundColor DarkGray
                # Pass the explicit file list: without it the CLI mirrors the whole
                # repository, which for SenseVoice means 1.1 GB instead of 228 MB.
                & $cli download $Repository @FileNames --local-dir $Destination
                if ($LASTEXITCODE -eq 0) { return }
                Write-Host "  $cliName failed; trying another downloader" -ForegroundColor Yellow
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
    if ($name -eq "rerank") {
        # Deliberately not huggingface_hub: this network cannot reach huggingface.co or
        # hf-mirror.com, and ModelScope does not publish these weights under the same API.
        & $python (Join-Path $PSScriptRoot "download-rerank-model.py") --repo $repository --dest $destination
        if ($LASTEXITCODE -ne 0) { throw "Model download failed: $repository" }
    } else {
        Get-ModelFiles -Repository $repository -Destination $destination -FileNames $entry.Files -Python $python
    }

    $missing = @($entry.Files | Where-Object { -not (Test-Path -LiteralPath (Join-Path $destination $_)) })
    if ($missing.Count -gt 0) { throw "Downloaded $name model is incomplete: $($missing -join ', ')" }

    $primary = Join-Path $destination $entry.Primary
    $hash = (Get-FileHash $primary -Algorithm SHA256).Hash
    Write-Host "  -> $destination" -ForegroundColor Green
    Write-Host "  $($entry.Primary) SHA-256: $hash"
    Write-Host "  runtime: $($entry.Runtime)"
    if ($name -eq "embedding") { Write-Host "  note: onnx\model.onnx is ~90 MB; without it the robot still talks, it just stops remembering." -ForegroundColor DarkGray }
    if ($name -eq "voiceprint") { Write-Host "  note: needs requirements-voiceprint.txt; without those packages enrollment answers 503." -ForegroundColor DarkGray }
    if ($name -eq "rerank") { Write-Host "  note: 1.06 GB; the second stage stays off until IOT_KNOWLEDGE_RERANK=local, and it measured slower than it is worth on this corpus (docs/RAG_KNOWLEDGE_PLAN.md A9.7.15)." -ForegroundColor DarkGray }
}

Write-Host "`nReview each model card and license before redistribution. Weights remain ignored by Git." -ForegroundColor DarkGray
