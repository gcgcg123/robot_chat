param(
    [string]$RuntimeDir
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "launcher-common.ps1")
if (-not $RuntimeDir) { $RuntimeDir = Join-Path $projectRoot "runtime" }

$stopped = 0
foreach ($name in @("heartbeat", "service")) {
    $stateFile = Join-Path $RuntimeDir "$name.json"
    if (-not (Test-Path -LiteralPath $stateFile)) { continue }
    if (Stop-OwnedProcess -StateFile $stateFile -ExpectedRole $name) {
        $stopped += 1
    }
}

if ($stopped -gt 0) {
    Write-Host "The emotional companion services have stopped." -ForegroundColor Green
} else {
    Write-Host "No launcher-managed services are currently running." -ForegroundColor Yellow
}
