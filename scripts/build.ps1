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
# Both shipped skills ride in the bundle (crm.spec datas, #1062).
foreach ($skill in 'skills', 'migration-skill') {
    if (-not (Test-Path "$RepoRoot\dist\crm\_internal\crm\$skill\SKILL.md")) { throw "missing bundled $skill" }
}
# The compatibility rules `crm inventory assess` loads (#1063).
if (-not (Test-Path "$RepoRoot\dist\crm\_internal\crm\rules\compatibility-rules.json")) { throw "missing bundled rules" }
# tree-sitter and its grammars, the engine of `crm inventory convert` (ADR 0034).
$ConvertOut = Join-Path ([System.IO.Path]::GetTempPath()) ("crm-convert-" + [guid]::NewGuid())
& "$RepoRoot\dist\crm\crm.exe" --json inventory convert "$RepoRoot\crm\tests\fixtures\inventory\convert-smoke\inventory" --findings "$RepoRoot\crm\tests\fixtures\inventory\convert-smoke\findings.json" --packages "$RepoRoot\crm\tests\fixtures\inventory\convert-smoke\packages" --out $ConvertOut | Out-Null
if ($LASTEXITCODE -ne 0) { throw "bundled convert failed" }
if (-not (Get-ChildItem $ConvertOut -Recurse -Filter *.patched.js | Select-String -SimpleMatch 'Xrm.Utility.getGlobalContext().getClientUrl()')) { throw "bundled convert wrote no patch" }
Remove-Item -Recurse -Force $ConvertOut
