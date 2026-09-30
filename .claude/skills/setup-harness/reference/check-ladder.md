# The check ladder: check.sh, the hooks and the budgets

## Contents

- [The rungs](#the-rungs)
- [Writing check.sh](#writing-checksh)
- [Writing the hooks](#writing-the-hooks)
- [Budgets](#budgets)
- [Timing the rungs](#timing-the-rungs)

## The rungs

| Rung | Runs | Trigger | Budget (deadline / hook `timeout`) |
|---|---|---|---|
| `edit` | lint and format, fix mode, through the pre-commit runner on the edited file | `PostToolUse` on `Edit\|Write` | 5 s / 15 s, per file invocation |
| `turn` | typecheck plus affected tests of each member the uncommitted changes touch | `Stop` | 60 s / 75 s |
| commit | the runner's own git hook | `git commit` | 30 s, measured only |
| `full` | runner on every file, every member's typecheck and whole suite, the repo's check target | a human, CI, Ship's local gate | 10 min, measured only |

The linter set is defined once, in the runner config; `check.sh` calls a linter directly only when the runner cannot take a file list (lint-staged). Typecheck and tests live in `check.sh`. There is no `commit` subcommand: the commit rung is the runner's git hook.

The contract every caller parses: stdout is one JSON line `{"rung","verdict","checks":{<name>:<status>}}`, status `pass | fail | unavailable | skipped | over-budget`; stderr carries each failing check's last 40 lines; exit 0 pass, 1 fail, 2 unavailable or tooling, 3 over budget. `CHECK_DEADLINE=<epoch s>` makes `check.sh` stop at that time, mark the check running at that time `over-budget` (none, when the deadline fell between checks) and the rest `skipped`, and exit 3; without it exit 3 never occurs. A failure outranks an over-budget check, so a real failure still blocks.

## Writing check.sh

Copy [templates/check.sh](../templates/check.sh) to `scripts/check.sh`, executable, and fill only the block between `# >>> setup-harness configuration` and `# <<< setup-harness configuration`:

- `EDIT_GLOBS`: shell globs on the file name for every extension the runner config covers (`'*.py *.pyi *.ts *.tsx *.sh'`), plus the basename of each extensionless file detection claimed by shebang (`deploy`). A file matching none is `skipped` fast, so the `PostToolUse` entry needs no per-extension `if`.
- `EDIT_RUN`: the runner on a file list, `prek run --files {files}` (or `pre-commit run --files {files}`, `lefthook run pre-commit --file {files}`). `check.sh` runs it twice when the first run fails: the first run's fixes fail it, and only what the fixes leave fails the second.
- `FULL_RUN`: the runner on every file, `prek run --all-files`.
- `TURN_ROWS`: one row per member, `<path prefix>|<member>|<globs>|<typecheck>|<tests>|<affected tests>`. The prefix ends in `/` (`api/`, `packages/web/`), or is empty for a member at the repo root. Globs are the stack's extensions plus its manifest names, so a manifest change re-checks the member. Commands run in the member's directory, prefixed with the stack's exec command (`uv run --frozen mypy .`, `pnpm exec tsc --noEmit`). `<affected tests>` takes `{files}` relative to the member (`pnpm exec vitest related --run {files}`); leave it empty to run `<tests>`, the member's whole suite. An empty typecheck or tests field is no check.
- `FULL_ROWS`: extra checks on `full` only, `<name>|<command>` from the root: the repo's own check target (`check-target|make check`), and each surface's behaviour tools per [surfaces.md](surfaces.md) (`e2e-web|cd web && pnpm exec playwright test`).
- `LOCAL_ONLY`: the `FULL_ROWS` names the cloud cannot run, space-separated, each a profile `Local-only:` part: a cloud session records them `skipped` unrun, so the proof's `full` still exits 0.
- `EXCLUDED`: the profile's `Excluded:` prefixes, one per line, since a prefix may hold a space: the edit rung skips a file under one whatever the runner config excludes, and a changed file under one belongs to no `TURN_ROWS` row, even one whose prefix covers it, and is no new root. A prefix answered read, or whose line is gone, leaves it.

A changed file matching some row's globs under no row's prefix and no `EXCLUDED` prefix reports `new-root: unavailable` with "re-run setup-harness": a stack root appeared that this file has no row for. A repo's existing check target is kept and called from `FULL_ROWS`, never replaced.

## Writing the hooks

Copy [templates/check-hook.sh](../templates/check-hook.sh) to `.claude/hooks/check-hook.sh`, executable, and fill its configuration block: `CHECK` stays `scripts/check.sh`; `EDIT_BUDGET` and `TURN_BUDGET` are the edit and turn budgets in seconds as timing left them (`default` is 5 and 60, `override <N>s: ...` is N). Then merge [templates/settings-hooks.json](../templates/settings-hooks.json) into `.claude/settings.json`, each `timeout` set to the deadline plus max(10 s, deadline / 4): 15 and 75 at the defaults. Every entry carries a `timeout`: a hook that times out passes silently, and the default is 600 s.

Merge beside what the repo has: keep every existing hook entry, add these two, and never duplicate one already present. Hooks live in `.claude/hooks/`, never in a plugin: a cloud session does not install repo-enabled plugins.

## Budgets

The defaults are the table's; a budget moves only by the human's `override <N>s: <reason>` in the profile. The edit budget covers one invocation: sync `PostToolUse` hooks run one after another, so a batch of N edits costs N times the edit time.

## Timing the rungs

Time with the wrapper's own clock, whole seconds (`start=$(date +%s)`; Bash 3.2 has no sub-second clock and macOS no `timeout`). Run each rung twice: the first is cold, reported; the second is warm, judged against the budget.

- `edit`: the largest tracked file per extension `EDIT_GLOBS` covers, one invocation each. Fix mode on a clean file writes nothing.
- `turn` and commit: the files of the last commit that touched a member's source, `scripts/check.sh turn <files>` and the runner's hook on those files (`prek run --files <files>`).
- `full`: one run of `scripts/check.sh full`.

A rung that fails while timing is the repo's code, not the harness: its verdict is `not judged: fail (<check>)`, and one that answers `unavailable` is `not judged: unavailable (<check>)`.

A warm time over budget gets three offers, in order, each with a recommendation: **narrow** the check to the file set where its tool allows; **demote** it to the next rung (a slow linter to `turn`, a slow typecheck to `full`); **override** the budget with the human's reason. An applied offer re-times the rung. Commit and `full` are measured only: over budget is reported `over`, with no offer.
