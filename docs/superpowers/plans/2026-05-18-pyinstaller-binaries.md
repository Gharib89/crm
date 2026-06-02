# PyInstaller Binaries Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce single-file `crm` executables for Windows x86_64 and Linux x86_64, built locally and via GitHub Actions, requiring no Python on the end-user machine.

**Architecture:** PyInstaller `--onefile` freeze driven by a committed `crm.spec`. Local build scripts (`scripts/build.sh`, `scripts/build.ps1`) for dev iteration. Two GH Actions workflows: `build.yml` for sanity builds on push/PR, `release.yml` for tag-driven publishing of artifacts to GitHub Releases. Smoke tests run the frozen binary on each OS before any artifact is accepted.

**Tech Stack:** PyInstaller 6.x, Python 3.11, GitHub Actions (`ubuntu-22.04`, `windows-latest`), `softprops/action-gh-release@v2`, existing project deps (`click`, `requests`, `requests_ntlm`, `prompt_toolkit`).

**Reference spec:** `docs/superpowers/specs/2026-05-18-pyinstaller-binaries-design.md`

---

## File Structure

| File | Status | Purpose |
|---|---|---|
| `.gitignore` | Modify | Ignore PyInstaller `build/`, `dist/`, `*.spec.bak` artifacts |
| `setup.py` | Modify | Add `pyinstaller` to `extras_require["dev"]` |
| `crm.spec` | Create | PyInstaller spec — source of truth for freeze |
| `scripts/build.sh` | Create | Local Linux/macOS build script |
| `scripts/build.ps1` | Create | Local Windows build script |
| `scripts/check_tag_version.py` | Create | Asserts git tag == `setup.py` version (used by release workflow) |
| `.github/workflows/build.yml` | Create | CI sanity build on push/PR |
| `.github/workflows/release.yml` | Create | Tag-driven release build + GitHub Release |
| `README.md` | Modify | Add "Install (prebuilt binary)" section, document SmartScreen workaround |

---

## Task 1: Wire up build tooling (gitignore + dev dep)

**Files:**
- Modify: `.gitignore`
- Modify: `setup.py`

- [ ] **Step 1: Append build artifact patterns to `.gitignore`**

Append these lines:

```gitignore
build/
dist/
*.spec.bak
```

- [ ] **Step 2: Add `pyinstaller` to dev extras in `setup.py`**

Replace:

```python
    extras_require={
        "dev": ["pytest>=7.0", "requests_mock>=1.10"],
    },
```

with:

```python
    extras_require={
        "dev": ["pytest>=7.0", "requests_mock>=1.10", "pyinstaller>=6.0"],
    },
```

- [ ] **Step 3: Install dev extras and verify**

Run:

```bash
pip install -e ".[dev]"
pyinstaller --version
```

Expected: PyInstaller version `6.x` prints. No errors.

- [ ] **Step 4: Commit**

```bash
git add .gitignore setup.py
git commit -m "build: add pyinstaller dev dep and ignore build artifacts"
```

---

## Task 2: Write `crm.spec`

**Files:**
- Create: `crm.spec`

- [ ] **Step 1: Write the spec file**

Create `crm.spec` with this content:

