# Design: Fast-loading `crm` CLI + script-based install via Cloudflare R2

**Date:** 2026-06-02
**Status:** Approved (brainstorming)
**Owner:** Ahmed Gharib
**Approach:** A — PyInstaller `--onedir` + lazy imports + R2-hosted install script
**Supersedes (delivery only):** `2026-05-18-pyinstaller-binaries-design.md` keeps the `--onefile` outputs; this design replaces the runtime/distribution model.

## Goal

Make the `crm` CLI start fast and trivial to install on Windows, without giving up a "download and run" distribution. Source stays a normal private Python project; binaries are a distribution artifact published to a public Cloudflare R2 bucket.

## Problem (measured 2026-06-02)

| Path | `crm --version` | Cause |
|---|---|---|
| Bare CPython | ~27ms | floor |
| Source `python -m crm --version` | ~250ms | eager imports at top + bottom of `cli.py` |
| **Frozen `--onefile` `./crm --version`** | **~410–530ms** | above **+ ~160–280ms self-extraction** of the 24MB bundle to a temp dir on **every** launch (worse on Windows: cold disk + AV scans the bootloader) |
| Frozen `--onedir` `./crm/crm --version` (prototyped) | **~330ms** | no extraction; remaining cost is eager imports |

Three independent problems, addressed by three independent changes:

1. **Per-launch extraction** — `--onefile` unpacks the whole bundle every run. Fix: `--onedir`.
2. **Eager imports** — `crm --version`/simple commands pay the full ~220ms import cost they don't need. Fix: lazy imports.
3. **Distribution** — repo `Gharib89/crm` is **private**, so release assets are not anonymously downloadable. Fix: publish artifacts + install script to a public Cloudflare R2 bucket (reuses the existing Cloudflare/wrangler setup already used for the docs site).

## Target / success criteria

| Criterion | Now | Target | Verification |
|---|---|---|---|
| `crm --version` (frozen) | ~410–530ms | **≤ 100ms** | `/usr/bin/time` locally + CI smoke |
| `crm <cmd>` real invocation | full eager cost | only that command's deps import | importtime + unit test |
| Windows install | manual: download, Unblock, PATH by hand | `irm https://pub-<hash>.r2.dev/install.ps1 \| iex` → on PATH, zero auth | clean Win11 VM |
| Skill resolution under onedir | n/a | unchanged | ✅ already proven: built onedir, `crm skill --help` resolved `_internal/crm/skills/SKILL.md` |

## Architecture

### Components

1. **`crm.spec`** — flip `--onefile` → `--onedir`.
   - `EXE(..., exclude_binaries=True)` (drop `a.binaries`, `a.zipfiles`, `a.datas` from the `EXE()` call).
   - Add a `COLLECT(exe, a.binaries, a.zipfiles, a.datas, name='crm')` step.
   - Output: `dist/crm/` containing `crm` / `crm.exe` plus `_internal/`.
   - `datas=[('crm/skills', 'crm/skills')]` unchanged — verified to land at `_internal/crm/skills/` and resolve via the existing `Path(_crm_pkg.__file__).resolve().parent / "skills"` logic. **No application code change for skill resolution.**

2. **`crm/cli.py`** — lazy imports (the only application code change). See "Lazy-import strategy" below.

3. **`scripts/install.ps1`** (new) — Windows installer script (see "install.ps1").

4. **`scripts/install.sh`** (new) — Linux mirror of install.ps1 (parity; ~20 lines).

5. **`.github/workflows/release.yml`** — build `--onedir`, zip/tar per-OS, add a `publish-r2` job that uploads artifacts + install scripts to R2. Update smoke-test paths.

