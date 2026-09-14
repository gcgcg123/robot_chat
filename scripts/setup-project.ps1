param(
    [switch]$NonInteractive,
    [switch]$SkipInstall,
    [string]$SecretFile,
    [string]$AdminSecretFile,
    [switch]$SkipAdmin
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $SecretFile) {
    $SecretFile = Join-Path $projectRoot "data\secrets\deepseek.key"
}
if (-not $AdminSecretFile) {
    $AdminSecretFile = Join-Path $projectRoot "data\secrets\admin.password"
}

$secretDirectory = Split-Path -Parent $SecretFile
New-Item -ItemType Directory -Force -Path $secretDirectory | Out-Null

if (-not $SkipInstall) {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($pythonLauncher) {
            foreach ($selector in @("-3.13", "-3.10", "-3")) {
                & $pythonLauncher.Source $selector -m venv (Join-Path $projectRoot ".venv")
                if (Test-Path -LiteralPath $venvPython) { break }
            }
        }
        if (-not (Test-Path -LiteralPath $venvPython)) {
            $systemPython = Get-Command python -ErrorAction Stop
            & $systemPython.Source -m venv (Join-Path $projectRoot ".venv")
        }
    }
    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw "Unable to create the Python virtual environment. Install Python 3.10 or newer first."
    }
    & $venvPython -m pip install -r (Join-Path $projectRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Python dependency installation failed."
    }
}

$plainKey = $env:DEEPSEEK_API_KEY
if ($plainKey) {
    if ([string]::IsNullOrWhiteSpace($plainKey)) { throw "The API key cannot be empty." }
    $secureKey = ConvertTo-SecureString $plainKey.Trim() -AsPlainText -Force
} elseif ($NonInteractive) {
    throw "NonInteractive mode requires the DEEPSEEK_API_KEY environment variable."
} else {
    Write-Host "Enter a new DeepSeek API key. The input will be hidden:" -ForegroundColor Cyan
    $secureKey = Read-Host -AsSecureString
}

$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $validatedKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if ([string]::IsNullOrWhiteSpace($validatedKey)) { throw "The API key cannot be empty." }
} finally {
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    $validatedKey = $null
    $plainKey = $null
}

$encryptedKey = ConvertFrom-SecureString $secureKey
if (-not $encryptedKey) {
    throw "The API key cannot be empty."
}
[System.IO.File]::WriteAllText(
    $SecretFile,
    $encryptedKey,
    (New-Object System.Text.UTF8Encoding($false))
)

if (-not $SkipAdmin) {
    # The admin password is deliberately independent from the API key.  In
    # non-interactive CI/setup calls it is optional so existing automation can
    # configure the password through IOT_ADMIN_PASSWORD at launch time.
    $adminPassword = $env:IOT_ADMIN_PASSWORD
    if ([string]::IsNullOrWhiteSpace($adminPassword) -and -not $NonInteractive) {
        Write-Host "Enter the Dashboard administrator password. The input will be hidden:" -ForegroundColor Cyan
        $adminSecure = Read-Host -AsSecureString
    } elseif (-not [string]::IsNullOrWhiteSpace($adminPassword)) {
        $adminSecure = ConvertTo-SecureString $adminPassword.Trim() -AsPlainText -Force
    }
    if ($adminSecure) {
        $adminDirectory = Split-Path -Parent $AdminSecretFile
        New-Item -ItemType Directory -Force -Path $adminDirectory | Out-Null
        $encryptedAdmin = ConvertFrom-SecureString $adminSecure
        [System.IO.File]::WriteAllText(
            $AdminSecretFile,
            $encryptedAdmin,
            (New-Object System.Text.UTF8Encoding($false))
        )
        Write-Host "Dashboard administrator password configured with Windows DPAPI." -ForegroundColor Green
        $adminPassword = $null
    } else {
        Write-Host "Dashboard administrator password is not configured. Set IOT_ADMIN_PASSWORD or run setup again interactively." -ForegroundColor Yellow
    }
}

Write-Host "Setup complete. The DeepSeek key is encrypted with Windows DPAPI." -ForegroundColor Green
Write-Host "You can now double-click the one-click start batch file."