```python
# crm.spec — PyInstaller spec for the crm CLI.
# Builds a single-file executable that bundles the CPython runtime, all
# Python dependencies, and the crm package data (skills/*.md).
#
# Build:  pyinstaller crm.spec
# Output: dist/crm  (Linux/macOS)  or  dist/crm.exe  (Windows)

# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['crm/__main__.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('crm/skills', 'crm/skills'),
        ('crm/README.md', 'crm'),
    ],
    hiddenimports=[
        'requests_ntlm',
        'prompt_toolkit',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

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

- [ ] **Step 2: Verify `crm/README.md` exists**

Run:

```bash
ls crm/README.md
```

Expected: file exists. If missing, change `datas=` to drop that entry — `setup.py` declares it under `package_data` but it is optional for runtime.

- [ ] **Step 3: Build locally and smoke-test**

Run:

```bash
pyinstaller crm.spec
./dist/crm --version
./dist/crm --help
```

Expected: both commands exit 0. `--version` prints the package version. `--help` prints Click's command listing.

- [ ] **Step 4: If smoke test fails on hidden imports**

If you see `ModuleNotFoundError` for a sub-module (e.g. `requests_ntlm.ntlm`), append it to `hiddenimports` in `crm.spec` and re-run `pyinstaller crm.spec`. Repeat until smoke test passes. Record the final list in the commit message.

- [ ] **Step 5: Commit**

```bash
git add crm.spec
git commit -m "build: add PyInstaller spec for single-file crm binary"
```

---

## Task 3: Local Linux/macOS build script

**Files:**
- Create: `scripts/build.sh`

- [ ] **Step 1: Write the script**

Create `scripts/build.sh`:

```bash
#!/usr/bin/env bash
# Build a single-file crm binary on Linux/macOS.
# Usage: ./scripts/build.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

pip install --quiet --upgrade pip
pip install --quiet -e ".[dev]"

rm -rf build dist

pyinstaller crm.spec

echo
echo "Built: $REPO_ROOT/dist/crm"
"$REPO_ROOT/dist/crm" --version
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x scripts/build.sh
```

- [ ] **Step 3: Run it end-to-end**

```bash
./scripts/build.sh
```

Expected: script runs without error and prints `Built: .../dist/crm` followed by the version string.

- [ ] **Step 4: Commit**

```bash
git add scripts/build.sh
git commit -m "build: add local Linux/macOS build script"
```

---

## Task 4: Local Windows build script

**Files:**
- Create: `scripts/build.ps1`

- [ ] **Step 1: Write the script**

Create `scripts/build.ps1`:

```powershell
# Build a single-file crm.exe on Windows.
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
Write-Host "Built: $RepoRoot\dist\crm.exe"
& "$RepoRoot\dist\crm.exe" --version
```

- [ ] **Step 2: Verify script syntax**

If you have access to a Windows / PowerShell environment, run:

```powershell
pwsh -File scripts/build.ps1
```

Expected: script runs without error and prints `Built: ...\dist\crm.exe` followed by the version string.

If no Windows host is available locally, defer the run-time verification to CI in Task 6 — but lint the script:

```bash
# Syntax check via pwsh on Linux if installed
pwsh -NoProfile -Command "Get-Command -Syntax pwsh" || true
```

(Script will execute on `windows-latest` in CI regardless.)

- [ ] **Step 3: Commit**

```bash
git add scripts/build.ps1
git commit -m "build: add local Windows build script"
```

---

## Task 5: Tag/version assertion script

**Files:**
- Create: `scripts/check_tag_version.py`

- [ ] **Step 1: Write the script**

Create `scripts/check_tag_version.py`:

```python
#!/usr/bin/env python3
"""Assert that the git tag passed as argv[1] (e.g. 'v1.0.1') matches the
version declared in setup.py. Fails with exit code 1 on mismatch."""
import re
import sys
from pathlib import Path

SETUP_PY = Path(__file__).resolve().parent.parent / "setup.py"


