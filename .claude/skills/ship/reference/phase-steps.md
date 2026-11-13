# Phase steps

The long steps of nine phases, moved out of `SKILL.md` so it stays short. Each
section adds to its phase there, which keeps the summary and the `Done when:`;
read the section here when the run enters that phase.

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

`preflight` takes `--unattended` in an unattended run: every `reasons` entry is
a row of the stop table, and every `warnings` entry a Ship note recorded after
`run-file init` ([isolate.md](isolate.md#what-preflight-proves)). The
`read-issue` result, reused for an issue, is phase 1's input, from which the
branch `<type>` (`feat`, `fix`, `docs`, ...) and `<slug>` are derived. Then
`isolate <issue> <type> <slug>` with the profile's `Carry:` files, or `isolate
... --in-place` unattended. Attended, `EnterWorktree` with `path` set to that
path comes right after isolate, before any edit or `LSP` call: the language
server roots where the session stands when it first starts
([isolate.md](isolate.md#entering-the-worktree)). Every edit, commit and the PR
happen from the path `isolate` printed, and you **commit as you go**, because
the PR needs real commits. A `Bootstrap:` under `## Worktree` runs once here,
after isolate.

## Phase 1 anchors and criteria

An anchor the tree contradicts rewrites the body through `update-issue-body`
only when it changes a criterion, else it is logged
([implement.md](implement.md#phase-1-anchors-the-issue-cites)). A criterion
needing a host action no mechanic performs stays verbatim: unattended, it is an
**unmet criterion** the merge summary lists; attended, ask first, per
[spec-gaps.md](spec-gaps.md#a-criterion-no-mechanic-can-perform) on both. Only
then settle what success looks like: an issue's `## Criteria` as init recorded
them, else criteria a later phase can check.

## Phase 2 implement

The classes are `docs` / `code` / `infra`; the applicable verifications come
from `## Verification`, whose `Applies when:` lines are prose you judge here,
and by default you say what you chose. The grade is the public surface change's,
under the profile's `Subject constraints:`: `open-pr` and `update-pr-title`
refuse a title type that grades lower. Then implement test-first per class
([implement.md](implement.md#phase-2-classify-then-implement-test-first)). The
**deviations log**: whenever the territory forces a departure from the issue,
brief or plan, take the conservative option, log what and why, keep going; it
lands verbatim in the merge summary. `needs-split` stops with a proposal;
[spec-gaps.md](spec-gaps.md#needs-split) lists tells and the proposal's shape.

## Phase 3 verify

Run each verification on the environment the issue was reported against:
green elsewhere is not fixed. Record each run with `run-file ran <name>...
--issue <issue>` as it ends, so `close 3` stamps the head it ran at, not the
head it is closed at. Noisy runs go to a cheap-tier subagent in the `verify`
scratch directory. Skip this phase only when no Verification applies, whatever
the class.

## Phase 4 self-review

The Standards axis reads the profile's `## Coding standards` path and the Spec
axis the issue, each axis prompt carrying its own scratch directory
(`standards`, `spec`) and, by default, saying the Local gate runs the suite
later. A Report file that never arrives once
[retries](../SKILL.md#the-stops) stop helping is `red-after-retry: <axis>`,
never a disposition written from memory. **Auto-triage** every finding at the
judgment tier, under the rails and depth checks in
[implement.md](implement.md#phase-4-triage-and-depth-checks), then
[loop](implement.md#phase-4-the-self-review-loop) up to preflight's
`self_review_cap`. The evidence lines `close 4` wants are in
[evidence-lines.md](evidence-lines.md).

## Phase 4 writing pass

The pass fires whenever the diff touches a target on the profile's
`Agent-facing:` line, at the judgment tier, in the `writing` scratch directory,
over every agent-facing file in the diff. Human prose takes the mechanical pass.
Dispatch it with the two axes and end the turn, each naming its Report file.
Small lane: dispatch the axes, then run this pass inline.

## Phase 5 local gate

`base-fresh` comes first because CI tests the merge ref, so a branch that
predates a merge still goes green while every "does this exist?" answer taken
from the worktree was pre-merge. A `Carry:` file differing from the main
checkout's copy is `carried file modified`, because ship never edits untracked
secrets. Small lane: the gate runs per [small-lane.md](small-lane.md). The
gate's JSON holds `verdict` `pass|fail|unavailable`, per-gate statuses
`pass|fail|deferred-to-ci|unavailable` and `gates.secrets` in every lane;
unparseable output or a missing `secrets` key reads as `unavailable`. `fail`:
fix loop. `deferred-to-ci`: proceed, the merge summary naming each deferred
gate. `unavailable`: stop `local gate unavailable`, the PR unopened.

## Phase 7 reviewers

Each thread's answer is `reply-thread`, then the block's `Resolve:`; `Cap:`
bounds the rounds, and a docs-only fix-only diff ends them. A window that
closes with no round is read per
[review-loop.md](review-loop.md#a-window-that-closes-with-no-round): its stop,
its fallback and the two closed windows that re-poll. A round's fix is proven
by its targeted test nodes plus the repo's edit rung, the per-file check its
harness runs after each edit (`check.sh edit <file>...`), over the files it
touched; a repo with no harness, and so no edit rung, proves it with the local
gate instead. Otherwise the local gate is not re-run per round: the final
head's gate is the merge gate's re-run on `gate read`'s `current: false`, never
while a `code-review` is out (phase 5). A fix that carries a test is also
proven red, per [evidence lines](evidence-lines.md) (`--fix <fix commit>`). A
fallback is driven when its primary exits `not reviewed` (`inline lane` aside),
is capped with findings or stops `not landed`
([review-unconverged.md](review-unconverged.md#fallbacks)). The exit words and
the exit write-up are in [review-loop.md](review-loop.md#the-exit).

## Phase 8 CI

`ci-wait` and `poll-pr` wait for the expected
head ([mechanics.md](mechanics.md#waits-past-540-s)). On `conflict`,
its stderr carries the recovery. `no-checks` is fine only where `No-checks
legal:` says so. A red leg named on a verification's `Also proven by CI:` line
is that verification failing: back to phase 2. Red after the reviewers exited:
fix, push, proceed on green. Honour `Push policy:`: a push spends CI minutes and
review quota, so push when the tree changed.

## Phase 9 merge gate

The summary's `Host protections` line comes from `protections <pr>`. Attended:
take its `Unmet criteria` and
`Clean gate held by` lines from `run-file gate summary <pr>`, the settled
`ci-wait` answer saved as `ci-wait.json` beside the Run file first; follow the
`Merge:` branch, waiting for an explicit "merge" by default, or merging when
`Merge: on-clean-gate` or the run is inline lane and that answer reads `clean:
true`; an unmet criterion needs the human's explicit waiver.
Where `update-pr-body` or `update-pr-title` edited the PR since the last
`ci-wait`, re-run `ci-wait <pr> --sha <head>` and save its answer to
`ci-wait.json` first ([merge-gate.md](merge-gate.md#attended-post-then-wait)).
On either authorization record the admission with `run-file gate admit clean
<ci-file> --head <head> --issue <issue|slug>` or `run-file gate admit human
--head <head> --issue <issue|slug>`. Then run `merge <pr>
<issue|none> <head> [--worktree <path>] [--run <slug>]`, `<head>` the gate's
`head_sha`. Invoke it from the worktree root as
`.claude/skills/ship/scripts/merge.sh` where Ship is installed at project scope,
since auto mode has refused the absolute form on a clean gate; the absolute
base-directory path is for an install with no project copy (a global install).
Then run the closing sequence in
[merge-gate.md](merge-gate.md#attended-post-then-wait)'s paragraph opening
"Then each drafted tracker section", `cleanup` last as it removes the Run
file; a nonzero exit, or a `false` in `merge`'s or
`cleanup`'s JSON, re-runs the mechanic that owns the step; a step no mechanic
re-does is a Ship defect for the summary. Unattended: the
final head's gate, then `comment-pr <pr> --body-file` with the summary,
`run-file close 9`, per
[unattended.md](unattended.md#the-merge-gate-post-to-the-pr-then-return).
Either lane then returns.

## Hand-back

Hand-back is `manage-issue <issue> handback "<reason>"`: unassign, drop
`ready-for-agent`, add `ready-for-human`, comment the reason; one that exits 1
says on its own output what the claim is left as. In an attended run you stop
and ask, and hand back only if the human says stop, to the human queue: never to
the agent queue, which loops forever.
