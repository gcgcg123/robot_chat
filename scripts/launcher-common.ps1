function Get-ProcessSnapshot([int]$TargetProcessId) {
    $process = Get-Process -Id $TargetProcessId -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $TargetProcessId" -ErrorAction SilentlyContinue
    if (-not $cim) { return $null }
    return [pscustomobject]@{
        Process = $process
        StartTimeTicks = $process.StartTime.ToUniversalTime().Ticks
        Executable = [string]$cim.ExecutablePath
        CommandLine = [string]$cim.CommandLine
    }
}

function Write-OwnedProcessState(
    [string]$StateFile,
    [System.Diagnostics.Process]$Process,
    [string]$Role,
    [string]$CommandContains,
    [string]$RunId
) {
    $snapshot = $null
    for ($attempt = 0; $attempt -lt 10 -and -not $snapshot; $attempt++) {
        if ($Process.HasExited) { break }
        $snapshot = Get-ProcessSnapshot -TargetProcessId $Process.Id
        if (-not $snapshot) { Start-Sleep -Milliseconds 200 }
    }
    if (-not $snapshot) { throw "Unable to inspect the newly started $Role process." }
    $state = [ordered]@{
        pid = $Process.Id
        start_time_ticks = $snapshot.StartTimeTicks
        executable = $snapshot.Executable
        role = $Role
        command_contains = $CommandContains
        run_id = $RunId
    }
    $temporary = "$StateFile.tmp-$RunId"
    [System.IO.File]::WriteAllText($temporary, ($state | ConvertTo-Json -Compress), (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $StateFile -Force
}

function Get-OwnedProcess([string]$StateFile, [string]$ExpectedRole) {
    if (-not (Test-Path -LiteralPath $StateFile)) { return $null }
    try {
        $state = Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
        $targetId = 0
        if (-not [int]::TryParse([string]$state.pid, [ref]$targetId)) { throw "Invalid PID" }
        if ([string]$state.role -ne $ExpectedRole) { throw "Role mismatch" }
        $snapshot = Get-ProcessSnapshot -TargetProcessId $targetId
        if (-not $snapshot) { throw "Process is no longer running" }
        if ([long]$state.start_time_ticks -ne [long]$snapshot.StartTimeTicks) { throw "Process start time mismatch" }
        if ([string]$state.executable -ne [string]$snapshot.Executable) { throw "Executable mismatch" }
        if (-not $snapshot.CommandLine -or $snapshot.CommandLine.IndexOf([string]$state.command_contains, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw "Command line mismatch"
        }
        return [pscustomobject]@{ Process = $snapshot.Process; State = $state }
    } catch {
        Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
        return $null
    }
}

function Stop-ProcessTree([int]$TargetProcessId) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $TargetProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -TargetProcessId ([int]$child.ProcessId)
    }
    Stop-Process -Id $TargetProcessId -Force -ErrorAction SilentlyContinue
}

function Stop-OwnedProcess([string]$StateFile, [string]$ExpectedRole) {
    $owned = Get-OwnedProcess -StateFile $StateFile -ExpectedRole $ExpectedRole
    if (-not $owned) { return $false }
    Stop-ProcessTree -TargetProcessId $owned.Process.Id
    Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
    return $true
}

function Quote-ProcessArgument([string]$Value) {
    return '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}
