# Design: Standalone `crm` CLI Binaries for Windows + Linux

**Date:** 2026-05-18
**Status:** Approved (brainstorming)
**Owner:** Ahmed Gharib

## Goal

Ship the `crm` CLI as single-file executables for Windows (x86_64) and Linux (x86_64) so end users can run it without installing Python or any Python packages. Source remains a normal Python project; binaries are a distribution artifact.

## Approach

Use **PyInstaller** in `--onefile` mode to freeze the CPython interpreter and all runtime dependencies (`click`, `requests`, `requests_ntlm`, `prompt_toolkit`, `cryptography`, etc.) into a single self-extracting binary per OS. Build both locally (for dev iteration) and in GitHub Actions (for releases).

## Outputs

| Artifact | Builder | Approx. size | Notes |
|---|---|---|---|
| `crm-linux-x86_64` | `ubuntu-22.04` (glibc 2.35) | ~25 MB | Runs on any Linux with glibc ≥ 2.35 |
| `crm-windows-x86_64.exe` | `windows-latest` | ~20 MB | Unsigned; first-run SmartScreen warning expected |

## Architecture

### Components

1. **`crm.spec`** — PyInstaller spec file, committed to repo. Source of truth for the freeze.
   - Entry script: `crm/__main__.py`
   - `datas=[('crm/skills', 'crm/skills'), ('crm/README.md', 'crm')]` — bundles non-Python package data that `setup.py`'s `package_data` declares but PyInstaller does not honor automatically
   - `hiddenimports=[...]` — populated reactively if smoke tests reveal missing dynamic imports (likely candidates: `requests_ntlm`, `prompt_toolkit` terminal backends, `cryptography` backends)
   - `console=True`, `onefile=True`

2. **`scripts/build.sh`** — Linux/macOS local build script.
   - Creates/activates a venv, installs the project + `pyinstaller`, runs `pyinstaller crm.spec`, prints output path.

3. **`scripts/build.ps1`** — Windows local build script. Same behavior in PowerShell.

4. **`.github/workflows/build.yml`** — CI sanity build.
   - Triggers: `push` and `pull_request` against `main`.
   - Matrix: `os: [ubuntu-22.04, windows-latest]`.
   - Steps:
     1. `actions/checkout@v4`
     2. `actions/setup-python@v5` with Python 3.11
     3. `pip install -e .[dev] pyinstaller`
     4. `pytest` (unit tests only; D365 E2E suite skipped unless creds present)
     5. `pyinstaller crm.spec`
     6. Smoke test: run `dist/crm --version` and `dist/crm --help`; non-zero exit fails the build
     7. Upload artifact via `actions/upload-artifact@v4`
   - No GitHub Release created on this workflow.

5. **`.github/workflows/release.yml`** — release build.
   - Trigger: `push` of a tag matching `v*`.
   - Pre-flight: assert tag version equals `setup.py` version; fail fast if mismatched.
   - Same matrix and build steps as `build.yml`.
   - Rename outputs to `crm-linux-x86_64` and `crm-windows-x86_64.exe`.
   - Use `softprops/action-gh-release@v2` (or `gh release create`) to create a GitHub Release and attach both binaries.

### Data flow

**Build:** source tree → `pyinstaller crm.spec` → bootloader stub + zipped Python runtime + zipped deps + `crm/` package + `crm/skills/*.md` → single binary in `dist/`.

**Runtime:** binary launches → PyInstaller bootloader extracts archive to `$TMPDIR/_MEIxxxxxx/` → sets `sys._MEIPASS` to that path → runs `crm/__main__.py` → `crm.cli:cli` (Click entry point) executes → temp dir cleaned on exit.

Skill file resolution at `crm/cli.py:1106` uses `Path(_crm_pkg.__file__).resolve().parent / "skills" / "SKILL.md"`. Under PyInstaller, `__file__` resolves to `sys._MEIPASS/crm/cli.py`, so `parent / "skills"` lands on `sys._MEIPASS/crm/skills`, which is exactly where `datas=` places it. No code changes needed.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| `cryptography` (transitive via `requests_ntlm`) ships binary wheels that PyInstaller occasionally mis-bundles | CI smoke test runs the built binary; failure fails the build before release |
| `prompt_toolkit` uses runtime imports for terminal backends | Add `hiddenimports` entries reactively if the REPL smoke test fails. Document candidates in the spec file when added |
| `package_data` from `setup.py` is not honored by PyInstaller | Bypass entirely — declare skill bundle explicitly via `datas=` in `crm.spec` |
| Linux binary built on a newer glibc would not run on older distros | Pin builder to `ubuntu-22.04` (glibc 2.35). If older targets are reported by users, downgrade to `ubuntu-20.04` (glibc 2.31) |
| Windows SmartScreen warns on unsigned `.exe` | Document `Unblock-File` PowerShell workaround in README install section. Revisit code signing if user volume justifies the cost |
| AV false positives on PyInstaller bootloader | Known industry issue. Document in README. If recurrent for users, consider switching to Nuitka or adding signing |
| Binary size (~25 MB) | Accepted. Not optimizing in this iteration (no UPX, no `--exclude-module` tuning) |
| Tag/version drift between git tag and `setup.py` | Release workflow pre-flight check asserts equality and fails the release if mismatched |

## Testing

- **Existing `pytest` suite** runs in CI before the PyInstaller step. Test failures fail the build.
- **CI smoke test** on the frozen binary: `crm --version` and `crm --help` must exit 0 on both OSes.
- **Manual smoke test on each release** (documented in README or release checklist):
  1. Download `crm-linux-x86_64` on a clean Linux container (e.g., `debian:12`), `chmod +x`, run against a real D365 endpoint (`crm whoami`).
  2. Download `crm-windows-x86_64.exe` on a clean Windows VM, run `crm whoami`.

## Out of Scope

- macOS binary
- arm64 builds (Linux or Windows)
- Auto-update mechanism
- Installer packaging: `.msi`, `.deb`, `.rpm`, Homebrew, Scoop, winget
- Code signing (Windows EV or standard)
- Aggressive binary size reduction (UPX, module exclusion)
- Nuitka or alternative freezing tools

## Open Questions

None at design time. `hiddenimports` list will be finalized during implementation when smoke tests reveal what (if anything) PyInstaller misses.
