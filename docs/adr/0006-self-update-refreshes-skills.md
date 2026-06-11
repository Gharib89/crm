---
status: accepted
---

# `self-update` refreshes installed agent skills (version-mismatch keyed, every install method)

The repo requires the shipped agent skill (`crm/skills/`) to stay in sync with the
CLI — a stale installed skill teaches an agent the wrong flags. `crm skill install`
copies that tree into an agent's skill dir, but nothing kept it current after an
upgrade: a user who upgraded the CLI (frozen self-update *or* `pip install -U crm`)
was left with whatever skill they last installed by hand. Issue #225 closes that gap.

We record every install destination in `${CRM_HOME:-~/.crm}/installed-skills.json`
(`{target, dest, installed_version}` per entry; `installed_version` is the CLI version
at install time, since the skill ships in the wheel and has no version of its own).
On every non-`--check` `crm self-update`, we walk that registry and re-copy the
bundled skill into each recorded `dest` **whose `installed_version` differs from the
current bundled version** — a no-op when already in sync. This fires on **both**
install methods: after a frozen bundle swap (the new skill is on disk at the swapped
`install_dir`), and on a pip/uv install where `self-update` does not upgrade the
binary itself but the wheel's skill is already new.

## Considered options

- **Refresh on frozen self-update only (the issue's literal "after upgrading").**
  Rejected: pip/uv is the common dev install, and on that path `self-update` returns
  early without upgrading, so the skill would never be refreshed by the CLI at all —
  the user would have to remember `crm skill install --force` after each
  `pip install -U`. That defeats the sync guarantee for most installs. Keying the
  refresh off a **bundled-vs-recorded version mismatch** instead of the binary-swap
  event covers pip cleanly: after `pip install -U crm`, the next `crm self-update`
  sees the wheel's newer version and re-syncs the recorded dests. The trade-off is
  that `crm self-update` on a pip install is no longer a pure no-op — it still prints
  the version check and the `pip install -U crm` hint, but now also re-syncs skills.
- **Guess the default targets instead of a registry.** Rejected: a user may have
  installed to a custom `--dest`, to a non-default `--target`, or to several at once;
  guessing `~/.claude/skills/crm` et al. would miss custom dests and re-create skills
  in targets the user never used. The registry records exactly where skills live, so
  refresh touches only real installs.
- **Recreate a recorded dest that the user has since deleted.** Rejected as
  surprising: a vanished `dest` means the user removed the skill out-of-band. Refresh
  treats that as an uninstall — it **prunes** the entry and does not recreate the
  folder, rather than resurrecting a skill the user deliberately deleted.
- **Let a failed skill refresh fail the command.** Rejected: a permission error on
  one skill dir must not abort a successful binary update. Per-dest errors are
  reported in the envelope (`data.skills[].status = "error"`) and the overall result
  stays `ok:true` when the binary update itself succeeded.

## Consequences

- `crm self-update` now reads and writes `${CRM_HOME}/installed-skills.json` and may
  copy files into recorded skill dirs as a side effect. The per-dest outcome
  (`refreshed` / `skipped` / `pruned` / `error`, with `from_version` → `to_version`)
  is surfaced in `data.skills`, so agents see exactly what changed.
- `installed-skills.json` is internal CLI state, not contract vocabulary —
  [CONTEXT.md](../../CONTEXT.md) is intentionally left untouched. The file format is
  a top-level object (`{"skills": [...]}`) so future keys land without a break, and is
  read tolerantly (missing/corrupt → empty list, never raises).
- Re-running `self-update` when everything is already in sync is a cheap no-op (a
  version compare per recorded dest, no copy).
