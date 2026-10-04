param(
    [switch]$Development,
    [switch]$NoBrowser,
    [switch]$NoHeartbeat,
    [switch]$CheckOnly,
    [switch]$SkipSecret,
    [int]$Port = 0,
    [string]$RuntimeDir,
    [string]$LogDir,
    [string]$SecretFile,
    [string]$AdminSecretFile,
    [string]$DataDir,
    [string]$DeviceToken
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "launcher-common.ps1")

function Repair-ProcessEnvironmentPath {
    <#
        Start-Process fails when the current Windows process contains both
        "PATH" and "Path" entries. Some shells (notably Codex/agent launchers)
        produce that duplicate block. Normalise the current process to a single
        "Path" entry before any child process is launched.
    #>
    $pathValue = $env:Path
    [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    [Environment]::SetEnvironmentVariable("Path", $null, "Process")
    [Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")
}

Repair-ProcessEnvironmentPath

$configPath = Join-Path $projectRoot "configs\launcher.json"
$config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
$hostAddress = [string]$config.host
$actualPort = if ($Port -gt 0) { $Port } else { [int]$config.port }
if (-not $RuntimeDir) { $RuntimeDir = Join-Path $projectRoot "runtime" }
if (-not $LogDir) { $LogDir = Join-Path $projectRoot "logs" }
if (-not $SecretFile) { $SecretFile = Join-Path $projectRoot "data\secrets\deepseek.key" }
if (-not $AdminSecretFile) { $AdminSecretFile = Join-Path $projectRoot "data\secrets\admin.password" }
# Runtime data lives inside the checkout (.\IoTGroup5) so nothing is written to
# the C: drive. An explicit -DataDir or IOT_DATA_DIR still wins.
if (-not $DataDir) { $DataDir = if ($env:IOT_DATA_DIR) { $env:IOT_DATA_DIR } else { Join-Path $projectRoot "IoTGroup5" } }
$venvPython = Resolve-ProjectPython -ProjectRoot $projectRoot -Quiet
$dashboardUrl = "http://${hostAddress}:${actualPort}/dashboard"
$healthUrl = "http://${hostAddress}:${actualPort}/health"

function Resolve-AsrProvider {
    <#
        Keep the configured SenseVoice preference when its model is present;
        otherwise fall back to Whisper so the launcher still starts on a
        checkout that only has the Whisper checkpoint.
    #>
    $configured = if ($config.asr_provider) { [string]$config.asr_provider } else { "whisper" }
    if ($configured -ne "sensevoice") { return $configured }

    $sensePath = if ($config.asr_sensevoice_model_path) {
        [string]$config.asr_sensevoice_model_path
    } else {
        "models\asr\sensevoice-small"
    }
    if (-not [System.IO.Path]::IsPathRooted($sensePath)) {
        $sensePath = Join-Path $projectRoot $sensePath
    }
    $probe = Join-Path $sensePath "model.int8.onnx"
    if (Test-Path -LiteralPath $probe) { return "sensevoice" }

    Write-Host "SenseVoice model not found; falling back to Whisper ASR for this launch." -ForegroundColor Yellow
    return "whisper"
}

if (-not $CheckOnly -and (-not $venvPython -or -not (Test-Path -LiteralPath $venvPython))) {
    throw "No Python interpreter found. Install Python 3.10 or newer and double-click 一鍵安裝並啟動.bat, or set IOT_PYTHON (in .env or the environment), or fill in 'python' in configs\launcher.json."
}

# A resolved interpreter that cannot import the app's dependencies is the failure
# this probe exists for: PATH commonly names a Python that has none of them, and the
# only symptom used to be an opaque "Unable to inspect the newly started process".
# Probing here names the interpreter and both ways to point the project at the right
# one, before anything is started.
$interpreterRunnable = $false
if ($venvPython -and (Test-Path -LiteralPath $venvPython)) {
    # PowerShell 5.1 turns a native command's stderr into a *terminating* error while
    # $ErrorActionPreference is 'Stop', so this probe used to abort with a raw traceback on exactly
    # the machine it exists for -- a fresh one whose PATH Python has none of the dependencies --
    # instead of reporting python_runnable=false. Measured in a clean clone on 2026-10-03.
    $probePreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $venvPython -c "import uvicorn" 2>$null | Out-Null
        $interpreterRunnable = ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $probePreference
    }
}
if (-not $CheckOnly -and -not $interpreterRunnable) {
    throw "The interpreter '$venvPython' cannot import the project's dependencies (uvicorn), so the service would exit immediately. Point the project at the environment that has them: put IOT_PYTHON=<env>\python.exe in .env (machine-local and gitignored), or fill in 'python' in configs\launcher.json. Then double-click 一鍵安裝並啟動.bat (or run .\scripts\bootstrap-project.ps1) to install the dependencies there."
}

New-Item -ItemType Directory -Force -Path $RuntimeDir, $LogDir | Out-Null

if ($CheckOnly) {
    $adminConfigured = -not [string]::IsNullOrWhiteSpace($env:IOT_ADMIN_PASSWORD)
    if (-not $adminConfigured -and (Test-Path -LiteralPath $AdminSecretFile)) { $adminConfigured = $true }
    $deviceIdForCheck = [string]$config.simulator_device_id
    $tokenCandidates = @(
        (Join-Path $DataDir "secrets\simulator.token"),
        (Join-Path $DataDir ("secrets\simulator-{0}.token" -f $deviceIdForCheck))
    )
    $simulatorTokenConfigured = -not [string]::IsNullOrWhiteSpace($DeviceToken) -or ($tokenCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1)
    $asrProviderForCheck = Resolve-AsrProvider
    if ($asrProviderForCheck -eq "sensevoice") {
        $asrModelPathForCheck = [string]$config.asr_sensevoice_model_path
        $asrModelFileForCheck = "model.int8.onnx"
    } else {
        $asrModelPathForCheck = [string]$config.asr_whisper_model_path
        $asrModelFileForCheck = "model.bin"
    }
    $asrDirectoryForCheck = if ([System.IO.Path]::IsPathRooted($asrModelPathForCheck)) {
        $asrModelPathForCheck
    } else {
        Join-Path $projectRoot $asrModelPathForCheck
    }
    $asrModelProbeForCheck = Join-Path $asrDirectoryForCheck $asrModelFileForCheck
    [ordered]@{
        ready = $true
        project_root = $projectRoot
        python_ready = [bool]$venvPython
        python_runnable = [bool]$interpreterRunnable
        python_path = [string]$venvPython
        host = $hostAddress
        port = $actualPort
        development = [bool]$Development
        data_dir = $DataDir
        database_path = (Join-Path $DataDir "emotional_robot.sqlite3")
        admin_configured = [bool]$adminConfigured
        simulator_token_configured = [bool]$simulatorTokenConfigured
        asr_provider = $asrProviderForCheck
        asr_model_path = $asrModelPathForCheck
        asr_model_present = Test-Path -LiteralPath $asrModelProbeForCheck
        diagnostics = @(
            if (-not $adminConfigured) { "admin_password_required" }
            if (-not $interpreterRunnable) { "python_missing_dependencies:$venvPython" }
            if (-not $simulatorTokenConfigured -and [bool]$config.heartbeat_enabled -and -not $NoHeartbeat) { "simulator_token_required_for_heartbeat" }
            if (-not (Test-Path -LiteralPath $asrModelProbeForCheck)) { "asr_model_missing:$asrProviderForCheck" }
        )
    } | ConvertTo-Json -Compress
    exit 0
}

function Read-EncryptedSecret([string]$Path) {
    $encrypted = Get-Content -Raw -LiteralPath $Path
    $secure = ConvertTo-SecureString $encrypted
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

if ($env:IOT_TESTING -eq "1" -or $env:IOT_TESTING -eq "true") {
    $env:DEEPSEEK_API_KEY = ""
} elseif (-not $SkipSecret -and -not $env:DEEPSEEK_API_KEY) {
    if (-not (Test-Path -LiteralPath $SecretFile)) {
        Write-Host "DeepSeek API key is not configured. Starting first-time setup." -ForegroundColor Yellow
        & (Join-Path $PSScriptRoot "setup-project.ps1") -SkipInstall -SecretFile $SecretFile
    }
    $env:DEEPSEEK_API_KEY = Read-EncryptedSecret $SecretFile
}
if (-not $env:IOT_ADMIN_PASSWORD -and (Test-Path -LiteralPath $AdminSecretFile)) {
    try {
        $env:IOT_ADMIN_PASSWORD = Read-EncryptedSecret $AdminSecretFile
    } catch {
        throw "Unable to decrypt the Dashboard administrator password. Run the first-time setup under the same Windows account."
    }
}
$env:DEEPSEEK_BASE_URL = [string]$config.deepseek_base_url
$env:DEEPSEEK_MODEL = [string]$config.deepseek_model
# The ASR backend selection lives in the tracked launcher config rather than in
# .env (which is gitignored and never created by setup), so a fresh clone runs
# the intended backend instead of silently falling back to the default.
$effectiveAsrProvider = Resolve-AsrProvider
if ($effectiveAsrProvider) { $env:ASR_PROVIDER = $effectiveAsrProvider }
if ($config.asr_sensevoice_model_path) { $env:ASR_SENSEVOICE_MODEL_PATH = [string]$config.asr_sensevoice_model_path }
if ($config.asr_whisper_model_path) { $env:ASR_MODEL_PATH = [string]$config.asr_whisper_model_path }
if ($config.asr_device) { $env:ASR_DEVICE = [string]$config.asr_device }
if ($config.asr_cpu_threads) { $env:ASR_CPU_THREADS = ([int]$config.asr_cpu_threads).ToString() }
if ($null -ne $config.asr_use_itn) { $env:ASR_USE_ITN = $(if ([bool]$config.asr_use_itn) { "1" } else { "0" }) }
$env:IOT_DATA_DIR = $DataDir
$env:DATABASE_PATH = Join-Path $DataDir "emotional_robot.sqlite3"

function Test-Health {
    try {
        $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
        return $response.status -eq "ok"
    } catch {
        return $false
    }
}

function Test-HeartbeatReady([string]$ExpectedRun) {
    try {
        $status = Get-Content -Raw -LiteralPath (Join-Path $LogDir "heartbeat-status.json") | ConvertFrom-Json
        $reportedAt = if ($status.time -is [datetime]) { [DateTimeOffset]$status.time } else { [DateTimeOffset]::Parse([string]$status.time, [Globalization.CultureInfo]::InvariantCulture) }
        $age = ([DateTimeOffset]::UtcNow - $reportedAt).TotalSeconds
        $recent = $age -ge 0 -and $age -lt ([Math]::Max(30, [int]$config.heartbeat_interval_seconds + 15))
        return $status.run_id -eq $ExpectedRun -and $status.state -eq "ready" -and $recent
    } catch { return $false }
}

function Write-HeartbeatFailure([string]$Reason) {
    $diagnostic = [ordered]@{ time = [DateTimeOffset]::UtcNow.ToString("o"); run_id = $runId; reason = $Reason; exit_code = $heartbeatProcess.ExitCode }
    $path = Join-Path $LogDir "startup-error.json"
    [System.IO.File]::WriteAllText($path, ($diagnostic | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Heartbeat startup failed: $Reason. Details: $path" -ForegroundColor Red
    $tail = Get-Content -Tail 8 -LiteralPath (Join-Path $LogDir "heartbeat-error.log") -ErrorAction SilentlyContinue
    if ($tail) { $tail | ForEach-Object { Write-Host $_ } }
    else { Write-Host "No Python stderr was produced. Check startup-error.json for the process exit code." }
}

function Get-PortOwningProcessId([int]$Port) {
    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($connections) {
        return [int]($connections | Select-Object -First 1 -ExpandProperty OwningProcess)
    }
    $netstatLines = & netstat -ano
    $listenerLine = $netstatLines | Where-Object { $_ -match ":${Port}\s" -and $_ -match 'LISTENING' } | Select-Object -First 1
    if ($listenerLine -and $listenerLine -match 'LISTENING\s+(\d+)') {
        return [int]$Matches[1]
    }
    return 0
}

function Test-ProjectServiceProcess([int]$ProcessId) {
    if ($ProcessId -le 0) { return $false }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    $commandLine = [string]$process.CommandLine
    return ($commandLine -match 'uvicorn' -and $commandLine -match 'services\.dialogue\.app:app')
}

$serviceStateFile = Join-Path $RuntimeDir "service.json"
$heartbeatStateFile = Join-Path $RuntimeDir "heartbeat.json"
$lockFile = Join-Path $RuntimeDir "start.lock"
$lockStream = $null
try {
    $lockStream = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    $reuseService = $false
    $startedServiceHere = $false
    $existing = Get-OwnedProcess -StateFile $serviceStateFile -ExpectedRole "service"
    if ($existing -and (Test-Health)) {
        Write-Host "The service is already running: $dashboardUrl" -ForegroundColor Green
        $heartbeatOwned = Get-OwnedProcess -StateFile $heartbeatStateFile -ExpectedRole "heartbeat"
        if ($NoHeartbeat -or (-not [bool]$config.heartbeat_enabled) -or ($heartbeatOwned -and (Test-HeartbeatReady $heartbeatOwned.State.run_id))) {
            if (-not $NoBrowser -and [bool]$config.open_browser) { Start-Process $dashboardUrl }
            exit 0
        }
        if ($heartbeatOwned) { Stop-OwnedProcess -StateFile $heartbeatStateFile -ExpectedRole "heartbeat" | Out-Null }
        # Reuse the verified backend and only repair the missing heartbeat.
        $reuseService = $true
        $serviceProcess = $existing.Process
        $runId = [string]$existing.State.run_id
    } elseif ($existing) {
        Write-Host "Stopping the unresponsive launcher-managed service before restarting." -ForegroundColor Yellow
        Stop-OwnedProcess -StateFile $serviceStateFile -ExpectedRole "service" | Out-Null
        $existing = $null
    }

    if (-not $reuseService) {
        $portOwner = Get-PortOwningProcessId -Port $actualPort
        if ($portOwner -gt 0) {
            if (Test-ProjectServiceProcess -ProcessId $portOwner) {
                Write-Host "Stopping an outdated project service on port ${actualPort} (PID $portOwner)." -ForegroundColor Yellow
                Stop-ProcessTree -TargetProcessId $portOwner
                Start-Sleep -Milliseconds 500
            } else {
                throw "Port ${actualPort} is already in use by another process (PID $portOwner). Close it or change the port in configs\launcher.json."
            }
        }
        $serviceArguments = @(
            "-m", "uvicorn", "services.dialogue.app:app",
            "--host", $hostAddress,
            "--port", $actualPort.ToString()
        )
        if ($Development) { $serviceArguments += "--reload" }
        if ($Development) {
        $serviceProcess = Start-Process -FilePath $venvPython -ArgumentList $serviceArguments -WorkingDirectory $projectRoot -WindowStyle Normal -PassThru
        } else {
            $serviceProcess = Start-Process -FilePath $venvPython -ArgumentList $serviceArguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogDir "service.log") -RedirectStandardError (Join-Path $LogDir "service-error.log") -PassThru
        }
        $startedServiceHere = $true
        $runId = [guid]::NewGuid().ToString("N")
        Write-OwnedProcessState -StateFile $serviceStateFile -Process $serviceProcess -Role "service" -CommandContains "services.dialogue.app:app" -RunId $runId -LogHint (Join-Path $LogDir "service-error.log")

        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline) {
            if (Test-Health) { break }
            if ($serviceProcess.HasExited) {
                Stop-OwnedProcess -StateFile $serviceStateFile -ExpectedRole "service" | Out-Null
                throw "Backend startup failed. Check logs\service-error.log."
            }
            Start-Sleep -Milliseconds 300
        }
        if (-not (Test-Health)) {
            Stop-OwnedProcess -StateFile $serviceStateFile -ExpectedRole "service" | Out-Null
            throw "Backend health check did not pass within 30 seconds: $healthUrl"
        }
    }

$heartbeatEnabled = [bool]$config.heartbeat_enabled -and -not $NoHeartbeat
if ($heartbeatEnabled) {
    $runId = [guid]::NewGuid().ToString("N")
    # Module invocation and explicit argument quoting also work from paths with spaces.
    $heartbeatArguments = @("-u", "-m", "simulator.heartbeat", "--url", "http://${hostAddress}:${actualPort}/api/device/heartbeat", "--device-id", [string]$config.simulator_device_id, "--interval", ([int]$config.heartbeat_interval_seconds).ToString(), "--status-file", (Join-Path $LogDir "heartbeat-status.json"), "--run-id", $runId)
    if ([string]::IsNullOrWhiteSpace($DeviceToken)) {
        # The local helper renews only this device's session, including during long runs.
        $heartbeatArguments += @("--local-data-dir", $DataDir)
    } else {
        # An explicitly supplied token remains caller-managed; never auto-replace it.
        $heartbeatArguments += "--token=$DeviceToken"
    }
    $heartbeatCommand = ($heartbeatArguments | ForEach-Object { Quote-ProcessArgument ([string]$_) }) -join " "
    $heartbeatProcess = Start-Process -FilePath $venvPython -ArgumentList $heartbeatCommand -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogDir "heartbeat.log") -RedirectStandardError (Join-Path $LogDir "heartbeat-error.log") -PassThru
    try {
        Write-OwnedProcessState -StateFile $heartbeatStateFile -Process $heartbeatProcess -Role "heartbeat" -CommandContains "simulator.heartbeat" -RunId $runId -LogHint (Join-Path $LogDir "heartbeat-error.log")
    } catch {
        Write-HeartbeatFailure "process_inspection_failed"
        Stop-ProcessTree -TargetProcessId $heartbeatProcess.Id
        if ($startedServiceHere) { Stop-OwnedProcess -StateFile $serviceStateFile -ExpectedRole "service" | Out-Null }
        throw
    }
    # A live process alone does not prove delivery. Wait for this run's receipt.
    $heartbeatReady = $false
    $heartbeatDeadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $heartbeatDeadline) {
        $heartbeatProcess.Refresh()
        if ($heartbeatProcess.HasExited) { break }
        if (Test-HeartbeatReady $runId) { $heartbeatReady = $true; break }
        Start-Sleep -Milliseconds 200
    }
    if (-not $heartbeatReady) {
        $reason = if ($heartbeatProcess.HasExited) { "process_exited" } else { "no_successful_heartbeat_within_30s" }
        Write-HeartbeatFailure $reason
        Stop-OwnedProcess -StateFile $heartbeatStateFile -ExpectedRole "heartbeat" | Out-Null
        if ($startedServiceHere) { Stop-OwnedProcess -StateFile $serviceStateFile -ExpectedRole "service" | Out-Null }
        throw "Simulator heartbeat did not become ready. See logs\startup-error.json, heartbeat-status.json and heartbeat-error.log."
    }
}

Write-Host "Project started: $dashboardUrl" -ForegroundColor Green
if ($Development) { Write-Host "Development mode is active. Python changes reload the backend automatically." -ForegroundColor Cyan }
if (-not $NoBrowser -and [bool]$config.open_browser) { Start-Process $dashboardUrl }
} finally {
    if ($lockStream) { $lockStream.Dispose() }
}
