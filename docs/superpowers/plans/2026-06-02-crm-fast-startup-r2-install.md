# crm Fast-Startup CLI + R2 Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut `crm` CLI startup from ~410–530ms to ≤100ms and make Windows install a one-line script, by switching PyInstaller to `--onedir`, deferring heavy imports, and publishing the binary + install scripts to a public Cloudflare R2 bucket.

**Architecture:** Three independent changes. (1) `crm.spec` flips `--onefile`→`--onedir` to kill per-launch self-extraction. (2) `crm/cli.py` defers the connection/backend stack into `backend()` and replaces the 17 eager command-group imports with a LazyGroup that imports a subcommand's module only when invoked. (3) `release.yml` archives the onedir build and a new `publish` step uploads it plus `install.ps1`/`install.sh` to R2; users install with `irm …/install.ps1 | iex`. Source repo stays private; R2 is the public install channel.

**Tech Stack:** Python 3.9+, Click, PyInstaller 6.x, pytest, GitHub Actions, Cloudflare R2 + wrangler 4.97.0.

**Branch:** `perf/fast-startup-r2-install` (already created; spec committed at `47e3668`).

**Spec:** `docs/superpowers/specs/2026-06-02-crm-fast-startup-r2-install-design.md`

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `crm/cli.py` | Modify | Defer `conn_mod`/`D365Backend` into `backend()`; add `_LazyJsonAwareGroup`; remove the eager command-group wiring block (lines 270–305) |
| `crm/tests/test_lazy_imports.py` | Create | Regression test: `crm --version` must not import command modules or the D365 backend |
| `crm.spec` | Modify | `--onefile` → `--onedir` (`EXE(exclude_binaries=True)` + `COLLECT`) |
| `scripts/install.ps1` | Create | Windows: download R2 zip, extract to `%LOCALAPPDATA%\Programs\crm`, add to PATH, `-Uninstall` |
| `scripts/install.sh` | Create | Linux: download R2 tar.gz, extract to `~/.local/share/crm`, link `~/.local/bin/crm`, `--uninstall` |
| `.github/workflows/release.yml` | Modify | Build onedir, archive per-OS, upload to GitHub Release + R2 |
| `README.md` | Modify | Replace broken GitHub-release install with R2 one-liners |
| `docs/getting-started/install.md` | Modify | Replace private-releases link with R2 one-liners |

The R2 public host (`pub-<hash>.r2.dev`) is unknown until the bucket exists. Task 0 creates it and captures the host; the scripts and docs are written with the literal sentinel `pub-REPLACE_ME.r2.dev`, and Task 7 substitutes the real host everywhere in one pass. `grep -rn REPLACE_ME` returning nothing is the completion check.

---

## Task 0: Cloudflare R2 setup (manual, owner runs locally)

This task is run by the repo owner (Ahmed) in a local shell with wrangler authenticated. It is a prerequisite for Task 7 (host substitution) and for the release workflow to succeed, but does **not** block Tasks 1–6.

- [ ] **Step 1: Create the bucket**

Run (locally, with wrangler logged in via `wrangler login` or `CLOUDFLARE_API_TOKEN`/`CLOUDFLARE_ACCOUNT_ID`):

```bash
npx wrangler@4.97.0 r2 bucket create crm-cli-releases
```
Expected: `Created bucket 'crm-cli-releases'`.

- [ ] **Step 2: Enable the public dev URL**

```bash
npx wrangler@4.97.0 r2 bucket dev-url enable crm-cli-releases
```
Expected: prints the public URL, e.g. `https://pub-abc123def456.r2.dev`. **Record this host** — it is the value that replaces `pub-REPLACE_ME.r2.dev` in Task 7.

- [ ] **Step 3: Confirm the GitHub secret token has R2 edit scope**

The `CLOUDFLARE_API_TOKEN` repo secret is currently scoped for Cloudflare Pages (`docs.yml`). In the Cloudflare dashboard (My Profile → API Tokens), confirm the token used for that secret has **Workers R2 Storage: Edit** permission. If not, edit the token (or mint a new one with both Pages and R2 edit) and update the `CLOUDFLARE_API_TOKEN` repo secret. No code change; record done.

