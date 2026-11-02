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
| `full` | runner on every file first, since a hook in fix mode rewrites files; then every member's typecheck and whole suite and the repo's check target, all at once, so it costs the runner plus its longest row | a human, CI, Ship's local gate | 10 min, measured only |

Each `check.sh` rung (`edit`, `turn`, `full`) first installs each root whose dependencies are missing from the checkout, as a fresh worktree's are, before anything else runs: a runner hook calling a root's own binary (`web/node_modules/.bin/eslint`) fails until they exist, and a language server stops reporting missing modules once they do. Each install is a `deps:<name>` check under the rung's deadline; a failed one fails its check and the rung runs on, and a root already present costs one present test and is not reported. An install that never passed, killed by the deadline or a hook's timeout, runs again on the next call although its partial tree passes the present test: a mark in the checkout's own git directory records it until it passes. `check.sh deps` runs those installs and nothing else, no rung of its own: it is how the cloud setup's step 3 installs.

`check.sh` calls a linter directly only when the runner cannot take a file list (lint-staged). Typecheck and tests live in `check.sh`. There is no `commit` subcommand: the commit rung is the runner's git hook.

The contract every caller parses: stdout is one JSON line `{"rung","verdict","checks":{<name>:<status>}}`, status `pass | fail | unavailable | skipped | over-budget`; stderr carries each failing check's last 40 lines, and under a check whose tool is missing (`unavailable`, or a `runner` failure with `not found` or `No such file or directory` in its log, a hint a hook's own message can also fire) a line naming the cloud setup's `--local` ([cloud.md](cloud.md)) where the harness profile's `Setup:` names one, or, at `Setup: None.`, where `.claude/hooks/cloud-setup.sh` exists; exit 0 pass, 1 fail, 2 unavailable or tooling, 3 over budget. `full`'s report order and `CHECK_DEADLINE` are as [templates/check.sh](../templates/check.sh) states.

## Writing check.sh

Copy [templates/check.sh](../templates/check.sh) to `scripts/check.sh`, executable, and fill only the block between `# >>> setup-harness configuration` and `# <<< setup-harness configuration`:

- `EDIT_GLOBS`: shell globs on the file name for every extension the runner config covers (`'*.py *.pyi *.ts *.tsx *.sh'`), plus the basename of each extensionless file detection claimed by shebang (`deploy`). A file matching none is `skipped` fast, so the `PostToolUse` entry needs no per-extension `if`.
- `EDIT_RUN`: the runner on a file list, `prek run --files {files}` (or `pre-commit run --files {files}`, `lefthook run pre-commit --file {files}`).
- `FULL_RUN`: the runner on every file, `prek run --all-files`.
- `TURN_ROWS`: one row per member, `<path prefix>|<member>|<globs>|<typecheck>|<tests>|<affected tests>`. The prefix ends in `/` (`api/`, `packages/web/`), or is empty for a member at the repo root. Globs are the stack's extensions plus its manifest names, so a manifest change re-checks the member. Commands run in the member's directory, prefixed with the stack's exec command (`uv run --frozen mypy .`, `pnpm exec tsc --noEmit`). `<affected tests>` takes `{files}` (the changed files still on disk, relative to the member) for a tool that opens each file (`pnpm exec vitest related --run {files}`), or `{paths}` (every changed path, deleted ones included) for a selector that maps a path to its tests by name, so a deleted file still runs them; leave it empty to run `<tests>`, the member's whole suite. A re-run leaves a hand-added row as written, so a `shell` row that got deleted `.sh` paths from the previous `check.sh`, by its member name alone, gets them again only once its `<affected tests>` names `{paths}`. An empty typecheck or tests field is no check.
- `FULL_ROWS`: extra checks on `full` only, `<name>|<command>` from the root: the repo's own check target (`check-target|make check`), and each surface's behaviour tools per [surfaces.md](surfaces.md) (`e2e-web|cd web && pnpm exec playwright test`).
- `LOCAL_ONLY`: the `FULL_ROWS` names the cloud cannot run, space-separated, each a profile `Local-only:` part: a cloud session records them `skipped` unrun, so the proof's `full` still exits 0.
- `DEPS_ROWS`: one row per root whose lockfile has a present test in the lockfile table of [catalog/README.md](../catalog/README.md), `<name>|<present test>|<frozen install>`, both commands from the repo root: `<name>` the root's directory (`root` for a stack at the repo root), the present test with each path it names prefixed by that directory, or `cd <root> &&` before one that is a command (`cd api && poetry env info -p`), and the frozen install after `cd <root> &&` (`web|test -d web/node_modules|cd web && pnpm install --frozen-lockfile`, `api|test -d api/.venv|cd api && uv sync --frozen`); a stack at the repo root takes both unchanged. A present test carries no `|`, which separates the row's fields. A root whose lockfile has none restores into a cache every local checkout shares, and gets no row. These rows are the only copy of the install: the cloud setup runs them through `check.sh deps`.
- `EXCLUDED`: the profile's `Excluded:` prefixes, one per line, since a prefix may hold a space: the edit rung skips a file under one whatever the runner config excludes, and a changed file under one belongs to no `TURN_ROWS` row, even one whose prefix covers it, and is no new root. A prefix answered read, or whose line is gone, leaves it. `.claude/skills/` takes no line here: `check.sh` skips it the same way unconditionally, since detection never scans it ([detection.md](detection.md)).

