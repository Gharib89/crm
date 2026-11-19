# Phase 0: preflight, the profile it loads, and isolation

`SKILL.md` names the calls; this file says what each one proves, what it admits
and how the worktree is made.

## What preflight proves

Tooling, identity and push permission; each `reasons` entry is a row of the
stop table, and an unattended run calls `preflight <issue> --unattended`. Azure
DevOps has no cheap push probe, so preflight returns `unknown`, warns on stderr
and continues: name the unproven check in the merge summary.

The composed-skills guard refuses on the repo's own `.claude/skills` only, and
beside it preflight's `warnings` lists, never refusing, each personal copy under
`~/.claude/skills` that shadows ship or a skill it composes and differs from the
project copy or cannot be compared with it; record each as a Ship note once
the Run file exists
(`run-file note "Ship notes" "Ship-note: <warning>" --issue <issue>`), so the
merge summary shows the run executed a copy the lock does not describe.

## Admission

Admission by label is `ready-for-agent` always and `ready-for-human` in an
attended run only; anything else is `not triaged`. An assignee, your own
identity included, is `already claimed`: stale-claim recovery is a human
unassigning by hand. A [rework run](rework.md) is the exception: it needs your
own claim and an open PR, and isolates on that PR's branch. `existing PR` is a
live PR whose body **closes** this issue. A head branch on origin ending in
`-<issue>` is `existing branch`, whether or not a PR holds it.

## The worktree

The branch `<type>` (`feat`, `fix`, `docs`, ...) and `<slug>` come from the
`read-issue` result. Attended, `isolate <issue> <type> <slug>` fetches, branches
`<type>/<slug>-<issue>` from `origin/HEAD`, creates the worktree
`<main checkout>/.claude/worktrees/<slug>-<issue>`, lists `.claude/worktrees/`
once in the repo's shared exclude file (`<git common dir>/info/exclude`),
copies each `--carry <file>` (the profile's `Carry:` files) in one way, and
prints the path. For a
`<slug>` already ending in `-<issue>`, `isolate` drops that suffix so the number
appears once; `--rework` keeps the slug as given, since it names the PR's own
branch. Unattended, `isolate ... --in-place` does the same fetch and branch in
the sandbox clone, which is already isolation: no worktree, no carry.

Creating the worktree is `isolate`'s call alone, rather than `EnterWorktree` or
a bare `git worktree add`: it is what lands the worktree at the path and branch
preflight checks. Work from the printed path, every edit under it absolute,
because an absolute main-checkout path silently edits the wrong tree, and
commit as you go, because the PR needs real commits. A `Bootstrap:` under the
profile's `## Worktree` runs once, after `isolate`.

## Adopting the launched worktree

A host's worktree the session started in, on a branch ending `-<issue>`, is
adopted, fast-forwarded to `origin/HEAD`, `reused: true`. A dirty or ahead
one is refused, the main checkout never adopted. Already there, the session
skips `EnterWorktree` and `ExitWorktree`. `cleanup` leaves it.

## Entering the worktree

Attended, the session then enters the path `isolate` printed with
`EnterWorktree` (`path` set to it), before any edit and before any `LSP` call.
Claude Code roots its language server at the session's directory at the first
`LSP` call or the first edit of a file it serves, and never moves it: a session
left at the main checkout gets every `LSP` answer from the main checkout's code,
and a Bash `cd` changes nothing. Under `.claude/worktrees/` the switch raises no
permission prompt, which is why `isolate` creates the worktree there. A session
that edited a served file before `/ship` keeps its server where it started.

Through any stop or hand-back the session stays in the worktree, so the human
picks up there. At the merge gate it leaves with `ExitWorktree`
(`action: "keep"`) before `cleanup` removes the folder. `CLAUDE_PROJECT_DIR`
stays the main checkout, so hooks run its scripts; the mechanics resolve the
main checkout and the Run file through the git common directory.

## The ship profile

`docs/agents/ship.md` has fourteen fixed `##` headings, every one always
present; a defaulted axis reads `None.` or `Default.`. Most facts sit on
`Label:` lines; Coding standards, Public surface and Triage carry theirs as the
section body. Preflight, prepare and the reviewer mechanics read their own
lines; everything else you pass as arguments.

A run that changes the profile, or refreshes the ship copy that reads it,
re-validates the pair with `preflight none` rather than a second `preflight
<issue>`, for the reason [preflight](../scripts/preflight.sh) carries at the top
of its file.
