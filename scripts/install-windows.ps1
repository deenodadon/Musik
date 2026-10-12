# Installs Musik per user. Run from PowerShell on Windows 10/11.
[CmdletBinding()]
param([string]$InstallDirectory = "$env:LOCALAPPDATA\Programs\Musik", [switch]$SkipDependencies)
$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed (exit $LASTEXITCODE)." }
}

function Get-NodeMajor {
    $nodeVersion = & node --version
    if ($LASTEXITCODE -eq 0 -and $nodeVersion -match '^v(\d+)\.') {
        return [int]$Matches[1]
    }
    return 0
}

# Include changes from prior installers before deciding whether Node is needed.
$env:PATH += ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not $SkipDependencies) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'Install App Installer (winget), or install Python 3.12+, Git, mpv and Node.js 22+ manually, then use -SkipDependencies.'
    }
    foreach ($package in @('Python.Python.3.12', 'Git.Git', 'shinchiro.mpv', 'OpenJS.NodeJS.LTS')) {
        if ($package -eq 'OpenJS.NodeJS.LTS' -and (Get-Command node -ErrorAction SilentlyContinue)) {
            $nodeMajor = Get-NodeMajor
            if ($nodeMajor -ge 22) { continue }
        }
        & winget install --exact --id $package --accept-source-agreements --accept-package-agreements --disable-interactivity
        # WinGet returns this code when a package is already current.
        if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) {
            throw "Could not install $package. Install it manually, then rerun with -SkipDependencies."
        }
    }
}
# See PATH changes from installers without making the user reopen PowerShell.
$env:PATH = $env:PATH + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw 'Git is missing from PATH.' }
$mpvCommand = Get-Command mpv -ErrorAction SilentlyContinue
if (-not $mpvCommand) {
    $mpvPath = $null
    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if (-not $root) { continue }
        foreach ($folder in @('MPV Player', 'mpv', 'Programs\MPV Player', 'Programs\mpv')) {
            $candidate = Join-Path (Join-Path $root $folder) 'mpv.exe'
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $mpvPath = $candidate; break }
        }
        if ($mpvPath) { break }
    }
    if (-not $mpvPath) {
        foreach ($key in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths\mpv.exe', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\mpv.exe')) {
            if (Test-Path -LiteralPath $key) {
                $candidate = ([string]((Get-Item -LiteralPath $key).GetValue(''))).Trim('"')
                if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { $mpvPath = $candidate; break }
            }
        }
    }
    if (-not $mpvPath) { throw 'mpv installation was not found. Run winget install --exact --id shinchiro.mpv, then rerun this installer.' }
    $env:PATH += ';' + (Split-Path -Parent $mpvPath)
    Write-Host "Found mpv at $mpvPath"
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js 22+ is missing from PATH.' }
$nodeMajor = Get-NodeMajor
if ($nodeMajor -lt 22) { throw 'Node.js 22+ is required. Upgrade Node.js, then rerun this installer.' }
$python = Get-Command py -ErrorAction SilentlyContinue
if ($python) { $pythonExe = $python.Source; $pythonPrefix = @('-3.12') }
else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) { throw 'Python is missing from PATH.' }
    $pythonExe = $python.Source; $pythonPrefix = @()
}
New-Item -ItemType Directory -Force -Path $InstallDirectory | Out-Null
$source = Join-Path $InstallDirectory 'source'
if (-not (Test-Path $source)) {
    Invoke-Checked 'git' @('clone', '--branch', 'main', 'https://github.com/deenodadon/Musik.git', $source)
} else {
    if (-not (Test-Path (Join-Path $source '.git'))) { throw 'Source directory already exists and is not a Git checkout.' }
    $origin = & git -C $source remote get-url origin
    if ($origin.TrimEnd('/') -ne 'https://github.com/deenodadon/Musik.git') { throw 'Source checkout has a different origin; choose another install directory.' }
    $changes = & git -C $source status --porcelain
    if ($changes) { throw 'Source checkout contains local changes. Preserve them before reinstalling.' }
    Invoke-Checked 'git' @('-C', $source, 'pull', '--ff-only', 'origin', 'main')
}
$venv = Join-Path $InstallDirectory 'venv'
Invoke-Checked $pythonExe ($pythonPrefix + @('-m', 'venv', $venv))
$venvPython = Join-Path $venv 'Scripts\python.exe'
Invoke-Checked $venvPython @('-m', 'pip', 'install', '--upgrade', $source)
& $venvPython -I -m musik.app setup-playback
if ($LASTEXITCODE -ne 0) { Write-Warning 'Musik is installed, but playback-token setup failed. Retry musik setup-playback for affected YouTube streams.' }
$bin = Join-Path $InstallDirectory 'bin'
New-Item -ItemType Directory -Force -Path $bin | Out-Null
# The launcher remains stable while the updater replaces the installed package.
$launcher = Join-Path $bin 'musik.cmd'
@('@echo off', '"%~dp0..\venv\Scripts\python.exe" -I -m musik.app %*', 'exit /b %errorlevel%') | Set-Content -Path $launcher -Encoding ASCII
$userPath = [string][Environment]::GetEnvironmentVariable('Path', 'User')
if ($bin -notin ($userPath -split ';')) {
    [Environment]::SetEnvironmentVariable('Path', (($userPath.TrimEnd(';') + ';' + $bin).TrimStart(';')), 'User')
}
$env:PATH += ';' + $bin
Write-Host 'Musik installed. Run musik, musik login, or musik stop.'
