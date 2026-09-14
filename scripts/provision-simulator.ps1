param(
    [Parameter(Mandatory = $true)]
    [string]$DataDir,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$DeviceId,
    [string]$TokenFile
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $TokenFile) { $TokenFile = Join-Path $DataDir "secrets\simulator.token" }
$tokenDirectory = Split-Path -Parent $TokenFile
New-Item -ItemType Directory -Force -Path $tokenDirectory | Out-Null

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { throw "Virtual environment not found and python is unavailable. Run 一鍵安裝並啟動.bat first." }
    $venvPython = $pythonCommand.Source
}

# Keep all provisioning inputs in the child environment.  The bearer token is
# captured by PowerShell and written to the local token file, never echoed.
$env:IOT_DATA_DIR = (Resolve-Path -LiteralPath $DataDir -ErrorAction SilentlyContinue)
if (-not $env:IOT_DATA_DIR) {
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    $env:IOT_DATA_DIR = (Resolve-Path -LiteralPath $DataDir).Path
}
$env:IOT_PROVISION_DEVICE_ID = $DeviceId
$pythonCode = @'
import os, time
from services.storage.database import open_database
from services.storage.migrations import migrate
from services.security.auth import create_session
p = os.environ["IOT_DATA_DIR"]
d = os.environ["IOT_PROVISION_DEVICE_ID"]
with open_database(os.path.join(p, "emotional_robot.sqlite3")) as c:
    migrate(c)
    c.execute("UPDATE sessions SET revoked_at=? WHERE role='device' AND device_id=? AND revoked_at IS NULL", (time.time(), d))
    c.commit()
    print(create_session(c, d, "device", device_id=d))
'@
$pythonFile = Join-Path $tokenDirectory "provision_token_tmp.py"
[System.IO.File]::WriteAllText($pythonFile, $pythonCode, (New-Object System.Text.UTF8Encoding($false)))
try {
    # Running a temporary file avoids PowerShell/native quoting differences
    # that can strip SQL string quotes when passing a multiline -c argument.
    $previousPythonPath = $env:PYTHONPATH
    if ($previousPythonPath) { $env:PYTHONPATH = "$projectRoot;$previousPythonPath" }
    else { $env:PYTHONPATH = $projectRoot }
    $token = (& $venvPython $pythonFile)
} finally {
    $env:PYTHONPATH = $previousPythonPath
    Remove-Item -LiteralPath $pythonFile -Force -ErrorAction SilentlyContinue
}
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($token -join ""))) {
    throw "Simulator token provisioning failed."
}
$tokenValue = ($token -join "").Trim()
[System.IO.File]::WriteAllText($TokenFile, $tokenValue, (New-Object System.Text.UTF8Encoding($false)))
try {
    $acl = Get-Acl -LiteralPath $TokenFile
    $acl.SetAccessRuleProtection($true, $false)
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($identity, "FullControl", "Allow")
    $acl.SetAccessRule($rule)
    Set-Acl -LiteralPath $TokenFile -AclObject $acl
} catch {
    # DPAPI/ACL hardening is best effort on non-Windows PowerShell hosts; the
    # token still remains outside source control and is never printed.
}
Write-Host "Simulator token provisioned for device '$DeviceId'. Token file: $TokenFile" -ForegroundColor Green
