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
$configPath = Join-Path $projectRoot "configs\launcher.json"
$config = Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
$hostAddress = [string]$config.host
$actualPort = if ($Port -gt 0) { $Port } else { [int]$config.port }
if (-not $RuntimeDir) { $RuntimeDir = Join-Path $projectRoot "runtime" }
if (-not $LogDir) { $LogDir = Join-Path $projectRoot "logs" }
if (-not $SecretFile) { $SecretFile = Join-Path $projectRoot "data\secrets\deepseek.key" }
if (-not $AdminSecretFile) { $AdminSecretFile = Join-Path $projectRoot "data\secrets\admin.password" }
if (-not $DataDir) { $DataDir = if ($env:IOT_DATA_DIR) { $env:IOT_DATA_DIR } elseif ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "IoTGroup5" } else { Join-Path $env:USERPROFILE "AppData\Local\IoTGroup5" } }
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$dashboardUrl = "http://${hostAddress}:${actualPort}/dashboard"
$healthUrl = "http://${hostAddress}:${actualPort}/health"

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Virtual environment not found. Run the first-time setup batch file."
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
    [ordered]@{
        ready = $true
        project_root = $projectRoot
        python_ready = (Test-Path -LiteralPath $venvPython)
        host = $hostAddress
        port = $actualPort
        development = [bool]$Development
        data_dir = $DataDir
        database_path = (Join-Path $DataDir "emotional_robot.sqlite3")
        admin_configured = [bool]$adminConfigured
        simulator_token_configured = [bool]$simulatorTokenConfigured
        diagnostics = @(
            if (-not $adminConfigured) { "admin_password_required" }
            if (-not $simulatorTokenConfigured -and [bool]$config.heartbeat_enabled -and -not $NoHeartbeat) { "simulator_token_required_for_heartbeat" }
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
        if ($NoHeartbeat -or $heartbeatOwned) {
            if (-not $NoBrowser -and [bool]$config.open_browser) { Start-Process $dashboardUrl }
            exit 0
        }
        # Reuse the verified backend and only repair the missing heartbeat.
        $reuseService = $true
        $serviceProcess = $existing.Process
        $runId = [string]$existing.State.run_id
    }

    if (-not $reuseService) {
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
        Write-OwnedProcessState -StateFile $serviceStateFile -Process $serviceProcess -Role "service" -CommandContains "services.dialogue.app:app" -RunId $runId

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
    if ([string]::IsNullOrWhiteSpace($DeviceToken)) {
        $tokenCandidates = @(
            (Join-Path $DataDir "secrets\simulator.token"),
            (Join-Path $DataDir ("secrets\simulator-{0}.token" -f [string]$config.simulator_device_id))
        )
        foreach ($candidate in $tokenCandidates) {
            if (Test-Path -LiteralPath $candidate) {
                $DeviceToken = (Get-Content -Raw -LiteralPath $candidate).Trim()
                break
            }
        }
    }
    if ([string]::IsNullOrWhiteSpace($DeviceToken)) {
        if ($startedServiceHere) { Stop-OwnedProcess -StateFile $serviceStateFile -ExpectedRole "service" | Out-Null }
        throw "Simulator heartbeat is enabled but no device token is configured. Run scripts\provision-simulator.ps1 -DataDir '$DataDir' -DeviceId '$([string]$config.simulator_device_id)' or pass -DeviceToken."
    }
    $heartbeatScript = Join-Path $projectRoot "simulator\heartbeat.py"
    # Start-Process quotes each ArgumentList item as needed.  Supplying a
    # pre-quoted path here causes PowerShell 5.1 to retain literal quotes and
    # makes argparse treat the script invocation as malformed.
    $heartbeatArguments = @($heartbeatScript, "--url", "http://${hostAddress}:${actualPort}/api/device/heartbeat", "--device-id", [string]$config.simulator_device_id, "--interval", ([int]$config.heartbeat_interval_seconds).ToString())
    # Use the equals form so a URL-safe token beginning with '-' cannot be
    # mistaken for another argparse option.
    if ($DeviceToken) { $heartbeatArguments += "--token=$DeviceToken" }
    $heartbeatProcess = Start-Process -FilePath $venvPython -ArgumentList $heartbeatArguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogDir "heartbeat.log") -RedirectStandardError (Join-Path $LogDir "heartbeat-error.log") -PassThru
    try {
        Write-OwnedProcessState -StateFile $heartbeatStateFile -Process $heartbeatProcess -Role "heartbeat" -CommandContains "heartbeat.py" -RunId $runId
    } catch {
        Stop-ProcessTree -TargetProcessId $heartbeatProcess.Id
        if ($startedServiceHere) { Stop-OwnedProcess -StateFile $serviceStateFile -ExpectedRole "service" | Out-Null }
        throw
    }
    # heartbeat.py sends its first request immediately.  An exited child here
    # means authentication/transport failed; do not report a false "started"
    # state or leave an orphan backend behind.
    for ($probe = 0; $probe -lt 15; $probe++) {
        if ($heartbeatProcess.HasExited) { break }
        Start-Sleep -Milliseconds 200
    }
    if ($heartbeatProcess.HasExited) {
        Remove-Item -LiteralPath $heartbeatStateFile -Force -ErrorAction SilentlyContinue
        if ($startedServiceHere) { Stop-OwnedProcess -StateFile $serviceStateFile -ExpectedRole "service" | Out-Null }
        throw "Simulator heartbeat failed during startup. Check logs\heartbeat-error.log and provision a valid device token."
    }
}

Write-Host "Project started: $dashboardUrl" -ForegroundColor Green
if ($Development) { Write-Host "Development mode is active. Python changes reload the backend automatically." -ForegroundColor Cyan }
if (-not $NoBrowser -and [bool]$config.open_browser) { Start-Process $dashboardUrl }
} finally {
    if ($lockStream) { $lockStream.Dispose() }
}