---

## Task 1: Lazy imports in `crm/cli.py`

**Files:**
- Test: `crm/tests/test_lazy_imports.py` (create)
- Modify: `crm/cli.py` (imports at lines 15, 19–24; `backend()` at lines 92–105; wiring block lines 270–305)

- [ ] **Step 1: Write the failing regression test**

Create `crm/tests/test_lazy_imports.py`:

```python
"""Guard the CLI fast path: `crm --version` must not import command modules or
the D365 backend stack. Run in a subprocess so sys.modules starts clean."""
import json
import subprocess
import sys

# Command modules that must load only when their subcommand is invoked.
LAZY_MODULES = {
    "crm.commands.action", "crm.commands.app", "crm.commands.async_ops",
    "crm.commands.batch", "crm.commands.connection", "crm.commands.data",
    "crm.commands.entity", "crm.commands.init", "crm.commands.metadata",
    "crm.commands.query", "crm.commands.repl", "crm.commands.session",
    "crm.commands.skill", "crm.commands.solution", "crm.commands.view",
    "crm.commands.workflow", "crm.utils.d365_backend",
}


def test_version_does_not_import_command_modules_or_backend():
    probe = (
        "import sys, json\n"
        "from click.testing import CliRunner\n"
        "from crm.cli import cli\n"
        "result = CliRunner().invoke(cli, ['--version'])\n"
        f"lazy = {sorted(LAZY_MODULES)!r}\n"
        "leaked = sorted(set(lazy) & set(sys.modules))\n"
        "print(json.dumps({'exit': result.exit_code, "
        "'output': result.output.strip(), 'leaked': leaked}))\n"
    )
    proc = subprocess.run([sys.executable, "-c", probe],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["exit"] == 0
    assert data["output"].startswith("crm, version"), data["output"]
    assert data["leaked"] == [], f"fast path imported deferred modules: {data['leaked']}"


def test_lazy_group_still_resolves_a_subcommand():
    """The LazyGroup must still expose every command and import on demand."""
    from click.testing import CliRunner
    from crm.cli import cli
    result = CliRunner().invoke(cli, ["entity", "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage: crm entity" in result.output
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest crm/tests/test_lazy_imports.py -v`
Expected: `test_version_does_not_import_command_modules_or_backend` FAILS — `leaked` is non-empty (currently `crm.commands.*` and `crm.utils.d365_backend` import eagerly when `crm.cli` is imported). The second test passes already.

- [ ] **Step 3: Add `TYPE_CHECKING` import and defer the backend stack**

In `crm/cli.py`, change line 15 from:

```python
from typing import Any
```
to:
```python
from typing import TYPE_CHECKING, Any
```

Replace lines 19–24 (the block from `from crm import __version__` through `from crm.utils.d365_backend import D365Backend`):

```python
from crm import __version__
from crm.core import (
    connection as conn_mod,
)
from crm.core.logging_setup import setup_logging
from crm.utils.d365_backend import D365Backend
```
with:
```python
from crm import __version__
from crm.core.logging_setup import setup_logging

if TYPE_CHECKING:
    from crm.utils.d365_backend import D365Backend
```

(`from crm.utils.repl_skin import ReplSkin` and `from crm.commands._helpers import _sanitize, _short_repr` on the following lines stay unchanged — both are cheap.)

- [ ] **Step 4: Local-import the deferred symbols inside `backend()`**

In `crm/cli.py`, change the start of the `backend()` method (currently line 92–93):

```python
    def backend(self) -> D365Backend:
        key = (self.profile_name, self.password, self.dry_run, self.auth_scheme)
```
to:
```python
    def backend(self) -> "D365Backend":
        from crm.core import connection as conn_mod
        from crm.utils.d365_backend import D365Backend

        key = (self.profile_name, self.password, self.dry_run, self.auth_scheme)
```

The rest of `backend()` (`conn_mod.resolve_credentials(...)`, `D365Backend(...)`) is unchanged and now resolves against the local imports.