A changed file matching some row's globs under no row's prefix and no `EXCLUDED` prefix reports `new-root: unavailable` with "re-run setup-harness": a stack root appeared that this file has no row for. A repo's existing check target is kept and called from `FULL_ROWS`, never replaced.

## Writing the hooks

Copy [templates/check-hook.sh](../templates/check-hook.sh) to `.claude/hooks/check-hook.sh`, executable, and fill its configuration block: `CHECK` stays `scripts/check.sh`; `EDIT_BUDGET` and `TURN_BUDGET` are the edit and turn budgets in seconds as timing left them (`default` is 5 and 60, `override <N>s: ...` is N). Then merge [templates/settings-hooks.json](../templates/settings-hooks.json) into `.claude/settings.json`, each `timeout` set to the deadline plus max(10 s, deadline / 4): 15 and 75 at the defaults. Every entry carries a `timeout`: a hook that times out passes silently, and the default is 600 s.

Merge beside what the repo has: keep every existing hook entry, add these two, and never duplicate one already present. Hooks live in `.claude/hooks/`, never in a plugin: a cloud session does not install repo-enabled plugins.

## Budgets

The edit budget covers one invocation: sync `PostToolUse` hooks run one after another, so a batch of N edits costs N times the edit time.

## Timing the rungs

Time with the wrapper's own clock, whole seconds (`start=$(date +%s)`; Bash 3.2 has no sub-second clock and macOS no `timeout`). Run each rung twice: the first is cold, reported; the second is warm, judged against the budget.

- `edit`: the largest tracked file per extension `EDIT_GLOBS` covers, one invocation each. Fix mode on a clean file writes nothing.
- `turn` and commit: the files of the last commit that touched a member's source, `scripts/check.sh turn <files>` and the runner's hook on those files (`prek run --files <files>`).
- `full`: one run of `scripts/check.sh full`.

A rung that fails while timing is the repo's code, not the harness: its verdict is `not judged: fail (<check>)`, and one that answers `unavailable` is `not judged: unavailable (<check>)`.

A warm time over budget gets three offers, in order, each with a recommendation: **narrow** the check to the file set where its tool allows; **demote** it to the next rung (a slow linter to `turn`, a slow typecheck to `full`); **override** the budget with the human's reason. An applied offer re-times the rung. Commit and `full` are measured only: over budget is reported `over`, with no offer.