6. **Docs** — update install instructions to the R2 script flow in both places that currently document it:
   - **`README.md`** "## Install" — currently tells users to `curl -L https://github.com/Gharib89/crm/releases/latest/download/…` and Unblock the `.exe`. That GitHub URL **does not work anonymously** (private repo) — it is already broken. Replace with the R2 one-liners as the primary path; keep the "from source" `pip install -e .` path; keep the SmartScreen/`Unblock-File` note (binary is still unsigned).
   - **`docs/getting-started/install.md`** — the mkdocs page (publishes to the Pages site). Currently links to the private releases page. Replace the "Prebuilt binary" section with the R2 one-liners; keep "From source".
   - New primary instructions:
     - Windows: `irm https://pub-<hash>.r2.dev/install.ps1 | iex`
     - Linux: `curl -fsSL https://pub-<hash>.r2.dev/install.sh | sh`
   - Optional: a manual-download fallback (direct R2 zip/tar URLs) for users who don't want to pipe a script.

### Data flow

**Build (CI):** source → `pyinstaller crm.spec` → `dist/crm/` (onedir) → archived per-OS (`crm-windows-x86_64.zip`, `crm-linux-x86_64.tar.gz`) → attached to a (private) GitHub Release **and** uploaded to R2.

**Install (user, Windows):** `irm …/install.ps1 | iex` → script downloads `latest/crm-windows-x86_64.zip` from R2 (no auth) → extracts to `%LOCALAPPDATA%\Programs\crm` → adds dir to user PATH → runs `crm --version` to verify.

**Runtime:** `crm.exe` launches → no extraction (files already on disk in `_internal/`) → CPython runs `crm/__main__.py` → `crm.cli:cli` → lazy group imports only the invoked subcommand's module.

## Lazy-import strategy

Current `cli.py` eagerly imports, on **every** invocation:
- top (lines ~20–26): `from crm.core import connection as conn_mod`, `from crm.utils.d365_backend import D365Backend` — both pull the `requests` / `requests_ntlm` / `spnego` / `cryptography` stack (~190ms cumulative).
- bottom (lines ~272–284): all 10 command groups (`connection`, `entity`, `query`, `metadata`, `solution`, `data`, `action`, `async_ops`, `workflow`, `skill`) + `repl` — `repl` pulls `prompt_toolkit`.

(Confirmed not a cost driver: `crm.utils.repl_skin` already lazy-imports `prompt_toolkit` inside methods; importing the module is ~0.5ms. `ReplSkin()` in `CLIContext.__init__` stays as-is.)

Changes:

1. **Defer the connection/backend stack.**
   - Move `from crm.core import connection as conn_mod` and `from crm.utils.d365_backend import D365Backend` out of module top.
   - Put `D365Backend` under `if TYPE_CHECKING:` for the type annotations (already safe — `from __future__ import annotations` makes annotations strings).
   - Local-import both inside the `backend()` property where they are used.
   - Effect: the requests/NTLM/crypto stack no longer loads on the fast path.

2. **LazyGroup for subcommands.**
   - Replace the bottom-of-file eager group imports + `cli.add_command(...)` calls with a `click.Group` subclass that holds a static `name → "module:attr"` map and imports the target module only in `get_command()`.
   - Effect: `crm entity …` imports only `crm.commands.entity`; `crm --version` imports no command modules.

### Known trade-off (accepted)

`crm --help` enumerates subcommands to render each one's short help, so it still imports all command modules (~330ms). This path is rare and user-initiated. `crm --version` and direct command invocation — the hot paths — are fast. Not optimizing `--help` now (would require duplicating short-help text into the lazy map). Documented, deferred.

## install.ps1

Stable URL: `https://pub-<hash>.r2.dev/install.ps1`. One-liner:

```powershell
irm https://pub-<hash>.r2.dev/install.ps1 | iex
```

Behavior:

1. Resolve version: `latest` by default, or `$env:CRM_VERSION` (e.g. `v0.6.0`) for a pinned archive key.
2. Download `…/{latest|v X.Y.Z}/crm-windows-x86_64.zip` to a temp file (`Invoke-WebRequest`).
3. Extract to `$env:LOCALAPPDATA\Programs\crm`. Remove any existing install dir first → idempotent install/upgrade.
4. Add the install dir to **user** PATH via `[Environment]::SetEnvironmentVariable('Path', …, 'User')` only if absent. Warn that a new shell is required for PATH to take effect.
5. Verify: run `crm.exe --version` from the install dir and print the result.