- [ ] **Step 5: Add the LazyGroup class**

In `crm/cli.py`, immediately after the `_JsonAwareGroup` class (after its `main()` method ends, before the `# ── Root group ──` comment at line 196), insert:

```python
class _LazyJsonAwareGroup(_JsonAwareGroup):
    """Root group that imports a subcommand's module only when that subcommand is
    invoked, so `crm --version` and direct command invocations avoid importing all
    command modules (and their requests/NTLM/prompt_toolkit deps). `crm --help`
    still imports every module to render short help — an accepted trade-off."""

    # Click command name -> "module:attribute"
    _lazy_commands = {
        "action": "crm.commands.action:action_group",
        "app": "crm.commands.app:app_group",
        "async": "crm.commands.async_ops:async_group",
        "batch": "crm.commands.batch:batch_cmd",
        "connection": "crm.commands.connection:connection_group",
        "data": "crm.commands.data:data_group",
        "entity": "crm.commands.entity:entity_group",
        "init": "crm.commands.init:init_cmd",
        "metadata": "crm.commands.metadata:metadata_group",
        "query": "crm.commands.query:query_group",
        "repl": "crm.commands.repl:repl",
        "service-document": "crm.commands.batch:service_document_cmd",
        "session": "crm.commands.session:session_group",
        "skill": "crm.commands.skill:skill_group",
        "solution": "crm.commands.solution:solution_group",
        "view": "crm.commands.view:view_group",
        "workflow": "crm.commands.workflow:workflow_group",
    }

    def list_commands(self, ctx):
        return sorted({*self._lazy_commands, *super().list_commands(ctx)})

    def get_command(self, ctx, cmd_name):
        eager = super().get_command(ctx, cmd_name)
        if eager is not None:
            return eager
        target = self._lazy_commands.get(cmd_name)
        if target is None:
            return None
        import importlib
        module_name, attr = target.split(":")
        return getattr(importlib.import_module(module_name), attr)
```

- [ ] **Step 6: Point the root group at the LazyGroup**

In `crm/cli.py` line 199, change:

```python
@click.group(cls=_JsonAwareGroup, invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
```
to:
```python
@click.group(cls=_LazyJsonAwareGroup, invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
```

- [ ] **Step 7: Remove the eager command-wiring block**

In `crm/cli.py`, delete lines 270–305 entirely — the block starting at the comment `# ── Wire up command modules ─────────────────────────────────────────────` through the last `cli.add_command(app_group)`. This removes 17 `from crm.commands.X import …` lines and 17 `cli.add_command(...)` lines.

After deletion, the region around the end of file reads:

```python
    if ctx.invoked_subcommand is None:
        from crm.commands.repl import repl
        ctx.invoke(repl)


if __name__ == "__main__":
    cli()
```

(The bare-invocation `repl` import inside the root callback stays — it is already a local import.)

- [ ] **Step 8: Run the lazy-import tests — verify they pass**

Run: `pytest crm/tests/test_lazy_imports.py -v`
Expected: both tests PASS. `leaked == []`.

- [ ] **Step 9: Run the full suite — verify no regressions**

Run: `pytest -q`
Expected: all tests pass (the lazy group resolves every command via `get_command`; tests that do `CliRunner().invoke(cli, [...])` are unaffected).

- [ ] **Step 10: Verify pyright is clean on cli.py's neighborhood**

Run: `pyright crm/cli.py` (basic mode for cli.py per `pyrightconfig.json`)
Expected: no new errors. The `TYPE_CHECKING` import provides `D365Backend` for the `-> "D365Backend"` annotation.

- [ ] **Step 11: Manually confirm the speed win on source**

Run: `python -X importtime -c "import crm.cli" 2>&1 | grep -E "requests_ntlm|prompt_toolkit|d365_backend" | head`
Expected: **no output** (none of those import when `crm.cli` is imported).

- [ ] **Step 12: Commit**

