#requires -Version 5
<#
.SYNOPSIS
  Install or update the crm CLI on Windows from Cloudflare R2.
.DESCRIPTION
  Downloads the prebuilt crm onedir bundle, extracts it to
  %LOCALAPPDATA%\Programs\crm, and adds that directory to the user PATH.
  Set $env:CRM_VERSION (e.g. v0.6.0) to pin a version; default is latest.
.PARAMETER Uninstall
  Remove the install directory and its PATH entry.
#>
[CmdletBinding()]
param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'

$BaseUrl    = 'https://pub-REPLACE_ME.r2.dev'   # set during R2 setup (Task 0/7)
$InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\crm'

function Remove-FromUserPath([string]$dir) {
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not $current) { return }
    $parts = $current.Split(';') | Where-Object { $_ -and $_ -ne $dir }
    [Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')
}

if ($Uninstall) {
    if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
    Remove-FromUserPath $InstallDir
    Write-Host "crm uninstalled. Open a new shell for PATH changes to apply."
    return
}

$version = if ($env:CRM_VERSION) { $env:CRM_VERSION } else { 'latest' }
$zipUrl  = "$BaseUrl/$version/crm-windows-x86_64.zip"
$tmpZip  = Join-Path $env:TEMP ("crm-" + [Guid]::NewGuid().ToString('N') + ".zip")

Write-Host "Downloading $zipUrl ..."
Invoke-WebRequest -Uri $zipUrl -OutFile $tmpZip -UseBasicParsing

if (Test-Path $InstallDir) { Remove-Item -Recurse -Force $InstallDir }
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

Write-Host "Extracting to $InstallDir ..."
Expand-Archive -Path $tmpZip -DestinationPath $InstallDir -Force
Remove-Item $tmpZip -Force

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (($userPath -split ';') -notcontains $InstallDir) {
    $newPath = if ($userPath) { "$userPath;$InstallDir" } else { $InstallDir }
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    Write-Host "Added $InstallDir to your user PATH. Open a new shell to use 'crm'."
}

$exe = Join-Path $InstallDir 'crm.exe'
Write-Host "Installed: " -NoNewline
& $exe --version
