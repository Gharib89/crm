# update-skills in the source repo

The steps only the source repo, `Gharib89/skills`, runs: step 9 of
[SKILL.md](../SKILL.md#9-source-repo-mode), and the steps a Ship run follows by
hand on an upstream-drift issue. `$S` and `$U` are as SKILL.md defines them,
and a bare step number names a SKILL.md step: read its steps 2 to 7 first.

## Source-repo mode

`mode` is `source`: this is Gharib89/skills, and each `drift` and `others` row
is a pin to move, in this one PR. It runs before step 7, which then files or
finds the drift issue this PR closes; steps 6 and 8 run unchanged. Per row:

1. Read what moved:
   `https://api.github.com/repos/<source>/compare/<from>...<head>`, `<from>`
   a `drift` row's `pin` or an `others` row's `old_ref`, the files under the
   skill's folder with their patches.
2. A `drift` row: re-add the skill at `#<head>` on its install line and move
   the pin on the `composes` line naming it (`skills/ship/SKILL.md` or
   `skills/setup-skills/SKILL.md`, the composing skill). An `others` row: run
   its `install` line. Either row: move the sha on every printed install line
   under `skills/` that installs it, splitting a line whose skills' pins part.
3. Judge the upstream diff for a **break**: for a `drift` row, a change breaking
   a reliance of the composing skill's prose on it; for an `others` row, a
   change to or removal of something this repo's own files (`skills/`, `docs/`,
   `CLAUDE.md`, `AGENTS.md`) name. A row that breaks is **held back**, with no
   prose reworked: re-run its install line at `#<from>` (a null `old_ref`
   restores its folder and lock entry from `<old>`), put `<from>` back on each
   line item 2 edited, and record `held back: <skill>: <reliance it breaks>`.

Then the refresh line, and `scripts/check.sh full`, whose `derived-copies`
check holds every pin to the lock and whose `tests` check runs the suite; the
local gate defers both to CI, so it cannot hold a row back. A check red at the
new pins holds back the row whose skill or folder its failing output names, or
every moved row where it names none, the failing check as the reliance; then
refresh and run `full` again. In step 8
the PR body opens with `Closes #<n>`, the drift issue step 7 filed or found, or
`Refs #<n>` with a row held back, and `## Needs attention` lists each `held
back` line. With every row held back, open no PR: hand the owner those lines and
run `$S/cleanup.sh none` from the main checkout.

The title is scoped to the composing skill, so the release run records the move
in its CHANGELOG, or for `others` rows alone to the skill whose printed line
moved, else it reads `chore(skills): move <skills> to <short sha>`. The grade
follows `docs/contributing/standards/release.md`'s composes bullet and its
`## The PR title and commits`: a pin moved on ship's `composes` line breaks
ship, so while ship is 0.x the title is `feat(ship): move <skill> to <short
sha>`, with no `!` in the title or any commit (`bump-guard` reads commits), no
`BREAKING CHANGE:` footer and no `major` label. A pin moved on setup-skills'
line or on a printed line alone is not breaking, since preflight refuses nothing
on it, and takes no `!` either. State
the break in plain words in the commit body and under
`## Special things to note`.

## In a Ship run

A Ship run cannot invoke this skill (`disable-model-invocation`), so on an
upstream-drift issue in the source repo it follows the steps below by hand, in
its worktree. Steps 1, 7 and 8 are Ship's: its worktree is step 1's, the issue
shipped is the drift issue step 7 would file, and phase 6 opens the PR with
`Closes #<issue>` but for a hold-back. Step 5 asks nothing here: source-repo
mode moves every `others` row.

1. `<old>`: `git rev-parse HEAD` in the worktree, before the first edit.
2. Step 2's source-repo refresh line, from the source repo's CLAUDE.md.
3. Step 3, with its source-repo token. A `heads` exit 1 is retried once, then
   stops the run `red-after-retry: heads`.
4. Step 4: the composed install lines at their pins, then its preflight.
5. Step 6, its `/setup-skills` re-run replaced by the record in the table
   below; the source repo has no retired terms to sweep.
6. [Source-repo mode](#source-repo-mode)'s per-row items and its check
   paragraph, before Ship's phase 5: a red check at the new pins is a
   hold-back, not a fix-and-retry; its title rule is phase 6's title. The PR
   and hand-back lines below replace its "In step 8 the PR body opens" and
   "With every row held back" sentences.

A held-back row hands the issue back to the owner. With some rows moved, phase
6 runs `open-pr none --title <t> --body-file <f>`, `<f>` saying `Refs #<issue>`,
then `manage-issue <issue> handback "<reason>"` naming each `held back` line,
before the merge gate; `merge` takes `none` as its issue, `cleanup`
keeps `<issue>`.
With every row held back there is no PR: hand back, `cleanup <issue>`, and stop.

Where a step would ask the owner, the run takes the conservative answer and
writes it to the Run file's deviations log, which lands in the merge summary:

| Step asks | The run records |
|---|---|
| Step 6, re-running `/setup-skills`, which interviews | not re-run; `setup-skills needed: <section or profile reason>` per item |
| Step 3, an `unreachable` row | `drift not checked: <skill>: <error>` |