```bash
git add crm/cli.py crm/tests/test_lazy_imports.py
git commit -m "perf(cli): lazy-load subcommands and backend stack

Defer conn_mod/D365Backend into backend() and replace the 17 eager
command-group imports with a LazyGroup that imports a subcommand's
module only on invocation. crm --version no longer loads requests/
NTLM/prompt_toolkit. crm --help still loads all (accepted)."
```

---

## Task 2: Flip `crm.spec` to `--onedir`

**Files:**
- Modify: `crm.spec` (the `EXE(...)` call; add a `COLLECT(...)` call)

- [ ] **Step 1: Edit the EXE() call**

In `crm.spec`, replace the `EXE(...)` block:

```python
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='crm',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```
with:
```python
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='crm',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='crm',
)
```

- [ ] **Step 2: Build the onedir bundle**

Run: `pyinstaller crm.spec`
Expected: `Build complete!`; output directory `dist/crm/` containing `crm` (binary) and `_internal/`.

- [ ] **Step 3: Verify startup, version, and skill resolution**

Run:
```bash
./dist/crm/crm --version
./dist/crm/crm --help
./dist/crm/crm skill --help
ls dist/crm/_internal/crm/skills/
```
Expected: `--version` prints `crm, version 0.6.0`; `--help` lists all 17 commands; `skill --help` shows the skill usage (proves the bundled `SKILL.md` resolves); the `ls` lists `SKILL.md`.

- [ ] **Step 4: Confirm the startup improvement**

Run: `for i in 1 2 3; do t0=$(date +%s.%N); ./dist/crm/crm --version >/dev/null; t1=$(date +%s.%N); echo "$(echo "$t1-$t0"|bc)s"; done`
Expected: each run well under the old ~0.45s (target ≤0.10s with the Task 1 lazy imports now compiled in; onedir + lazy together).

- [ ] **Step 5: Commit**

```bash
git add crm.spec
git commit -m "build: PyInstaller --onedir to remove per-launch extraction

EXE(exclude_binaries=True) + COLLECT() produces dist/crm/ instead of a
single self-extracting binary. Skills resolve under _internal/crm/skills."
```

---

## Task 3: `scripts/install.ps1` (Windows)

**Files:**
- Create: `scripts/install.ps1`

- [ ] **Step 1: Write the install script**

Create `scripts/install.ps1`:

```powershell
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
```

- [ ] **Step 2: Verification (limited — no pwsh on the dev box)**

`pwsh` is not available in this dev environment, so the script cannot be syntax-checked or run locally. Verify by inspection against this checklist (do not claim a passing run that did not happen):
- `-Uninstall` branch returns before any download.
- `$version` defaults to `latest`, honors `$env:CRM_VERSION`.
- Extraction target is cleared before extract (idempotent re-install).
- PATH edit is on `User` scope and only when absent.
Functional verification happens in the Task 6 manual VM smoke after release.

- [ ] **Step 3: Commit**

```bash
git add scripts/install.ps1
git commit -m "feat(install): add Windows install.ps1 (R2 download + PATH)"
```

---

## Task 4: `scripts/install.sh` (Linux)

**Files:**
- Create: `scripts/install.sh`

- [ ] **Step 1: Write the install script**

Create `scripts/install.sh`:

```sh
#!/bin/sh
# Install or update the crm CLI on Linux from Cloudflare R2.
# Set CRM_VERSION (e.g. v0.6.0) to pin a version; default is latest.
# Run with --uninstall to remove the install.
set -eu

BASE_URL="https://pub-REPLACE_ME.r2.dev"   # set during R2 setup (Task 0/7)
INSTALL_DIR="${HOME}/.local/share/crm"
BIN_DIR="${HOME}/.local/bin"
BIN_LINK="${BIN_DIR}/crm"

if [ "${1:-}" = "--uninstall" ]; then
    rm -rf "$INSTALL_DIR"
    rm -f "$BIN_LINK"
    echo "crm uninstalled."
    exit 0
fi

VERSION="${CRM_VERSION:-latest}"
URL="${BASE_URL}/${VERSION}/crm-linux-x86_64.tar.gz"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading ${URL} ..."
curl -fsSL "$URL" -o "${TMP}/crm.tar.gz"

rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
tar -xzf "${TMP}/crm.tar.gz" -C "$INSTALL_DIR"

mkdir -p "$BIN_DIR"
ln -sf "${INSTALL_DIR}/crm" "$BIN_LINK"
echo "Installed to ${INSTALL_DIR}; linked at ${BIN_LINK}."

case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *) echo "Add ${BIN_DIR} to your PATH:  export PATH=\"${BIN_DIR}:\$PATH\"" ;;
esac

"${BIN_LINK}" --version
```

