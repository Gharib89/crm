# The gap report: a re-run on an existing harness

## Contents

- [Dispositions](#dispositions)
- [Files the skill wrote](#files-the-skill-wrote)
- [Pins](#pins)
- [Deleted pieces and standing choices](#deleted-pieces-and-standing-choices)
- [Timing on a re-run](#timing-on-a-re-run)
- [The report](#the-report)

A run that finds `docs/agents/harness.md` is a re-run. Explore runs in full, as on a first run, and what it finds is compared with what the harness already has: each difference is a **gap**, the report lists them, and the run writes only the gaps the human approves. Every re-run ends in the confirm step, or in `harness: no gaps`. On a first run, the comparison in `## Files the skill wrote` and all of `## Pins` apply too, their Offers asked as step 4 questions; the contract check is a re-run's.

## Dispositions

Each gap kind takes exactly one disposition:

- **Write**: a row in the proposed-writes table, applied on the one approval; droppable by number unless the row says otherwise.
- **Offer**: a numbered question with a recommendation, asked before the table; its answer adds, changes or removes rows.
- **Install-check row**: a row in the same table carrying the install-check columns ([install-check.md](install-check.md) `## Presenting`), the unit passing the install check before the row is shown.
- **Report only**: listed under Not acted on or Standing choices; nothing is written.

| Gap | Disposition |
|---|---|
| The profile's `Schema:` trails the skill's `metadata.harness-schema` | Write: the migration, row 1, undroppable |
| A piece the skill writes is missing: `scripts/check.sh`, `.claude/hooks/check-hook.sh`, a hook entry, the cloud setup or its `SessionStart` entry (cloud-first only; a Ship bootstrap entry whose last command is the cloud setup is that entry, [cloud.md](cloud.md) `## Writing the cloud setup`), a vendored language server, the `CLAUDE.md` block | Write |
| A file the skill wrote lacks what its current template says | Write |
| A step the skill wrote whose evidence is gone (a `TURN_ROWS` row for a removed member, for a root now `Declined:` as a root or for one under an `Excluded:` prefix, a runner hook for a file kind no longer tracked, a cloud setup step for a removed stack, a browser step or `FULL_ROWS` row for a surface no longer detected, the `tags` step once no griffe row is left, the `dockerd` row once no `mcr` browser step or container tool needs it, a `LOCAL_ONLY` name whose row is gone) | Write: the removal |
| A `Root:` line whose manifest is no longer a candidate root ([detection.md](detection.md)): the manifest gone, a lockfile beside it, a root's `Workspace:` config naming it, or an `Excluded:` prefix holding it | Write: the removal |
| An `Excluded:` line whose prefix holds no tracked file | Write: the removal, its alternative in the runner config's `exclude:` and its prefix in `check.sh`'s `EXCLUDED` |
| A hook `timeout` off deadline plus max(10 s, deadline / 4) | Write |
| prek's git shim, `$(git rev-parse --git-path hooks)/pre-commit`, lacking `--skip-on-missing-config`, the mark `prek install --allow-missing-config` writes ([runner.md](runner.md)) | Offer: reinstall it with that flag, a change to this machine that commits nothing |
| A new root whose stack has a catalog entry | Write: its `TURN_ROWS` row and runner additions |
| New web UI evidence ([surfaces.md](surfaces.md)) | Write: its browser step, cloud-first, and its suite's `FULL_ROWS` row where surfaces.md `## Web UI` gives one |
| New library evidence ([surfaces.md](surfaces.md)) | Write: each `public API` default surfaces.md `## Library` proposes, its `FULL_ROWS` row, droppable; cloud-first, its `LOCAL_ONLY` name and `Local-only:` line where the catalog carries one, and the cloud setup's `tags` step with a griffe row |
| `Allowlist:` gained or lost `cdn.playwright.dev` | Write: each web UI member's browser step on the route it now takes, on `vendor` also where an MCR no left it unwritten, removing that member's `LOCAL_ONLY` name and `Local-only:` line; on `mcr` only after the MCR tag check ([surfaces.md](surfaces.md) `## Web UI`), a missing tag becoming its question; the `dockerd` row added for `mcr`, or removed for `vendor` where no container tool needs it |
| A now-available role: a role with no tool that its catalog entry now fills (a `public API` default whose baseline now exists included), or a `Constraints:` `Unavailable:` that no longer holds | Write |
| `check.sh` breaks its contract, and no Write above explains it | Offer: rewrite onto the current template, keeping every check the old file ran |
| A warm `edit` or `turn` time over budget | Offer: narrow, demote or override ([check-ladder.md](check-ladder.md) `## Timing the rungs`) |
| A new fixture tree ([detection.md](detection.md)): one with neither an `Excluded: <path prefix>` nor a `Declined: <path prefix> as excluded` line | Offer; its answer removes the steps a previous run wrote for anything under it when it excludes, and keeps them when it reads |
| A new candidate root ([detection.md](detection.md)): one with neither a `Root: <manifest>` nor a `Declined: <manifest> as a root` line | Offer; the steps a previous run wrote for it are no removal Write, its answer keeping them for a root and removing them for an ignore |
| `cloud: unproven`, or `unproven (changed since <sha>)`, on a cloud-first repo | Offer: the proof ([cloud.md](cloud.md) `## The proof`) |
| A broken pin | Offer: re-pin or remove |
| Evidence gone from a `Local-only:` line that is not `operator's choice` | Offer: set it up for the cloud (recommended), or keep it as `operator's choice: <why>` |
| A surface and no `.claude/skills/run-*/`, not `Declined: run recipe` | Offer: type `/run-skill-generator` ([surfaces.md](surfaces.md) `## The run recipe`) |
| A third-party unit a Write above adds or re-pins (the Write keeps its own row), and a pin that is behind | Install-check row |
| A catalog `Traps:` question whose condition holds (js-ts Playwright Test's `webServer.command` starting with `pnpm exec`), not `Declined:` | Offer |
| Unclaimed extensions and stacks, unwired tools, Found-not-installed units, `public API` defaults with no baseline, roots a language server does not serve | Report only |
| Standing choices | Report only |

Not gaps: a line the repo added to a file the skill wrote (kept); a red or unavailable check during timing (`not judged: fail (<check>)`, `not judged: unavailable (<check>)`); a skill pin in `skills-lock.json` (tier 3, never proposed: that bump is `update-skills`'); a profile `Schema:` ahead of the skill, which stops the run at step 2.

## Files the skill wrote

`scripts/check.sh`, `.claude/hooks/check-hook.sh`, `.claude/hooks/cloud-setup.sh`, the hook entries in `.claude/settings.json`, a vendored plugin (against what [language-servers.md](language-servers.md) `## Vendoring` writes at its pinned SHA, which `## Pins` alone moves), the launcher copied into one (`.claude/skills/harness-jdtls-lsp/jdtls-launch.sh` against `templates/jdtls-launch.sh`) and the `CLAUDE.md` block are each compared whole with the current template **by what they say**: there is no version stamp and no old template to diff against. What the template says outside a configuration block and the copy lacks is a Write that restores it in place; the configuration block is compared with the block Explore would fill now. A line the repo added is kept, in the Write's proposed text too. A line the repo reworded to the same effect is no gap.

`scripts/check.sh` is also run against its contract, the cold timing run of `edit`, `turn` and `full` going through this skill's `scripts/check-contract.sh scripts/check.sh <rung> [<file>...]` (commit is the runner's own hook, with no `check.sh` subcommand): it prints the entry point's own JSON line when the contract holds, one line per violation (exit 1) when it does not, and exits 2 when the entry point answered no line with exit 2, which is the repo's tooling failing, not the contract. A violation that a Write from the comparison explains (the JSON `printf` deleted) is that Write. Any other (a repo-added check echoing to stdout, an exit code off its verdict) is the Offer: the current template with a configuration block that runs every check the old file ran, the repo's own additions moved into `TURN_ROWS` or `FULL_ROWS`. Either way the rung's budget verdict is `not judged: contract` until the rewrite lands.

## Pins

Every pin the harness carries is read: runner config `rev`s, the dev dependency pins of catalog tools, versions in launch and install commands, vendored plugin SHAs, direct downloads.

- **Broken**: the version is yanked or gone from its registry, its SHA is not reachable from the tag it names, its provenance or checksum fails, or its publisher no longer matches the catalog `Publisher:`. Offer: re-pin to the newest version passing the install check, or remove the tool when no version passes. Keeping it is not an option.
- **Behind**: `scripts/pick-version.sh` (for a hook repo, the newest tag at least 7 days old) picks a newer version than the pin. Install-check row, droppable, showing old and new; a `Declined: <unit> <version>` line stops that version's row, and a newer version is proposed again. Skipped for an ecosystem the repo's Renovate config (`renovate.json`, `.renovaterc*`, `.github/renovate.json*`) or `.github/dependabot.yml` already updates.
  - **A vendored plugin SHA**: the newer version is the head of `main`, and it counts only when the glue [language-servers.md](language-servers.md) `## Vendoring` step 1 reads differs from the pin's, the `marketplace.json` entry taken by name so another plugin's entry moving is no change. In a blobless clone of `anthropics/claude-plugins-official`, with `<upstream>` the entry name the plugin's README records, `a=$(git show <pin>:.claude-plugin/marketplace.json | jq -Se '.plugins[] | select(.name == "<upstream>")') && b=$(git show <head>:.claude-plugin/marketplace.json | jq -Se '.plugins[] | select(.name == "<upstream>")') && git diff --quiet <pin> <head> -- plugins/<upstream>/ && [ "$a" = "$b" ]` exiting 0 is identical glue: no row and no write, the README SHA and the `plugin.json` version staying at the pin. Exiting 1 is glue that differs, the Behind row. Any other exit is a read that returned nothing: where `git show <head>:.claude-plugin/marketplace.json | jq -e --arg n <upstream> 'any(.plugins[]; .name == $n)'` exits 1 (`false`), upstream dropped the plugin, which is Broken, gone from its registry; otherwise the read failed (`jq -e` answers 4 for an empty read, the shell 127 for a missing `jq`), and it is fixed and run again. Broken is read first, on the pin itself: one `main` no longer reaches is Broken whatever its glue says, and takes no Behind row.
- **Tier 3**: skill pins are never proposed.

## Deleted pieces and standing choices

A missing piece is proposed every run until step 5 records it `Declined:`; removing that line re-opens it. A `Root:` line the human deletes re-opens its candidate root's question the same way, and an `Excluded:` line its fixture tree's question, the tree new again; its prefix stays in the runner config's `exclude:` and `check.sh`'s `EXCLUDED` only if the answer excludes it again.

**Standing choices** are listed and never re-asked: every `Excluded:`, every `Root:`, every `Declined:`, every `Local-only:` whose reason is `operator's choice`, budget overrides, the `Verdict:` and `Allowlist:`. A `Local-only:` line whose reason is not `operator's choice` is re-checked against its evidence: a file, target or host in the repo, the MCR tag check for the member's current locked Playwright version, or the catalog `Local-only:` line with no `Cloud setup:` override; when that evidence is gone it is the Offer above, whose "set it up" also removes its name from `LOCAL_ONLY`, and "keep" rewrites its reason to `operator's choice: <why>`.

## Timing on a re-run

Every rung is timed in Explore, before the report, as [check-ladder.md](check-ladder.md) `## Timing the rungs` says, the cold run of `edit`, `turn` and `full` through `check-contract.sh`. Timing leaves `git status --porcelain` empty: a file a fixer rewrote is `git restore`d and listed under Not acted on with the check that rewrote it. A rung that fails or answers `unavailable` takes check-ladder.md's `not judged` verdict, and no gap or offer follows from it.

After the writes, in place of step 6's first timing, every rung the batch touched is timed again: a `check.sh` configuration change re-times the rungs whose rows changed, `check-hook.sh` or a hook entry the rung it runs, a runner config change `edit`, commit and `full`, an installed or re-pinned unit the rungs that run it. A proposed write to a path the proof covers ([cloud.md](cloud.md) `## Recording the proof`) makes a standing `Proof: <sha>` stale: the Offers carry one proof Offer naming that row's number. Approved, the proof runs after the writes; declined, the profile keeps `Proof: <sha>` and the report says `cloud: unproven (changed since <sha>)`.

## The report

Step 4's message on a re-run, in this order:

1. **Header**: skill version, `Schema: N` (and "migrates to M" when it trails), the Claude Code floor against the installed version, the step-1 warnings, the surfaces with their evidence paths.
2. **Offers**, numbered, each with its recommendation.
3. **Proposed writes**: one table, `| # | Gap | Path | Now | Proposed |`, with the install-check columns filled on unit rows and each unit's glue in full after it; the migration is row 1, marked undroppable.
4. **Budgets**: rung, cold, warm, budget, verdict.
5. **Not acted on.**
6. **Standing choices**, with the cloud state.

With no gaps: `harness: no gaps`, then the budgets table and the standing choices. Nothing is written, and the run ends after this message.
