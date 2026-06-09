# `crm update` + startup update nudge — design

Date: 2026-06-09
Status: approved, pre-implementation

## Problem

`crm` ships through two channels:

1. **R2 prebuilt binary** — PyInstaller onedir, installed by `scripts/install.sh`
   (Linux, `~/.local/share/crm` + symlink `~/.local/bin/crm`) or
   `scripts/install.ps1` (Windows, `%LOCALAPPDATA%\Programs\crm`). Self-contained,
   no Python. Published to Cloudflare R2 (`crm-cli-releases` bucket) under
   `latest/` and `vX.Y.Z/`, with `SHA256SUMS` and the install scripts at the
   bucket root.
2. **pip / uv** — `uv tool install git+…` or `pip install -e .`. A real Python
   environment.

Today there is no in-CLI way to update. A user must remember the install
one-liner. The bundled agent skill (`crm/skills/`, copied into
`~/.claude/skills/crm/` etc. by `crm skill install`) goes stale the moment the
binary is updated, because the installed copy is a snapshot taken at
`skill install` time.

Goal: a `crm update` command that self-updates the binary channel and refreshes
the installed skill, plus a non-intrusive startup nudge when a newer version is
available.

## Decisions (from brainstorming)

- **Behavior**: explicit `crm update` command **plus** a startup "update
  available" nudge. No silent auto-apply.
- **Update reach**: binary channel self-updates; pip/uv channel only prints the
  correct upgrade command (never shells out to pip/uv).
- **Skill refresh**: re-copy the bundled skill into the targets that are
  **already installed**, not all three, not none.
- **Startup check**: cached, throttled (24h), opt-out. Best-effort, never blocks
  or errors the real command.
- **Swap mechanism**: re-run the canonical install script fetched from R2
  (reuse the battle-tested verify+swap+relink+PATH logic; no duplication).

## Channel detection

```
frozen = getattr(sys, "frozen", False)
```

`frozen` true ⇒ R2 binary channel. Else pip/uv channel; distinguish uv from pip
by whether `sys.executable` resolves under a `uv`/`tools` path (best-effort
string check) to choose the printed command.

| Channel | `crm update` action |
|---|---|
| R2 binary | Self-update via install script (below), then refresh skill. |
| uv tool | Print `uv tool upgrade crm`. Refresh skill. No mutation of the env. |
| pip | Print `pip install -U git+https://github.com/Gharib89/crm`. Refresh skill. |

## Self-update (binary channel)

1. Download the canonical install script from R2 to a temp file
   (`{BASE_URL}/install.sh` or `/install.ps1`) using the bundled `requests`.
   Honor `CRM_INSTALL_BASE_URL` override (same env the scripts already read).
2. Execute it:
   - **Linux/macOS**: `sh <tmp>` synchronously. The script downloads `latest`,
     verifies SHA256, replaces `~/.local/share/crm`, relinks. The running
     onedir's files are on disk but the script `rm -rf`s the dir and recreates
     it — safe on POSIX because the inode stays open for the running process.
   - **Windows**: a running `crm.exe` cannot be replaced in place. Launch
     `powershell -File <tmp>` **detached** and exit immediately. The script
     replaces the install dir after our process is gone; it already prints
     "open a new shell".
3. Surface the script's exit code in the command envelope.

No re-implementation of verify/swap/PATH logic — the install scripts remain the
single source of truth for both first-install and update.

## Skill refresh

After a successful update (binary) — and unconditionally on pip/uv since the
new code is already importable — re-copy the bundled skill tree into every
target that is **already present**:

```
present = [t for t in SKILL_TARGETS if (SKILL_TARGETS[t] / "SKILL.md").exists()]
```

For each present target, force-copy `crm/skills/` → target dir (same logic as
`skill install --force`). Extract that copy into a shared helper
`_force_copy_skill(src_dir, dest_dir)` in `crm/commands/skill.py`, reused by both
`skill_install` and the updater. Never create a target the user never chose.
`crm update --no-skill` skips this step entirely.

Note: on the **binary** channel the bundled skill only changes *after* the swap,
which (on Windows) happens in a detached process after we exit. So on the binary
channel the skill refresh reads the **newly swapped** files only on the *next*
`crm update`/`skill install`. Document this: on binary channel the skill is
refreshed from the just-installed tree on POSIX (same process, dir already
swapped) but on Windows the swap is detached — instruct the user that the skill
re-copies on the next run, or run `crm skill install --force` after reopening the
shell. (Acceptable: skill staleness is low-severity and self-heals.)

## Startup nudge

Hook in the `cli()` group callback, after `setup_logging`. **Skip entirely** if
ANY of:

- not frozen (only the binary channel can act on the nudge),
- `--json` mode,
- `not sys.stdout.isatty()`,
- `os.environ.get("CI")` set,
- `CRM_NO_UPDATE_CHECK` set,
- the invoked subcommand is `update` (it reports freshness itself).

