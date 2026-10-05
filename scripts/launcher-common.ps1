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

function Get-DotEnvValue([string]$ProjectRoot, [string]$Name) {
    <#
        Read one key from the project's .env.

        .env is the machine-local, gitignored configuration (bootstrap creates it from
        .env.example), so it is the right home for a value that must not be committed.
        IOT_PYTHON is exactly that: which interpreter has this project's dependencies.
        Without it the resolver falls through to whatever "python" PATH names, which
        on a machine whose project environment is a conda env is a Python that cannot
        import uvicorn at all.
    #>
    $path = Join-Path $ProjectRoot ".env"
    if (-not (Test-Path -LiteralPath $path)) { return "" }
    foreach ($line in [System.IO.File]::ReadAllLines($path)) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        $separator = $trimmed.IndexOf("=")
        if ($separator -lt 1) { continue }
        if ($trimmed.Substring(0, $separator).Trim() -ne $Name) { continue }
        return $trimmed.Substring($separator + 1).Trim().Trim('"').Trim("'")
    }
    return ""
}

function Get-LogTail([string]$Path, [int]$Lines = 3) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return "" }
    $tail = @(Get-Content -LiteralPath $Path -Tail $Lines -ErrorAction SilentlyContinue | Where-Object { $_ -and $_.Trim() })
    if (-not $tail) { return "" }
    return " Last lines of $(Split-Path -Leaf $Path): " + ($tail -join " | ")
}

function Write-OwnedProcessState(
    [string]$StateFile,
    [System.Diagnostics.Process]$Process,
    [string]$Role,
    [string]$CommandContains,
    [string]$RunId,
    [string]$LogHint = ""
) {
    $snapshot = $null
    for ($attempt = 0; $attempt -lt 10 -and -not $snapshot; $attempt++) {
        if ($Process.HasExited) { break }
        $snapshot = Get-ProcessSnapshot -TargetProcessId $Process.Id
        if (-not $snapshot) { Start-Sleep -Milliseconds 200 }
    }
    if (-not $snapshot) {
        # Either the process died at once or it never became inspectable. Saying which
        # log to read turns an opaque failure into an actionable one.
        throw "Unable to inspect the newly started $Role process (it exited immediately or could not be inspected).$(Get-LogTail $LogHint)"
    }
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
    $process = Get-Process -Id $TargetProcessId -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -InputObject $process -Force -ErrorAction SilentlyContinue
        if (-not $process.WaitForExit(5000)) { throw "Timed out stopping process $TargetProcessId." }
    }
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

function Resolve-ProjectPythonDetail {
    <#
        Resolve the project interpreter and report WHERE the choice came from.

        The source matters to the installer. A path that came from PATH is a guess
        about the machine, not an operator decision, so bootstrap-project.ps1 may
        replace it with a project-local .venv. A path that came from IOT_PYTHON --
        environment or .env -- is explicit intent and is never replaced, which is how
        a conda-based checkout keeps working.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot
    )

    $candidates = @()
    if ($env:IOT_PYTHON) { $candidates += @{ Path = $env:IOT_PYTHON; Source = "env" } }
    $dotenvPython = Get-DotEnvValue -ProjectRoot $ProjectRoot -Name "IOT_PYTHON"
    if ($dotenvPython) { $candidates += @{ Path = $dotenvPython; Source = "dotenv" } }

    $configPath = Join-Path $ProjectRoot "configs\launcher.json"
    if (Test-Path -LiteralPath $configPath) {
        $configured = [string](Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json).python
        if (-not [string]::IsNullOrWhiteSpace($configured)) {
            if ([System.IO.Path]::IsPathRooted($configured)) { $candidates += @{ Path = $configured; Source = "config" } }
            else { $candidates += @{ Path = (Join-Path $ProjectRoot $configured); Source = "config" } }
        }
    }

    $candidates += @{ Path = (Join-Path $ProjectRoot ".venv\Scripts\python.exe"); Source = "venv" }
    foreach ($candidate in $candidates) {
        if ($candidate.Path -and (Test-Path -LiteralPath $candidate.Path)) { return $candidate }
    }

    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { return @{ Path = $command.Source; Source = "path" } }
    return @{ Path = ""; Source = "none" }
}

function Resolve-ProjectPython {
    <#
        Locate the interpreter that runs the project, in priority order:

          1. IOT_PYTHON (process environment -- explicit operator intent)
          2. IOT_PYTHON in the project's .env (machine-local and gitignored, which is
             how a machine whose project environment is a conda env names it without
             committing a machine path)
          3. the "python" entry in configs/launcher.json
          4. <project>\.venv\Scripts\python.exe
          5. "python" on PATH

        Hardcoding one machine's interpreter path made the documented one-click
        start fail everywhere else, so every launcher script resolves it here.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [switch]$Quiet
    )

    # Delegates to Resolve-ProjectPythonDetail: this is the flat "give me a path" entry point the
    # launchers call, while Detail is what bootstrap needs to know *where* the answer came from (a
    # PATH guess means it should build .venv instead of installing into it).
    $resolved = Resolve-ProjectPythonDetail -ProjectRoot $ProjectRoot
    if ($resolved.Path) { return $resolved.Path }
    if ($Quiet) { return $null }
    throw "No Python interpreter found. Install Python 3.10 or newer and double-click 一鍵安裝並啟動.bat, set IOT_PYTHON (in .env or the environment), or fill in 'python' in configs\launcher.json."
}

function Read-DpapiSecret {
    <#
        Read a ConvertFrom-SecureString file and return its plaintext.

        Both launchers need this, and both need it to behave the same way, so it
        lives here instead of being copied into each script.

        Two traps this closes, both of which produced a baffling error message
        that pointed at the decryption call instead of the actual cause:

        1. Windows DPAPI binds the ciphertext to ONE Windows account on ONE
           machine.  A checkout copied to another machine, or restored under a
           different profile, holds blobs the current account can never read.
           The provider answers "The data is invalid." / "Key not valid for use
           in specified state.".

        2. When ConvertTo-SecureString cannot unprotect the blob it writes a
           NON-terminating error and returns $null rather than throwing.  Under
           $ErrorActionPreference = "Stop" that record aborts the launcher with
           no useful context, and if it does not abort, the $null travels on and
           fails later somewhere unrelated (SecureStringToBSTR on a null).

        The preference is relaxed around the call so a genuine success is never
        killed by a stray record, and the $null result is checked explicitly so a
        genuine failure is reported here, with the path and the fix.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Cannot read the secret '$Path': the file does not exist."
    }
    $encrypted = (Get-Content -Raw -LiteralPath $Path)
    if ([string]::IsNullOrWhiteSpace($encrypted)) {
        throw "Cannot read the secret '$Path': the file is empty. Delete it and run the first-time setup again."
    }

    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $secure = $null
    try {
        $secure = ConvertTo-SecureString $encrypted
    } catch {
        $secure = $null
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($null -eq $secure) {
        throw "Cannot decrypt '$Path'. Windows DPAPI encrypted this file under a different Windows account or on a different machine, so the current account cannot read it. Delete the file and run scripts\setup-project.ps1 again to re-encrypt it here."
    }

    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