- [ ] **Step 2: Syntax-check the script**

Run: `sh -n scripts/install.sh && echo OK`
Expected: `OK` (no syntax errors). `shellcheck` is not installed; `sh -n` is the available check.

- [ ] **Step 3: Smoke the uninstall path with no network**

Run: `sh scripts/install.sh --uninstall`
Expected: prints `crm uninstalled.` and exits 0 (it removes non-existent paths harmlessly, never reaching the download).

- [ ] **Step 4: Commit**

```bash
git add scripts/install.sh
git commit -m "feat(install): add Linux install.sh (R2 download + ~/.local/bin)"
```

---

## Task 5: Update `.github/workflows/release.yml`

**Files:**
- Modify: `.github/workflows/release.yml` (replace the `build` and `publish` jobs)

- [ ] **Step 1: Replace the workflow file**

Overwrite `.github/workflows/release.yml` with:

```yaml
name: release

on:
  push:
    tags:
      - 'v*'

permissions:
  contents: write

jobs:
  verify-tag:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with:
          python-version: '3.11'
      - name: Verify tag matches setup.py version
        run: python scripts/check_tag_version.py "${GITHUB_REF_NAME}"

  build:
    needs: verify-tag
    strategy:
      fail-fast: true
      matrix:
        include:
          - os: ubuntu-22.04
            archive_name: crm-linux-x86_64.tar.gz
          - os: windows-latest
            archive_name: crm-windows-x86_64.zip
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v6

      - uses: actions/setup-python@v6
        with:
          python-version: '3.11'

      - name: Install project + dev deps
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"

      - name: Run unit tests
        run: pytest -q

      - name: Build with PyInstaller
        run: pyinstaller crm.spec

      - name: Smoke test (Linux)
        if: runner.os != 'Windows'
        run: |
          ./dist/crm/crm --version
          ./dist/crm/crm --help

      - name: Smoke test (Windows)
        if: runner.os == 'Windows'
        run: |
          .\dist\crm\crm.exe --version
          .\dist\crm\crm.exe --help

      - name: Archive (Linux)
        if: runner.os != 'Windows'
        run: tar -czf "${{ matrix.archive_name }}" -C dist/crm .

      - name: Archive (Windows)
        if: runner.os == 'Windows'
        shell: pwsh
        run: Compress-Archive -Path dist/crm/* -DestinationPath "${{ matrix.archive_name }}" -Force

      - uses: actions/upload-artifact@v7
        with:
          name: ${{ matrix.archive_name }}
          path: ${{ matrix.archive_name }}
          if-no-files-found: error

  publish:
    needs: build
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v6

      - uses: actions/download-artifact@v8
        with:
          path: artifacts
          merge-multiple: true

      - name: List artifacts
        run: ls -la artifacts

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          files: |
            artifacts/crm-linux-x86_64.tar.gz
            artifacts/crm-windows-x86_64.zip
          generate_release_notes: true
          fail_on_unmatched_files: true

      - name: Publish to Cloudflare R2
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
          TAG: ${{ github.ref_name }}
        run: |
          bucket=crm-cli-releases
          for f in crm-linux-x86_64.tar.gz crm-windows-x86_64.zip; do
            case "$f" in
              *.zip) ct=application/zip ;;
              *)     ct=application/gzip ;;
            esac
            npx --yes wrangler@4.97.0 r2 object put "$bucket/${TAG}/$f" \
              --file="artifacts/$f" --content-type="$ct" --remote
            npx --yes wrangler@4.97.0 r2 object put "$bucket/latest/$f" \
              --file="artifacts/$f" --content-type="$ct" --remote
          done
          npx --yes wrangler@4.97.0 r2 object put "$bucket/install.ps1" \
            --file=scripts/install.ps1 --content-type="text/plain; charset=utf-8" --remote
          npx --yes wrangler@4.97.0 r2 object put "$bucket/install.sh" \
            --file=scripts/install.sh --content-type="text/plain; charset=utf-8" --remote
```

