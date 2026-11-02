# Phase steps

The long steps of nine phases, moved out of `SKILL.md` so it stays short. Each
phase there keeps its summary and its `Done when:`; read the section here when
the run enters that phase.

## Contents

- [Phase 0 isolate](#phase-0-isolate)
- [Phase 1 anchors and criteria](#phase-1-anchors-and-criteria)
- [Phase 2 implement](#phase-2-implement)
- [Phase 3 verify](#phase-3-verify)
- [Phase 4 self-review](#phase-4-self-review)
- [Phase 4 writing pass](#phase-4-writing-pass)
- [Phase 5 local gate](#phase-5-local-gate)
- [Phase 7 reviewers](#phase-7-reviewers)
- [Phase 8 CI](#phase-8-ci)
- [Phase 9 merge gate](#phase-9-merge-gate)
- [Hand-back](#hand-back)

## Phase 0 isolate

The text below continues phase 0 in `SKILL.md`.

Run `preflight <issue>` (`--unattended` in an unattended run):
every `reasons` entry is a row of the stop table below. Then `read-issue
<issue>` (reused for an issue), phase 1's input, from which the branch `<type>`
(`feat`, `fix`, `docs`, ...) and `<slug>` are derived. Then `isolate <issue>
<type> <slug>` with the profile's `Carry:` files, or `isolate ... --in-place`
unattended. Attended, `EnterWorktree` with `path` set to that path comes right
after isolate, before any edit or `LSP` call: the language server roots where
the session stands when it first starts
([isolate.md](isolate.md#entering-the-worktree)). Every edit, commit
and the PR happen from the path `isolate` printed, and you **commit as you go**,
because the PR needs real commits. A `Bootstrap:` under `## Worktree` runs once
here, after isolate.

## Phase 1 anchors and criteria

The text below continues phase 1's claim in `SKILL.md`.

Then **grep
each anchor** the issue cites; one the tree contradicts rewrites the body
through `update-issue-body` only when it changes a criterion, else it is logged
([implement.md](implement.md#phase-1-anchors-the-issue-cites)). A
criterion needing a host action no mechanic performs stays verbatim: unattended,
it is an **unmet criterion** the merge summary lists; attended, ask first, per
[implement.md](implement.md#phase-1-a-criterion-no-mechanic-can-perform)
on both. Only then settle what success looks like in the Run file: an issue's
`## Criteria` as init recorded them, else criteria a later phase can check; a
later triage brief or authoritative comment, each `writer: true`, supersedes the
body (**spec precedence**).

## Phase 2 implement

The text below continues phase 2 in `SKILL.md`.

Classify `docs` / `code` / `infra`, judge
the three lane keys and the applicable verifications from `## Verification`
(their `Applies when:` lines are prose you judge here), and by default say
what you chose. Record the version grade of the public
surface change under the profile's `Subject constraints:` with `run-file grade
<patch|minor|breaking>`: `open-pr` and `update-pr-title` refuse a title type
that grades lower. Then implement test-first per class; `Tripwires:` and
`In-PR requirement:` land in this change whatever the class.
Keep a **deviations log** from the first edit: whenever the territory forces a
departure from the issue, brief or plan, take the conservative option, log what
and why, keep going; it lands verbatim in the merge summary. A change you judge
too big for one PR, or needing a redesign the issue did not scope, stops
`needs-split` with a proposal;
[implement.md](implement.md#phase-2-needs-split) lists tells.

## Phase 3 verify

The text below continues phase 3 in `SKILL.md`.

Run each
applicable verification's `Run:` line **scoped to what you touched**, on the
environment the issue was reported against: green elsewhere is not fixed. Record
each run with `run-file ran <name>... --issue <issue>` as it ends, so `close 3`
stamps the head it ran at, not the head it is closed at. Noisy
runs go to a cheap-tier subagent in the `verify` scratch directory. Skip this
phase only when no Verification applies, whatever the class.

## Phase 4 self-review

**Self-review**, unconditional in every lane, is a loop. Round 1 invokes
`code-review` against the diff since `origin/HEAD`, its Standards axis reading
the profile's `## Coding standards` path, its Spec axis reading the issue, each
axis prompt carrying its own scratch directory (`standards`, `spec`) and, by
default, saying the Local gate runs the suite later. **Triage waits for every
Report file**; one that never arrives once
[retries](../SKILL.md#the-stops) stop helping is
`red-after-retry: <axis>`, never a disposition written from memory.
**Auto-triage** every finding at the judgment tier, one disposition each, under
the rails and depth checks in
[implement.md](implement.md#phase-4-triage-and-depth-checks), then
[loop](implement.md#phase-4-the-self-review-loop), the Standards
axis alone over each round's **fix-only diff**, until a round changes no file
(`tree unchanged`) or reaches preflight's `self_review_cap` (`cap at <sha>`).
`run-file close 4` refuses until the Run file holds the
[evidence lines](evidence-lines.md): each round's
`Round: self-review`, the loop's `Stop: self-review:`, and a `Reverted-fix:`
per test file the diff adds or changes, the red one written by `run-file
prove`.

## Phase 4 writing pass

The text below continues phase 4's docs-sync in `SKILL.md`.

**The `writing-for-agents` pass has a trigger of its own**, firing even where
docs-sync is skipped: whenever the diff touches a target on the profile's
`Agent-facing:` line, at the judgment tier, in the `writing` scratch directory,
over every agent-facing file in the diff. Human prose takes the mechanical pass.
With docs-sync's edits landed, load `code-review`, then dispatch this pass and
its two axes in one turn and end it, each naming its Report file. Small lane:
dispatch the axes, then run this pass inline.

## Phase 5 local gate

The text below continues phase 5's `base-fresh` and `Carry:` checks in
`SKILL.md`.

Run `base-fresh` first: CI
tests the merge ref, so a branch that predates a merge still goes green while
every "does this exist?" answer taken from the worktree was pre-merge; behind:
follow its advice, re-run, continue. Confirm every `Carry:` file still matches
the main checkout's copy; a difference is `carried file modified`, because ship
never edits untracked secrets. Then run the gate at the profile's `Location:`
from the worktree, inline, and `run-file gate record` its JSON (small lane:
small-lane.md). The JSON holds `verdict` `pass|fail|unavailable`, per-gate
statuses `pass|fail|deferred-to-ci|unavailable` and `gates.secrets` in every
lane; unparseable output or a missing `secrets` key reads as `unavailable`.
`fail`: fix loop. `deferred-to-ci`: proceed, the merge summary naming each
deferred gate. `unavailable`: stop `local gate unavailable`, the PR unopened.

## Phase 7 reviewers

The text below continues phase 7 in `SKILL.md`.

Best-effort: for each reviewer under `## Reviewers`, per round, start
it by its `Trigger:`, wait out one `poll-pr --brief` window, triage whatever
landed, push the fixes once, answer each thread with `reply-thread`, then the
block's `Resolve:`; `Cap:` bounds the rounds, and a docs-only fix-only diff ends
them. A window that closes with no round is read per
[review-loop.md](review-loop.md#a-window-that-closes-with-no-round): its stop,
its fallback and the two closed windows that re-poll. A round's
fix is proven by its targeted test nodes plus the repo's edit
rung, the per-file check its harness runs after each edit (`check.sh edit
<file>...`), over the files it touched; a repo with no harness, and so no edit
rung, proves it with the local gate instead. Otherwise the local gate is not
re-run per round: the final head's gate is the merge gate's re-run on `gate
read`'s `current: false`, never while a `code-review` is out (phase 5). A fix
that carries a test is also proven red, per [evidence lines](evidence-lines.md)
(`--fix <fix commit>`). **Every
reviewer whose `Fallback-for:` reads `None.` first, then the fallbacks**, each
driven when its primary exits `not reviewed` (`inline lane` aside), is capped
with findings or stops `not landed`. Exits: `reviewed`, `not reviewed:
<reason>`, or `not invoked: <primary> reviewed`; `not reviewed` proceeds to the
merge gate on green CI and is reported there. At exit, `update-pr-body --section
--body-file` writes the sections the rounds grew, the Change outline where it
fell short, `Review` last, the title checked against the `Grade:`, then the
phase-6 read-back; the cap, docs-only and not-landed rounds earn the local
review [review-loop.md](review-loop.md) describes.

## Phase 8 CI

The text below continues phase 8 in `SKILL.md`.

`ci-wait` and `poll-pr` wait for the expected
head ([mechanics.md](mechanics.md#waits-past-540-s)). On `conflict`,
its stderr carries the recovery. `no-checks` is fine only where `No-checks
legal:` says so. A red leg named on a verification's `Also proven by CI:` line
is that verification failing: back to phase 2. Red after the reviewers exited:
fix, push, proceed on green. Honour `Push policy:`: a push spends CI minutes and
review quota, so push when the tree changed.

## Phase 9 merge gate

The text below continues phase 9 in `SKILL.md`, after the summary is written per
merge-gate.md.

Write the summary per that file. Attended: take its `Unmet criteria` and
`Clean gate held by` lines from `run-file gate summary <pr>`, the settled
`ci-wait` answer saved as `ci-wait.json` beside the Run file first; follow the
`Merge:` branch, waiting for an explicit "merge" by default, or merging when
`Merge: on-clean-gate` or the run is inline lane and that answer reads `clean:
true`; an unmet criterion needs the human's explicit waiver.
On either authorization run `merge <pr> <issue|none> <head> [--worktree
<path>]`, `<head>` the gate's `head_sha`. Invoke it from the worktree root as
`.claude/skills/ship/scripts/merge.sh` where Ship is installed at project scope,
since auto mode has refused the absolute form on a clean gate; the absolute
base-directory path is for an install with no project copy (a global install).
Then `update-issue-body` per tracker draft, file Ship defect
drafts on the human's word, `run-file close 9`, `ExitWorktree` with `action:
"keep"` (`cleanup` owns the removal) so the session is not left in a deleted
folder, then `cleanup
<issue|none>`, last, as it removes the Run file; a nonzero exit, or a `false`
in `merge`'s or `cleanup`'s JSON, re-runs the mechanic that owns the step; a
step no mechanic re-does is a Ship defect for the summary. Unattended: the
final head's gate, then `comment-pr <pr> --body-file` with the summary,
`run-file close 9`. Either lane then returns.

## Hand-back

The text below continues the stop table in `SKILL.md`.

Hand-back is `manage-issue <issue> handback "<reason>"`: unassign, drop
`ready-for-agent`, add `ready-for-human`, comment the reason; one that exits 1
says on its own output what the claim is left as. In an attended run you stop
and ask, and hand back only if the human says stop, to the human queue: never to
the agent queue, which loops forever.