Otherwise, read the throttle/cache file `~/.crm/update_check.json` (honors
`CRM_HOME`), shape `{"checked_at": <iso8601>, "latest": "X.Y.Z"}`:

- **Print from cache** (instant, no network): if cached `latest` is newer than
  `__version__`, emit ONE line to **stderr**:
  `crm <cur> → <latest> available; run 'crm update'`.
- **Refresh in background**: if `checked_at` missing or older than 24h, spawn a
  daemon thread that GETs `{BASE_URL}/latest/VERSION` with a 1.5s timeout, writes
  the cache file, and exits. It never prints (the *next* invocation reads the
  fresh cache) and swallows every exception. Never joined — cannot delay or fail
  the real command.

`Date.now()`-style timestamps come from the runtime; the cache uses real wall
clock here (this is production code, not a workflow script).

Version comparison: strip a leading `v`, split `X.Y.Z`, compare as int tuples.
Non-numeric / malformed → treat as "not newer" (silent).

## R2: publish a version marker

`release.yml` currently publishes archives, `SHA256SUMS`, and the install
scripts. Add a tiny `VERSION` object so the startup check has a cheap,
rate-limit-free, same-trust-boundary source:

```
echo "${TAG#v}" > artifacts/VERSION
wrangler r2 object put "$bucket/latest/VERSION" --file=artifacts/VERSION \
  --content-type="text/plain; charset=utf-8" --remote
# also under ${TAG}/VERSION for symmetry
```

Using R2 (not the GitHub releases API) keeps the check on the same CDN as the
binary, with no API token and no rate limits.

## New / changed files

- **`crm/core/updater.py`** (pyright **strict**) — pure logic, no Click:
  `detect_channel() -> Literal["binary","uv","pip"]`,
  `latest_version(base_url) -> str | None`,
  `is_newer(current, latest) -> bool`,
  `read_cache()/write_cache()`, `should_check(cache, now) -> bool`,
  `run_self_update(base_url) -> int` (returns script exit code),
  `installed_skill_targets() -> list[str]`,
  `upgrade_hint(channel) -> str`.
- **`crm/commands/update.py`** — thin Click wrapper:
  `crm update [--no-skill] [--check]`. `--check` reports current vs latest and
  exits without mutating. Follows command-layer IO conventions
  (OSError-wrapped file ops → clean envelope; mutually-exclusive misuse →
  `click.UsageError`).
- **`crm/cli.py`** — register `update` in the lazy group; add the gated
  startup-nudge call in `cli()`.
- **`crm/commands/skill.py`** — extract `_force_copy_skill(src, dest)` shared
  helper; `skill_install` and updater both call it.
- **`.github/workflows/release.yml`** — publish `latest/VERSION` + `${TAG}/VERSION`.
- **Tests** (`crm/tests/`): channel detection (monkeypatch `sys.frozen`),
  `is_newer` matrix incl. malformed, skip-condition matrix for the nudge, cache
  throttle (inject `now`), `installed_skill_targets` (tmp dirs), `--check`
  envelope, self-update invokes script with right URL (mock subprocess +
  requests). All test files `# pyright: basic`.
- **Docs**: `README.md` Update section; `docs/how-to/update.md`;
  `docs/reference/cli.md` (auto/section for `update`); skill reference if the
  workflow note changes. (`mkdocs build --strict` must pass.)

## Error handling

- `crm update` is a mutating command → full `ctx.emit` envelope. Script
  download / verify / non-zero exit → clean error envelope, nonzero exit.
- Startup nudge → best-effort: wraps all work in `try/except Exception: pass`,
  never touches exit code, never prints on error.

## Out of scope (YAGNI)

- Auto-apply / silent background self-update.
- pip/uv automatic upgrade (only print the command).
- Delta/partial updates, rollback, multi-version pinning beyond existing
  `CRM_VERSION` env (the install scripts already honor it).
- macOS-specific binary channel (no macOS binary is published today).

## Verification criteria

1. `crm update --check` on a frozen build prints current+latest, mutates
   nothing, exit 0.
2. `crm update` on Linux frozen build re-runs `install.sh`, ends on the latest
   version (`crm --version`), refreshes only already-installed skill targets.
3. On pip/uv, `crm update` prints the correct upgrade command and refreshes the
   skill, mutates no environment.
4. Startup nudge: with a stale cache claiming a newer version, a frozen TTY run
   prints exactly one stderr line; `--json`, non-TTY, `CI`, and
   `CRM_NO_UPDATE_CHECK` each suppress it; the real command's output and exit
   code are unaffected.
5. `pytest` green; `pyright --pythonpath .venv/bin/python --pythonversion 3.9`
   clean on `crm/core/updater.py`; `mkdocs build --strict` passes.
