param([string]$BaseUrl = "http://127.0.0.1:8080", [string]$ActorId = "admin", [string]$Password, [switch]$SkipLogin)
$ErrorActionPreference = "Stop"
$BaseUrl = $BaseUrl.TrimEnd('/')
function Get-Json([string]$Path) { Invoke-RestMethod -Uri ($BaseUrl + $Path) -TimeoutSec 5 }
$health = Get-Json "/health"
if ($health.status -ne "ok") { throw "health_check_failed" }
$dashboard = Invoke-WebRequest -Uri ($BaseUrl + "/dashboard") -UseBasicParsing -TimeoutSec 5
if ($dashboard.StatusCode -ne 200) { throw "dashboard_check_failed" }
$result = [ordered]@{ health = $true; dashboard = $true; login = $false; users = $false; simulator = $false }
if (-not $SkipLogin) {
    if ([string]::IsNullOrWhiteSpace($Password)) { throw "Password is required unless -SkipLogin is used." }
    $session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $login = Invoke-RestMethod -Method Post -Uri ($BaseUrl + "/api/auth/login") -WebSession $session -ContentType "application/json" -Body (@{actor_id=$ActorId;password=$Password}|ConvertTo-Json)
    if (-not $login.ok) { throw "login_check_failed" }
    $result.login = $true
    $sessionInfo = Invoke-RestMethod -Uri ($BaseUrl + "/api/auth/session") -WebSession $session
    $users = Invoke-RestMethod -Uri ($BaseUrl + "/api/users") -WebSession $session
    if ($null -eq $users.items) { throw "users_api_check_failed" }
    $result.users = $true
    $sim = Invoke-RestMethod -Method Post -Uri ($BaseUrl + "/api/simulator/sessions") -WebSession $session -Headers @{"X-CSRF-Token" = $sessionInfo.csrf_token} -ContentType "application/json" -Body "{}"
    if (-not $sim.session_id) { throw "simulator_session_check_failed" }
    $result.simulator = $true
}
$result | ConvertTo-Json -Compress
exit 0
