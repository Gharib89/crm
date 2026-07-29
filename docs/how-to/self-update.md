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
  keeps working. On Linux it confirms the outcome on the last line — `Updated crm
  1.2.3 -> 1.2.4.`, or an already-up-to-date note naming the current version when
  there was nothing to do. A checksum mismatch or download failure leaves the
  existing install untouched and exits non-zero. **On Windows the swap itself is
  deferred** — see below.
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

On the Windows binary install, PyInstaller's bootloader holds a file inside the
running bundle open for the whole life of the process, so `crm.exe` can never
replace its own install while it is the one running `self-update`. So the new
bundle is staged beside the install and a detached copy of the *staged* `crm.exe`
is launched to finish the job; the command you ran then exits, prints:

```
Staged crm 1.2.4. It is applied as crm exits; the next run reports the outcome.
```

and the finisher takes over: once it sees the parent process gone, it replaces
the bundle's files in place (one file at a time, not by renaming the directory,
because the running `crm.exe`'s images are open files inside it), re-syncs
installed skills/completion, and records the outcome for the *next* `crm` command
to report (below). Running `self-update` again before that finishes reports the
same staged version instead of starting a second swap:

```
Update to crm 1.2.4 is already staged by an earlier run and is applied as
that crm exits; the next run reports the outcome.
```

The previous bundle is parked alongside the install as `crm.old-<pid>`, and the
staged payload the finisher ran from is parked as `crm.new-<pid>`; both are
cleaned up automatically on a later `self-update` run, once they are no longer in
use. If the swap can't complete, the files that had moved are put back, so the
existing install is left working, and the error points you at `install.ps1` as
the fallback.

Writing inside a program directory tends to wake a virus scanner or the search
indexer, and while one of those holds a file open the move is refused. Each step of
the swap is therefore retried — for up to a couple of seconds *per step*, so a
`self-update` on Windows may pause, and a heavily-scanned install can pause for
longer than that overall. If a lock survives a step's whole window,
something is holding the file open persistently rather than for the length of a
scan — the error says so, to distinguish that from the transient case.

Rarely, putting them back can fail too — a file locked in the meantime, say. The
error says so explicitly, and names the `crm.old-<pid>` directory: with the
install then split across the two, that directory holds the only copy of the
files that moved. **Keep it** — copy it somewhere safe before you re-run
`install.ps1`, because a later `self-update` reaps parked directories.

### The next run reports a Windows swap's outcome

Because the finisher runs after `self-update` has already exited, no command is
still open to print a confirmation or a failure. Instead, the **next** `crm`
command — any command, not just `self-update` — prints a one-off notice once the
finisher has recorded an outcome:

```
Finished updating crm to 1.2.4.
```

or, if the swap could not complete:

```
The last crm update to 1.2.4 could not be applied: <reason>. Your install is
unchanged (still 1.2.3). Details: C:\Users\you\.crm\update.log
```

A skill/completion refresh warning from the finisher, if any, follows on its own
line. The notice prints on a human terminal only (never under `--json`, and never
when stderr isn't a terminal) and is reported exactly once — reading it deletes
the record. It is **not** gated by `CRM_NO_UPDATE_CHECK` or `CI`: those opt out of
the passive *update-available* check, not the outcome of an update you already
ran. For the same reason it is not suppressed on a `self-update` run either — a
second `self-update` is exactly where you would look for the first one's outcome.

Because that notice is single-shot, the finisher also appends one line per attempt
to `${CRM_HOME:-~/.crm}/update.log` — the file the failure notice names. That is
where to look if you scrolled past the notice, or if the update ran under `--json`
or a non-terminal stderr, where no notice is printed at all:

```
2026-07-28T09:14:02 ok 1.2.3 -> 1.2.4
2026-07-29T11:02:55 FAILED 1.2.4 -> 1.2.5: Could not replace the installed files
in C:\Users\you\AppData\Local\Programs\crm: [WinError 32] ...
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

On the frozen path, a normal in-process swap emits `updated: true` with
`from_version`/`to_version`. A **deferred Windows swap** (above) emits
`updated: false`, `pending: true`, `reason: "swap-deferred"` (freshly staged) or
`reason: "swap-already-staged"` (one was already pending), and the same
`from_version`/`to_version` — a scripter keying off `updated` will not mistake a
staged-but-not-yet-applied payload for a completed upgrade. The outcome itself
only ever shows up in the next command's *human* notice (above); under `--json`
it is never mixed into an unrelated command's envelope.

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

On a **deferred Windows swap** the finisher does this work instead, after the new
bundle is in place — refreshing earlier would leave a new skill tree beside the
old binary. So the deferred envelope carries no `data.skills`, and a refresh
failure shows up as an extra line under the next run's outcome notice (above).

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
