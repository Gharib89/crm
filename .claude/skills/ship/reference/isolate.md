# Phase 0: preflight, the profile it loads, and isolation

`SKILL.md` names the calls; this file says what each one proves, what it
refuses and why the worktree is made the way it is.

## What preflight proves

Tooling and identity, and **push permission first where the host can answer
it**: every read-only call succeeds for an account that cannot push, so a wrong
account stays invisible until the merge answers 404. Azure DevOps has no cheap
push probe, so preflight returns `unknown`, warns on stderr and continues: name
the unproven check in the merge summary.

It also cross-checks `## Host` against the remote, validates the profile,
confirms every skill on ship's `composes` line is installed under this
checkout's `.claude/skills/` at its pinned ref, as `skills-lock.json` records
the installed one, and prunes worktrees whose PR is merged or closed.

## Admission

Every not-actionable reason is collected into `reasons`, each a row of
`SKILL.md`'s stop table, named in the words the table uses. Admission by label is
`ready-for-agent` always and `ready-for-human` in an attended run only; anything
else is `not triaged`. An assignee, your own identity included, is `already
claimed`: stale-claim recovery is a human unassigning by hand. `existing PR` is
a live PR whose body **closes** this issue or whose head branch ends in
`-<issue>`. PRs that merely mention it come back as `mentions[]`, and live
cross-references as `mentioned_by[]` rows: context for phase 1, no kind of stop.

## The worktree

Attended, `isolate <issue> <type> <slug>` fetches, branches
`<type>/<slug>-<issue>` from `origin/HEAD`, creates the sibling worktree
`<parent>/<repo>.worktrees/<slug>-<issue>`, copies the profile's `Carry:` files
in one way, and prints the path. Unattended,
`isolate ... --in-place` does the same fetch and branch in the sandbox clone,
which is already isolation: no worktree, no carry.

Isolation is `isolate`'s call alone, rather than `EnterWorktree` or a bare `git
worktree add`: it is what lands the worktree at the path and branch preflight
checks. Work from the printed path, every edit under it absolute, because an
absolute main-checkout path silently edits the wrong tree. The `-<issue>` suffix
on the branch is what preflight greps; the branch type is a label, since the
squash subject is what release tooling reads.

## The ship profile

Load `docs/agents/ship.md` **once, whole, at preflight**. It has fourteen fixed
`##` headings, every one always present; a defaulted axis reads `None.` or
`Default.`. Most facts sit on `Label:` lines; Coding standards, Public surface
and Triage carry theirs as the section body. Preflight, prepare and the
reviewer mechanics read their own lines; everything else you pass as arguments.
Triage roles (`ready-for-agent`, `ready-for-human`, `needs-triage`) take their
label strings from `docs/agents/triage-labels.md`, and the tracker's mechanics
come from `docs/agents/issue-tracker.md`.

Under the `# Ship profile` title the profile carries `Schema: N`, and this skill
declares the schema it reads as `metadata.profile-schema`. The number moves only
when ship's expectations of the profile change (a heading or `Label:` line
added, renamed or removed; a `Label:` vocabulary changed), always graded a ship
major, and never for a behaviour change that leaves the profile alone.

## What preflight refuses

A missing profile, a heading missing or out of order, a schema mismatch in
either direction, and a fact the current run needs reading `None.` where it
cannot be none are all refused before any claim, each `reasons` entry naming the
fix. The reviewer blocks are checked whatever the run touches, because preflight
parses them, and so is the one reviewer fact the host settles: a Copilot
`Trigger:` that disagrees with the `copilot_code_review` ruleset. Where
preflight cannot read that ruleset, it warns on stderr and admits the run.

A run that changes the profile, or refreshes the ship copy that reads it,
re-validates the pair with `preflight none` rather than a second `preflight
<issue>`, for the reason [preflight](../scripts/preflight.sh) carries at the top
of its file.
