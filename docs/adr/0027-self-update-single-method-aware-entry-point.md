---
status: accepted
---

# `self-update` is the single, install-method-aware upgrade entry point

Decided in #872. `crm self-update` previously knew only two worlds: a frozen
PyInstaller binary (upgraded in place) and "everything else", for which it printed
one **factually wrong** hint — "Run `pip install -U crm` to upgrade" — and did
nothing. That command cannot work: `crm` is not published to PyPI, so
`pip install -U crm` fails to resolve. Every non-binary channel (uv tool, pipx,
editable, pip-from-git) was handed a broken command. This hurt exactly the users
steered *away* from the unsigned binary — uv-tool users on managed Windows
machines, and macOS users (no macOS binary is built at all).

## Decision

`self-update` is the one upgrade entry point for **every** install method. It
detects the method and does the right thing; the passive "new release available"
notice is unified to always say "Run `crm self-update` to upgrade", so the
command owns all method logic and the notice can never drift from it.

- **Install-method detection** — `detect_install_method()` in `crm/core/update.py`
  returns a fixed vocabulary: `frozen`, `uv-tool`, `pipx`, `editable`, `pip-git`,
  `unknown`. Best-effort path/marker sniffing (`is_frozen()` stays the frozen
  signal; `sys.prefix` under `uv/tools/` or a `uv-receipt.toml` marker → `uv-tool`;
  under `pipx/venvs/` or `pipx_metadata.json` → `pipx`; PEP 610 `direct_url.json`
  with `dir_info.editable` → `editable`, with `vcs_info` → `pip-git`; else
  `unknown`). No shelling out to probe, no install-time stamp. An ambiguous signal
  degrades to `unknown`.
- **All non-frozen methods converge on the latest release tag** (`@vX.Y.Z`, the
  same value the frozen path reads from R2 `latest/VERSION`), so "latest" means one
  thing everywhere. uv → `uv tool install --force git+…@vX.Y.Z` (force, because uv
  pins the git commit SHA and a plain `uv tool upgrade` can no-op); pipx → the
  equivalent forced reinstall; `pip-git`/`unknown` → `pip install -U git+…@vX.Y.Z`;
  `editable` → `git pull && pip install -e .` guidance.
- **Auto-run is confined to `uv-tool` and `pipx`** — isolated environments a
  force-reinstall can safely replace. It is consent-gated: on a TTY, print the exact
  command and prompt (default No); non-interactively, run only with `--yes`,
  otherwise print the command and emit the structured payload without executing. If
  the `uv`/`pipx` binary isn't on PATH, fall back to printing. `editable`/`pip-git`/
  `unknown` never auto-run — we don't own or safely know those environments.
- **`--json` contract** — the non-frozen payload carries `install_method`,
  `current`, `latest`, `update_available`, `command`, `executed`, and either
  `exit_status` (executed) or `reason` (`up-to-date`, `manual-install-method`,
  `no-tty-without-yes`, `declined`, `tool-not-on-path`). If an attempted uv/pipx
  upgrade command exits non-zero, the command emits an `ok:false` envelope (exit 1,
  so CI sees the failure) that still carries the same `data` fields plus the
  `error` message. `install_method` joins the CLI contract vocabulary
  (`CONTEXT.md`). `--check` is unchanged and method-agnostic.
- **Post-upgrade skill/completion refresh** — after a successful uv/pipx reinstall,
  the running process is the pre-reinstall package (stale, sometimes already
  removed), so the freshly installed `crm` is re-invoked via a guarded internal
  `--refresh-only` entry (which never re-enters the upgrade path, so there is no
  reinstall loop) to re-sync the dests recorded in `installed-skills.json`. This
  reuses the frozen path's "refresh via the new binary" pattern (ADR-0006). When no
  upgrade ran (up-to-date, printed guidance, declined), the running package *is* the
  current install, so the refresh runs in-process as before.

## Considered options

- **Keep printing a hint, but fix the string.** Rejected: uv/pipx are isolated
  environments where a one-command upgrade is both safe and expected; leaving them
  to copy-paste a git URL is the worse experience the issue set out to remove.
- **`uv tool upgrade crm`.** Rejected: uv prefers the locked commit SHA and can
  silently no-op a git-sourced tool upgrade; `--force …@vX.Y.Z` reliably fetches the
  tagged release.
- **Auto-run for plain-pip / editable / unknown too.** Rejected: those environments
  are not ours to force-reinstall — mutating them could clobber a user's venv or
  working tree. They only ever get printed guidance.
- **A `--method` override flag.** Deferred (not in v1); detection plus the printed
  command covers the observed cases.

## Out of scope

- Publishing `crm` to PyPI (upgrade paths stay git-based).
- Code-signing / notarizing the binary to fix the Windows SmartScreen block at its
  root, and building a macOS binary — tracked separately; this change only improves
  *steering* of blocked users toward `uv tool`.