def setup_version() -> str:
    text = SETUP_PY.read_text()
    match = re.search(r'version\s*=\s*["\']([^"\']+)["\']', text)
    if not match:
        sys.exit("setup.py: version= not found")
    return match.group(1)


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit("usage: check_tag_version.py <git-tag>")
    tag = sys.argv[1]
    if not tag.startswith("v"):
        sys.exit(f"tag {tag!r} must start with 'v'")
    tag_version = tag[1:]
    pkg_version = setup_version()
    if tag_version != pkg_version:
        sys.exit(
            f"tag version {tag_version!r} != setup.py version {pkg_version!r}"
        )
    print(f"OK: tag {tag} matches setup.py version {pkg_version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify against current version**

Run:

```bash
python scripts/check_tag_version.py v1.0.0
```

Expected: `OK: tag v1.0.0 matches setup.py version 1.0.0` and exit 0.

- [ ] **Step 3: Verify mismatch fails**

Run:

```bash
python scripts/check_tag_version.py v9.9.9
echo "exit=$?"
```

Expected: prints `tag version '9.9.9' != setup.py version '1.0.0'` and exit code 1.

- [ ] **Step 4: Commit**

```bash
git add scripts/check_tag_version.py
git commit -m "build: add tag/version assertion script for releases"
```

---

## Task 6: CI sanity build workflow

**Files:**
- Create: `.github/workflows/build.yml`

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/build.yml`:

```yaml
name: build

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  build:
    strategy:
      fail-fast: false
      matrix:
        include:
          - os: ubuntu-22.04
            artifact_name: crm-linux-x86_64
            binary_path: dist/crm
          - os: windows-latest
            artifact_name: crm-windows-x86_64.exe
            binary_path: dist/crm.exe
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install project + dev deps
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"

      - name: Run unit tests
        run: pytest -q
        env:
          # Skip E2E tests that need real D365 creds
          D365_E2E_SKIP: "1"

      - name: Build with PyInstaller
        run: pyinstaller crm.spec

      - name: Smoke test (Linux/macOS)
        if: runner.os != 'Windows'
        run: |
          ./dist/crm --version
          ./dist/crm --help

      - name: Smoke test (Windows)
        if: runner.os == 'Windows'
        run: |
          .\dist\crm.exe --version
          .\dist\crm.exe --help

      - name: Rename artifact
        shell: bash
        run: |
          mkdir -p out
          cp "${{ matrix.binary_path }}" "out/${{ matrix.artifact_name }}"

      - uses: actions/upload-artifact@v4
        with:
          name: ${{ matrix.artifact_name }}
          path: out/${{ matrix.artifact_name }}
          if-no-files-found: error
```

- [ ] **Step 2: Verify YAML syntax**

Run:

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/build.yml'))"
```

Expected: no output, exit 0.

- [ ] **Step 3: Check existing test suite honors `D365_E2E_SKIP`**

Run:

```bash
grep -rn "D365_E2E_SKIP\|D365_URL\|skip" crm/tests | head
```

Expected: tests that hit real D365 are gated by an env var or by missing credentials. If `D365_E2E_SKIP` is not the gate used, change the workflow to set whatever envvars the existing suite already keys off (typically: leave `D365_URL` unset, which most E2E suites treat as "skip"). Update the workflow `env:` block accordingly.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/build.yml
git commit -m "ci: add sanity build workflow for Linux + Windows binaries"
```

- [ ] **Step 5: Push and verify CI passes**

```bash
git push origin main
```

Wait for the Actions run on GitHub. Both matrix jobs (`ubuntu-22.04`, `windows-latest`) must succeed and produce uploaded artifacts. Download each artifact from the run page and run it locally on a matching OS to confirm it executes.

---

## Task 7: Release workflow

**Files:**
- Create: `.github/workflows/release.yml`

- [ ] **Step 1: Write the workflow**

Create `.github/workflows/release.yml`:

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
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
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
            artifact_name: crm-linux-x86_64
            binary_path: dist/crm
          - os: windows-latest
            artifact_name: crm-windows-x86_64.exe
            binary_path: dist/crm.exe
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
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

      - name: Smoke test (Linux/macOS)
        if: runner.os != 'Windows'
        run: |
          ./dist/crm --version
          ./dist/crm --help

      - name: Smoke test (Windows)
        if: runner.os == 'Windows'
        run: |
          .\dist\crm.exe --version
          .\dist\crm.exe --help

      - name: Rename artifact
        shell: bash
        run: |
          mkdir -p out
          cp "${{ matrix.binary_path }}" "out/${{ matrix.artifact_name }}"

      - uses: actions/upload-artifact@v4
        with:
          name: ${{ matrix.artifact_name }}
          path: out/${{ matrix.artifact_name }}
          if-no-files-found: error

  publish:
    needs: build
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/download-artifact@v4
        with:
          path: artifacts
          merge-multiple: true
      - name: List artifacts
        run: ls -la artifacts
      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          files: |
            artifacts/crm-linux-x86_64
            artifacts/crm-windows-x86_64.exe
          generate_release_notes: true
          fail_on_unmatched_files: true
```

- [ ] **Step 2: Verify YAML syntax**

Run:

```bash
python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"
```

Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add tag-driven release workflow"
```

- [ ] **Step 4: Push and test with a real tag**

```bash
git push origin main
git tag v1.0.0
git push origin v1.0.0
```

Watch the Actions run on GitHub. Expected: `verify-tag` passes, both `build` matrix jobs pass with smoke tests, `publish` creates a GitHub Release at `https://github.com/Gharib89/crm/releases/tag/v1.0.0` with both binaries attached.

If `verify-tag` fails because `v1.0.0` already exists historically, bump `setup.py` to `1.0.1`, commit, then tag `v1.0.1` and push.

- [ ] **Step 5: Manually verify the released binaries**

Download both files from the Release page. On a clean Linux container (`docker run --rm -it debian:12 bash`), `chmod +x crm-linux-x86_64 && ./crm-linux-x86_64 --version`. On a clean Windows VM or VM-like environment, run `crm-windows-x86_64.exe --version`. Both must exit 0 and print the version.

---

## Task 8: Update README install section

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Read current Install section**

Run:

```bash
grep -n "^## Install" README.md
```

Identify the line range that the existing `## Install` section spans (typically until the next `^## `).

- [ ] **Step 2: Insert prebuilt-binary subsection above the existing pip block**

In `README.md`, replace the `## Install` section so it reads:

```markdown
## Install

### Option 1: Prebuilt binary (no Python required)

Download the latest release for your platform from
<https://github.com/Gharib89/crm/releases/latest>:

- Linux x86_64: `crm-linux-x86_64`
- Windows x86_64: `crm-windows-x86_64.exe`

**Linux:**

```bash
curl -L -o crm https://github.com/Gharib89/crm/releases/latest/download/crm-linux-x86_64
chmod +x crm
sudo mv crm /usr/local/bin/
crm --version
```

**Windows (PowerShell):**

The downloaded `.exe` will be marked as coming from the internet and may
trigger a SmartScreen warning on first run. Unblock it once:

```powershell
Unblock-File .\crm-windows-x86_64.exe
Rename-Item .\crm-windows-x86_64.exe crm.exe
# Move crm.exe somewhere on your PATH
.\crm.exe --version
```

Glibc compatibility: the Linux binary is built on Ubuntu 22.04 (glibc 2.35)
and runs on any Linux distribution with glibc ≥ 2.35.

### Option 2: From source (development)

```bash
# From source (local dev)
cd d365/agent-harness
pip install -e .

# Verify the command is on PATH
which crm
crm --version
```
```

(Adjust the existing source-install snippet to whatever the current README has — preserve its substance, only re-label it as "Option 2".)

- [ ] **Step 3: Verify Markdown renders**

Run:

```bash
grep -c "Prebuilt binary" README.md
```

Expected: `1`.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document prebuilt binary install for Linux and Windows"
```

---

## Final verification checklist

After all eight tasks are done:

- [ ] `pyinstaller crm.spec` succeeds locally on Linux; `./dist/crm --version` works
- [ ] `scripts/build.sh` runs end-to-end and produces `dist/crm`
- [ ] `.github/workflows/build.yml` passes on push to `main` with both OS matrix entries green and artifacts uploaded
- [ ] Downloading the `crm-linux-x86_64` and `crm-windows-x86_64.exe` artifacts from a passing CI run and running them on a clean machine prints the version
- [ ] Pushing a `v*` tag triggers `release.yml`, which publishes a GitHub Release containing both binaries
- [ ] README documents both install paths and the SmartScreen workaround