- [ ] **Step 2: Validate the YAML parses**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml')); print('YAML OK')"`
Expected: `YAML OK`.

- [ ] **Step 3: Sanity-check the logic by inspection**

Confirm: `build` produces `crm-linux-x86_64.tar.gz` / `crm-windows-x86_64.zip` from `dist/crm/`; smoke test paths point at `dist/crm/crm[.exe]`; `publish` uploads to both `${TAG}/` and `latest/` plus the two install scripts. (Actual end-to-end run only happens on a real `v*` tag push — note this; do not fake a CI run.)

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci(release): onedir archives + publish to Cloudflare R2

Build dist/crm/ onedir, tar.gz (Linux) / zip (Windows), attach to the
GitHub Release, and upload artifacts + install scripts to the
crm-cli-releases R2 bucket under <tag>/ and latest/."
```

---

## Task 6: Update install docs

**Files:**
- Modify: `README.md` (the `## Install` section, lines 30–62)
- Modify: `docs/getting-started/install.md` (the `## Prebuilt binary` section, lines 6–9)

- [ ] **Step 1: Rewrite the README install section**

In `README.md`, replace everything from `## Install` (line 30) through the glibc line (line 62, ending `…glibc ≥ 2.35.`) — i.e. the entire "Option 1: Prebuilt binary" block, stopping just before `### Option 2: From source (development)` — with:

````markdown
## Install

### Option 1: Install script (no Python required)

The prebuilt `crm` binary bundles CPython and all dependencies. Install it with
a one-liner — no GitHub account or Python needed.

**Windows (PowerShell):**

```powershell
irm https://pub-REPLACE_ME.r2.dev/install.ps1 | iex
```

Installs to `%LOCALAPPDATA%\Programs\crm` and adds it to your user PATH. Open a
new shell, then run `crm --version`. The binary is unsigned, so Windows
SmartScreen may warn on first run. To uninstall, download `install.ps1` and run
`.\install.ps1 -Uninstall`.

**Linux:**

```bash
curl -fsSL https://pub-REPLACE_ME.r2.dev/install.sh | sh
```

Installs to `~/.local/share/crm` and links `~/.local/bin/crm`. Ensure
`~/.local/bin` is on your PATH. Built on Ubuntu 22.04, so it runs on any Linux
with glibc ≥ 2.35. To uninstall, download `install.sh` and run
`sh install.sh --uninstall`.

Pin a version by setting `CRM_VERSION` (e.g. `v0.6.0`) before running.
````

(The `### Option 2: From source (development)` heading and everything after it stays unchanged.)

- [ ] **Step 2: Rewrite the docs install page**

In `docs/getting-started/install.md`, replace the `## Prebuilt binary` section (lines 6–9) with:

````markdown
## Install script (no Python required)

**Windows (PowerShell):**

```powershell
irm https://pub-REPLACE_ME.r2.dev/install.ps1 | iex
```

**Linux:**

```bash
curl -fsSL https://pub-REPLACE_ME.r2.dev/install.sh | sh
```

Pin a version by setting `CRM_VERSION` (e.g. `v0.6.0`). The binary is unsigned;
Windows SmartScreen may warn on first run.
````

(The `## From source` section below it stays unchanged.)

- [ ] **Step 3: Build the docs strictly**

Run: `pip install -e ".[docs]" && mkdocs build --strict`
Expected: build succeeds with no warnings (strict mode fails on broken links / nav issues).

- [ ] **Step 4: Commit**

