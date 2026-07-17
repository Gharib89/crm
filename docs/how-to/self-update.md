# How-to: self-update

Keep the `crm` CLI current. See the [CLI reference](../reference/cli.md) for every flag.

## Check for a newer release

```bash
crm self-update --check
```
Reports the running version, the latest published version, and whether an update
is available — without modifying anything. Works on every install type. Under
`--json` it emits the standard envelope (`data.current`, `data.latest`,
`data.update_available`); if the release server is unreachable it returns a clean
error envelope rather than hanging or crashing.

## Upgrade (install-method-aware)

```bash
crm self-update
```
`self-update` detects how `crm` was installed and upgrades it the right way:

- **Install-script binary (frozen)** — downloads the platform archive, verifies
  it against the published `SHA256SUMS` (the same integrity check the install
  script uses), and swaps the bundle in place — the `crm` launcher on your PATH
  keeps working. A checksum mismatch or download failure leaves the existing
  install untouched and exits non-zero.
- **uv tool / pipx** — force-reinstalls from the latest release tag
  (`uv tool install --force git+https://github.com/Gharib89/crm@vX.Y.Z`, or the
  pipx equivalent). `--force` is used because uv/pipx pin the git commit and a
  plain upgrade can silently no-op. On a terminal it prints the command and asks
  `Proceed? [y/N]` (default No); non-interactively it runs only with `--yes`.
  If `uv`/`pipx` isn't on your PATH, it falls back to printing the command.
- **editable / `pip install git+…` / unknown** — prints the correct git-based
  upgrade command and never runs anything.

```bash
crm self-update --yes     # run the uv/pipx reinstall without the prompt (scripts/CI)
```

## The `--json` contract

Under `--json`, a non-frozen `self-update` emits these `data` fields:

- `install_method` — `frozen` \| `uv-tool` \| `pipx` \| `editable` \| `pip-git` \|
  `unknown`.
- `current`, `latest`, `update_available` — the version comparison.
- `command` — the exact upgrade command string for this install method.
- `executed` — whether `self-update` ran that command.
- `exit_status` — the command's exit code (present only when `executed` is true).
- `reason` — why nothing ran (present when `executed` is false): `up-to-date`,
  `manual-install-method`, `no-tty-without-yes`, `declined`, or `tool-not-on-path`.

If an attempted uv/pipx upgrade command itself exits non-zero, `self-update`
emits an `ok:false` envelope (exit 1) — so a script sees a non-zero process exit
on a failed upgrade — while still carrying the same `data` fields
(`install_method`, `command`, `executed:true`, `exit_status`) alongside the
`error` message, so you can inspect what ran.

`--check` is unchanged and method-agnostic (`data.current`, `data.latest`,
`data.update_available`).

## Keeping installed skills in sync

Every non-`--check` `self-update` re-syncs the agent skills you installed with
[`crm skill install`](skill.md), so the shipped `SKILL.md` never lags the CLI. It
reads the install registry (`${CRM_HOME:-~/.crm}/installed-skills.json`) and, for
each recorded destination whose version is stale, re-copies the bundled skill
tree. This fires on both install types — after a frozen bundle swap, and on a
`pip`/`uv` install once the upgraded wheel is in place.

The per-destination outcome is reported under `data.skills` (a list of
`{dest, from_version, to_version, status}`, `status ∈ refreshed | skipped |
pruned | error`):

- **refreshed** — the skill was re-copied to the current version.
- **skipped** — already current; no copy.
- **pruned** — the folder was deleted out-of-band, so its registry entry is
  dropped (the folder is *not* recreated).
- **error** — copying that destination failed (e.g. permissions); the entry is
  kept for a later retry.

A skill-refresh failure never aborts the binary update — the command still
reports `ok:true` when the upgrade itself succeeded.

## The passive update notice

On an interactive terminal, `crm` checks at most once every 24 hours whether a
newer release exists and prints a one-line notice on stderr after a command
finishes — at most once per 24 hours (tracked via `notified_at` in the cache),
so it does not reprint on every command. A newly discovered version resets that
gate so the new release is surfaced promptly. The probe runs in the background
and never delays a command.

It is silent — and skips the network entirely — in any of these cases:

- `--json` output mode (machine-readable output is never polluted),
- stderr is not a terminal (pipes, redirects, agents),
- the `CI` environment variable is set,
- the `CRM_NO_UPDATE_CHECK` environment variable is set,
- the command being run is `self-update` itself (it owns its own update messaging;
  the running process still reports the pre-update version, so the notice would
  otherwise tell you to upgrade to the release you just installed).

Set `CRM_NO_UPDATE_CHECK=1` to opt out permanently:

```bash
export CRM_NO_UPDATE_CHECK=1
```

The last check result is cached under `${CRM_HOME:-~/.crm}/update-check.json`.
