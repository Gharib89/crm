# Build the crm onedir bundle on Windows.
# Usage:  pwsh -File scripts/build.ps1
#         or:  .\scripts\build.ps1
$ErrorActionPreference = 'Stop'

$RepoRoot = Resolve-Path "$PSScriptRoot\.."
Set-Location $RepoRoot

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}
& .\.venv\Scripts\Activate.ps1

pip install --quiet --upgrade pip
pip install --quiet -e ".[dev]"

if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist)  { Remove-Item -Recurse -Force dist }

pyinstaller crm.spec

Write-Host ""
Write-Host "Built: $RepoRoot\dist\crm\  (onedir bundle; launcher: dist\crm\crm.exe)"
& "$RepoRoot\dist\crm\crm.exe" --version