`-Uninstall` flag: remove the install dir and strip the PATH entry. No download.

install.sh mirrors this for Linux: download `crm-linux-x86_64.tar.gz`, extract to `~/.local/share/crm`, symlink/`PATH` note for `~/.local/bin`, verify.

## Cloudflare R2 setup

### One-time, manual (run once by the owner)

```bash
wrangler r2 bucket create crm-cli-releases
wrangler r2 bucket dev-url enable crm-cli-releases   # prints the public https://pub-<hash>.r2.dev URL
```

Record the `pub-<hash>.r2.dev` host; it is hardcoded into `install.ps1`/`install.sh` and the README one-liner.

**Prerequisite:** the existing `CLOUDFLARE_API_TOKEN` GitHub secret (currently used for `pages deploy` in `docs.yml`) must have **R2 edit** permission. If it was scoped to Pages only, add R2 write scope (or mint a dedicated token). This is a hard prereq for the `publish-r2` job.

### Release workflow — `publish-r2` job

Runs after `build`, on `ubuntu-22.04`, reusing `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID`. Downloads the build artifacts, then for each OS archive uploads to both an immutable versioned key and a mutable `latest/` key:

```bash
npx wrangler@4.97.0 r2 object put crm-cli-releases/latest/crm-windows-x86_64.zip \
  --file=crm-windows-x86_64.zip --content-type=application/zip --remote
npx wrangler@4.97.0 r2 object put crm-cli-releases/${TAG}/crm-windows-x86_64.zip \
  --file=crm-windows-x86_64.zip --content-type=application/zip --remote
# …same for crm-linux-x86_64.tar.gz (content-type application/gzip)
# …and install.ps1 / install.sh to bucket root (text/plain; charset=utf-8)
```

`--remote` is required so uploads hit real R2 (not local dev storage). Command flags verified against current Cloudflare wrangler docs (`r2 object put`: `--file`, `--content-type`, `--remote`; `r2 bucket dev-url enable` for the public URL).

## Testing

- **CI smoke (updated paths):** `dist/crm/crm --version` and `dist/crm/crm --help` exit 0 on both OSes. Add a loose wall-clock guard: `--version` < 250ms (generous for CI runners; catches a regression back to onefile extraction).
- **Lazy-import regression test:** a unit test that invokes `crm --version` via Click's `CliRunner` (or a subprocess) and asserts `crm.commands.entity` (and the rest) are **not** in `sys.modules` afterward.
- **Manual release smoke:** clean Win11 VM → `irm …/install.ps1 | iex` → new shell → `crm whoami` against a real D365 endpoint. Repeat the upgrade path (run installer twice).
- **Docs:** `mkdocs build --strict` (already gated in `docs.yml`) must pass after the install-page edits; verify the R2 one-liners render and links resolve.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| `CLOUDFLARE_API_TOKEN` lacks R2 scope | Documented prereq; `publish-r2` fails fast and loud if so |
| Public R2 URL exposes the compiled binary to anyone with the link | Accepted by owner: binary only (no source, no creds — CLI uses the user's own D365 + creds). Can gate later with Cloudflare Access without changing the script materially |
| LazyGroup breaks `--help` formatting or shell completion | Covered by CI smoke (`--help` exit 0) + a help-output assertion |
| Unsigned `crm.exe` still triggers SmartScreen on first run | Documented in README; code signing remains out of scope |
| Windows MOTW blocks the downloaded zip | `Expand-Archive` handles MOTW on the zip; document `Unblock-File` fallback if a user reports it |
| onedir folder shipped incompletely (missing `_internal`) | Install script extracts the full archive atomically; CI smoke runs the extracted binary |

## Out of scope

- Code signing (Windows EV/standard)
- macOS and arm64 builds
- Auto-update mechanism
- winget / Scoop / MSI / `.deb` packaging
- Nuitka or alternative freezers
- UPX / aggressive size trimming
- Optimizing `crm --help` import cost

## Open questions

None at design time. The `pub-<hash>.r2.dev` host is determined during the one-time R2 setup and then hardcoded into the scripts + README.