```bash
git add README.md docs/getting-started/install.md
git commit -m "docs: install via R2 one-liner instead of private release URL

README and the docs site told users to curl a github.com/.../releases
URL that 404s on a private repo. Replace with irm/curl install scripts."
```

---

## Task 7: Substitute the real R2 host (after Task 0)

**Files:**
- Modify: `scripts/install.ps1`, `scripts/install.sh`, `README.md`, `docs/getting-started/install.md`

- [ ] **Step 1: Replace the sentinel host everywhere**

Using the host recorded in Task 0 Step 2 (e.g. `pub-abc123def456.r2.dev`), run (substitute the real host for `pub-abc123def456.r2.dev`):

```bash
grep -rl 'pub-REPLACE_ME\.r2\.dev' scripts/ README.md docs/getting-started/install.md \
  | xargs sed -i 's/pub-REPLACE_ME\.r2\.dev/pub-abc123def456.r2.dev/g'
```

- [ ] **Step 2: Verify no sentinel remains**

Run: `grep -rn REPLACE_ME scripts/ README.md docs/getting-started/install.md`
Expected: **no output** (exit 1). If anything prints, fix it.

- [ ] **Step 3: Re-validate docs build**

Run: `mkdocs build --strict`
Expected: succeeds.

- [ ] **Step 4: Commit**

```bash
git add scripts/install.ps1 scripts/install.sh README.md docs/getting-started/install.md
git commit -m "chore(install): point install scripts and docs at the R2 host"
```

---

## Task 8: Release & verify end-to-end

- [ ] **Step 1: Open the PR**

```bash
git push -u origin perf/fast-startup-r2-install
gh pr create --fill --base main
gh pr edit --add-reviewer @copilot
```

- [ ] **Step 2: Merge on green**

After CI (build.yml + docs.yml) and Copilot review pass:
```bash
gh pr merge --squash --auto
```

- [ ] **Step 3: Tag a release to exercise the R2 publish**

Bump `setup.py` version if needed, then push a matching tag (e.g. `v0.7.0`). The `release` workflow builds onedir, attaches archives to the GitHub Release, and uploads to R2. Confirm the R2 objects exist:
```bash
curl -sSI https://pub-<host>.r2.dev/latest/crm-windows-x86_64.zip | head -1
```
Expected: `HTTP/2 200`.

- [ ] **Step 4: Manual VM smoke (Windows)**

On a clean Win11 VM: `irm https://pub-<host>.r2.dev/install.ps1 | iex` → open new shell → `crm --version` → `crm whoami` against a real D365 endpoint. Run the installer a second time to confirm idempotent upgrade.

---

## Self-Review (completed by plan author)

**Spec coverage:**
- onedir → Task 2 ✓
- lazy imports (conn_mod/D365Backend + LazyGroup) → Task 1 ✓
- `--help` trade-off → documented in Task 1 LazyGroup docstring ✓
- install.ps1 (incl. -Uninstall) → Task 3 ✓
- install.sh → Task 4 ✓
- release.yml onedir archive + publish-r2 + smoke paths → Task 5 ✓
- R2 one-time setup + token scope → Task 0 ✓
- docs (README + install.md) → Task 6 ✓
- skill resolution unchanged → verified in Task 2 Step 3 ✓
- tests (lazy regression + mkdocs --strict + smoke) → Tasks 1/6/8 ✓

**Placeholder scan:** `pub-REPLACE_ME.r2.dev` is a deliberate sentinel with an explicit fill-in (Task 7) gated on a runtime-determined value (Task 0); `grep REPLACE_ME` is the completion check. No TBD/TODO/"handle edge cases" left. install.ps1 verification is honestly marked as inspection-only (no pwsh locally).

**Type/name consistency:** Command names in the LazyGroup map (`async`, `service-document`, etc.) were introspected from the live `cli` object, not guessed. `_LazyJsonAwareGroup` subclasses `_JsonAwareGroup` (preserves `main()` JSON behavior). Archive names (`crm-linux-x86_64.tar.gz`, `crm-windows-x86_64.zip`) and bucket (`crm-cli-releases`) are identical across Tasks 3/4/5/6/7.
