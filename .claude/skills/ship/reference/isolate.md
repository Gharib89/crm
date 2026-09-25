# Phase 0: preflight, the profile it loads, and isolation

## Contents

- [What preflight proves](#what-preflight-proves)
- [Admission and the not-actionable reasons](#admission-and-the-not-actionable-reasons)
- [The worktree](#the-worktree)
- [The ship profile](#the-ship-profile)
- [The schema number](#the-schema-number)
- [What preflight refuses](#what-preflight-refuses)
- [Re-validate an edited profile with `preflight none`](#re-validate-an-edited-profile-with-preflight-none)

`SKILL.md` names the two calls and the branch they leave behind; this file says
what each one proves, what it refuses and why the worktree is made the way it
is.

## What preflight proves

Tooling and identity, and **push permission first where the host can answer
it**: every read-only call succeeds for an account that cannot push, so a wrong
account stays invisible until the merge answers 404. Azure DevOps has no cheap
push probe, so preflight returns `unknown`, warns on stderr and continues: read
the warning before trusting `ok: true`, and name the unproven check in the merge
summary.

It also cross-checks `## Host` against the remote, validates the profile
headings below, confirms every skill on ship's `composes` line is installed
under this checkout's `.claude/skills/`, one `skill missing` reason per absent
skill, and prunes worktrees whose PR is merged or closed.

## Admission and the not-actionable reasons

Every not-actionable reason is collected into `reasons`, which is the whole
stop: an empty `reasons` with `ok: true` is the admission, and every entry in it
is a row of `SKILL.md`'s stop table. Admission by label is `ready-for-agent`
always, `ready-for-human` in an attended run only (`preflight <issue>
--unattended` is what turns that into the `ready-for-human: attended only`
stop), and anything else is `not triaged`.

An assignee, including your own identity, is `already claimed`; stale-claim
recovery is a human unassigning by hand. `existing PR` means a live PR whose
body **closes** this issue or whose head branch ends in `-<issue>`. PRs that
merely mention it come back as `mentions[]`, context for phase 1 and no kind of
stop, alongside a `mentioned_by[]` row per live cross-reference, open issues and
open or merged PRs both, naming its `kind` and `state`.

## The worktree

Attended, `isolate <issue> <type> <slug>` resolves the main checkout through
`--git-common-dir`, fetches, branches from `origin/HEAD`, creates the sibling
worktree `<parent>/<repo>.worktrees/<slug>-<issue>`, copies the profile's
`Carry:` files in one way, and prints the path. Unattended,
`isolate ... --in-place` does the same fetch and branch in the sandbox clone,
which is already isolation: no worktree, no carry.

Isolation is `isolate`'s call alone, rather than `EnterWorktree` or a bare `git
worktree add`: it is what lands the worktree at the path and branch preflight
checks, with the `Carry:` files copied in. Work from the printed path, every
edit under it absolute, because an absolute main-checkout path silently edits
the wrong tree. The `-<issue>` suffix on the branch is what preflight greps, and
the branch type is a label: the squash subject, not the branch, is what release
tooling reads.

## The ship profile

Load `docs/agents/ship.md` **once, whole, at preflight**, the way a session
loads `docs/agents/issue-tracker.md`. It has fourteen fixed `##` headings, every
one always present; a defaulted axis reads `None.` or `Default.`. Most facts sit
on `Label:` lines; Coding standards, Public surface and Triage carry theirs as
the section body, and the prose under a heading explains them. Preflight,
prepare and the reviewer mechanics read their own lines; everything else you
pass as arguments. Two more repo docs feed a run and are read the same way: triage
roles (`ready-for-agent`, `ready-for-human`, `needs-triage`) are canonical role
names whose label strings come from `docs/agents/triage-labels.md`, and the
tracker's mechanics come from `docs/agents/issue-tracker.md`.

## The schema number

Directly under the `# Ship profile` title, before the first
`##`, the profile carries `Schema: N`. This skill declares the schema it reads as
`metadata.profile-schema` in SKILL.md's frontmatter. The number is separate from
`metadata.version`: it moves only when ship's expectations of the profile change
(a heading or `Label:` line added, renamed or removed; a `Label:` vocabulary
changed), always graded a ship major, and stays put for a behaviour change
that leaves the profile alone. Preflight compares the two and refuses a mismatch
in either direction, in both lanes, before any claim; the detail names both numbers
and the fix. A profile older than ship is refused even where ship could default
the missing axis: a defaulted axis reads `None.`/`Default.` explicitly, on a
heading that is present like every other.

## What preflight refuses

Missing file: stop `profile missing`, naming `docs/agents/ship.md` and
`/setup-skills`. Missing or misordered headings: stop `profile invalid`, naming
them. A fact the current run needs that reads `None.` where it cannot be none is
also `profile invalid`; a fact the run will not touch goes unchecked. The
reviewer blocks are the exception, checked whatever the run touches, because
preflight parses them: an on-request reviewer with no `Cap:`, a `Cap:` that is
neither a number nor `None.`, a `Fallback-for:` on a reviewer that is not
on-request, a `Fallback-for:` naming a reviewer the profile does not list, a
`Request: comment` with no phrase for the transport to post, a
`Request: comment <phrase>` with no `Workflow:` naming the file its round comes
from, a `Workflow:` on a block whose `Request:` is not a comment transport, and
a `Workflow:` naming a file the checkout does not carry are all refused there,
before the claim. So is the one reviewer fact the host settles
rather than the block: a Copilot reviewer whose `Trigger:` disagrees with the
`copilot_code_review` ruleset that drives it. Where preflight cannot read that
ruleset, it warns on stderr and admits the run.

## Re-validate an edited profile with `preflight none`

A run that changes the profile, or refreshes the ship copy that reads it, proves
the new pair with the issueless call. Use that call rather than a second
`preflight <issue>`, for the reason [preflight](../scripts/preflight.sh) carries
at the top of its own file.
