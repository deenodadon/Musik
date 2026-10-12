# Removes only Musik. Shared prerequisites and personal music are preserved.
[CmdletBinding()]
param([string]$InstallDirectory = "$env:LOCALAPPDATA\Programs\Musik", [switch]$KeepData)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($InstallDirectory).TrimEnd('\')
$bin = Join-Path $root 'bin'
$python = Join-Path $root 'venv\Scripts\python.exe'

function Remove-MusikPath {
    param([string]$Value)
    $kept = foreach ($entry in ($Value -split ';')) {
        if (-not $entry) { continue }
        try {
            $expanded = [Environment]::ExpandEnvironmentVariables($entry.Trim().Trim('"'))
            $normalized = [IO.Path]::GetFullPath($expanded).TrimEnd('\')
        } catch { $normalized = $entry }
        if ($normalized -ine $bin) { $entry }
    }
    return ($kept -join ';')
}

if (Test-Path -LiteralPath $root) {
    foreach ($broadPath in @([IO.Path]::GetPathRoot($root), $env:USERPROFILE, $env:LOCALAPPDATA, "$env:LOCALAPPDATA\Programs")) {
        if ($root -ieq $broadPath.TrimEnd('\')) { throw 'Refusing to remove a broad parent directory.' }
    }
    if (-not (Test-Path -LiteralPath $python)) { throw 'No Musik virtual environment found. Installation was not removed.' }
    & $python -I -c "from importlib.metadata import version; version('musik-tui')"
    if ($LASTEXITCODE -ne 0) { throw 'This directory is not a recognized Musik installation.' }
    if ($KeepData) {
        & $python -I -m musik.app stop
        if ($LASTEXITCODE -ne 0) { throw 'Playback could not be stopped; installation was not removed.' }
        $checkUpdate = @'
from musik.core import Store
from musik.platform import lock_exclusive
store = Store()
store.directory.mkdir(parents=True, exist_ok=True)
with (store.directory / 'update.lock').open('a+') as handle:
    lock_exclusive(handle)
'@
        & $python -I -c $checkUpdate
        if ($LASTEXITCODE -ne 0) { throw 'An update is running. Wait for it to finish and retry.' }
    } else {
        & $python -I -m musik.app reset
        if ($LASTEXITCODE -ne 0) { throw 'Reset failed. Wait for any update to finish and retry; installation was not removed.' }
    }
    $source = Join-Path $root 'source'
    if (Test-Path -LiteralPath $source) {
        $preserve = $true
        if ((Get-Command git -ErrorAction SilentlyContinue) -and (Test-Path (Join-Path $source '.git'))) {
            $changes = & git -C $source status --porcelain --untracked-files=all
            $statusExit = $LASTEXITCODE
            $localCommits = & git -C $source rev-list --count --branches --not --remotes
            $commitExit = $LASTEXITCODE
            $ignored = & git -C $source ls-files --others --ignored --exclude-standard
            $ignoredExit = $LASTEXITCODE
            $unknownIgnored = $ignored | Where-Object { $_ -notmatch '^(build/|dist/|[^/]+\.egg-info/|(?:.*/)?__pycache__/|\.pytest_cache/)' }
            $preserve = ($statusExit -ne 0 -or $commitExit -ne 0 -or $ignoredExit -ne 0 -or $changes -or $unknownIgnored -or $localCommits -ne '0')
        }
        if ($preserve) {
            $backup = $root + '-source-backup-' + [Guid]::NewGuid().ToString('N')
            Move-Item -LiteralPath $source -Destination $backup
            Write-Host "Source changes preserved at $backup"
        } else { Remove-Item -LiteralPath $source -Recurse -Force }
    }
    foreach ($component in @('bin', 'venv')) {
        $path = Join-Path $root $component
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
    if (-not (Get-ChildItem -LiteralPath $root -Force)) { Remove-Item -LiteralPath $root -Force }
    else { Write-Host "Other files left unchanged at $root" }
}
$userPath = [string][Environment]::GetEnvironmentVariable('Path', 'User')
[Environment]::SetEnvironmentVariable('Path', (Remove-MusikPath $userPath), 'User')
$env:PATH = Remove-MusikPath $env:PATH
Write-Host 'Musik uninstalled. Python, Git, mpv, Node.js and personal music were not removed.'
if ($KeepData) { Write-Host 'Saved Musik data was kept.' }
