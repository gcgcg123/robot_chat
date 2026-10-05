param(
    [switch]$NonInteractive,
    [switch]$SkipInstall,
    [string]$SecretFile,
    [string]$AdminSecretFile,
    [switch]$SkipAdmin
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "launcher-common.ps1")

function ConvertTo-SecureText([string]$PlainText) {
    <#
        Wrapping these two calls keeps the DPAPI error-handling policy in one
        place and matches Read-DpapiSecret in launcher-common.ps1: Windows
        PowerShell can record a non-terminating error from the DPAPI layer while
        the conversion itself is fine, and this script runs with
        $ErrorActionPreference = "Stop".  Relax the preference for the call so a
        stray record cannot abort first-time setup.
    #>
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        return ConvertTo-SecureString $PlainText -AsPlainText -Force
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
}

function ConvertTo-EncryptedText([System.Security.SecureString]$Secure) {
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        return ConvertFrom-SecureString $Secure
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
}
if (-not $SecretFile) {
    $SecretFile = Join-Path $projectRoot "data\secrets\deepseek.key"
}
if (-not $AdminSecretFile) {
    $AdminSecretFile = Join-Path $projectRoot "data\secrets\admin.password"
}

$secretDirectory = Split-Path -Parent $SecretFile
New-Item -ItemType Directory -Force -Path $secretDirectory | Out-Null

if (-not $SkipInstall) {
    $venvPython = Resolve-ProjectPython -ProjectRoot $projectRoot
    & $venvPython -m pip install -r (Join-Path $projectRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Python dependency installation failed."
    }
}

$plainKey = $env:DEEPSEEK_API_KEY
if ($plainKey) {
    if ([string]::IsNullOrWhiteSpace($plainKey)) { throw "The API key cannot be empty." }
    $secureKey = ConvertTo-SecureText $plainKey.Trim()
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

$encryptedKey = ConvertTo-EncryptedText $secureKey
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
        $adminSecure = ConvertTo-SecureText $adminPassword.Trim()
    }
    if ($adminSecure) {
        # Read-Host -AsSecureString returns an empty SecureString when the
        # user presses Enter without entering a password.  PowerShell's
        # ConvertFrom-SecureString rejects that value, so validate it first
        # and provide a clear retry message.
        $adminBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($adminSecure)
        try {
            $adminPlainLength = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($adminBstr).Length
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($adminBstr)
        }
        if ($adminPlainLength -lt 1) {
            throw "Dashboard administrator password cannot be empty. Run setup again and enter a password."
        }
        $adminDirectory = Split-Path -Parent $AdminSecretFile
        New-Item -ItemType Directory -Force -Path $adminDirectory | Out-Null
        $encryptedAdmin = ConvertTo-EncryptedText $adminSecure
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
