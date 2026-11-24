---
name: ship-run
description: Drive one tracker issue to a merge-ready PR in a single run, reaching the merge gate or merging when the repo opts in and the gate is clean. Use when the user wants to ship an issue, or to run the unattended lane.
argument-hint: "[issue-number] [--unattended | [--rework [\"<feedback>\"]] [--inline [--review]]]"
metadata:
  version: 0.74.1
  profile-schema: "5"
  composes: mattpocock/skills#f3fc5632f401156837ee3872f14fe33ccf1024ea:tdd mattpocock/skills#a496f5513d4a4cf889bc48e189e579f4b0bdf6aa:codebase-design mattpocock/skills#c55ee46073ed923f86ce59a5eb3b6d895095d1b7:writing-for-agents mattpocock/skills#a496f5513d4a4cf889bc48e189e579f4b0bdf6aa:code-review upstash/context7#681554c5d3a688c67879d02d4f01d79ffd15df13:find-docs humanlayer/skills#ca7c8088db69e315a8b2deea43820270457f8f3c:show-me
---

# ship-run

This skill is **generic**: it knows how to ship and nothing about the repo, and
every repo fact comes from the **ship profile**, `docs/agents/ship.md`. The copy
under `.claude/skills/ship-run` is a **derived copy**, changed in its source
repo `Gharib89/skills` and refreshed through the command the repo's `### Ship`
block in CLAUDE.md carries.

**Version.** The harness strips this file's frontmatter on load, so read the
version once, at the run's start, with
`sed -n 's/^  version: //p' <base directory>/SKILL.md`; by default print
`ship <version>` in the first reply and the merge summary, so the PR records
which ship produced it.

## Argument and flags

`$ARGUMENTS`:

- `<issue>`: the issue number (work item id on Azure DevOps); `--unattended`
  alone takes none, the no-issue lane. Omitted with no flag: ask which issue.
- Free text instead of a number: the task spec itself. No issue fetch, claim,
  `Closes` or reflect, nor the `Done when:` clauses naming them; `none` is the
  issue argument to `preflight`, `isolate`, `open-pr`, `merge` and `cleanup`.
- `--unattended`: the **unattended run**, detailed in
  [reference/unattended.md](reference/unattended.md). No human is present: a
  blocked stop hands back instead of asking, the sandbox clone is the isolation,
  and the merge gate posts the summary as a PR comment and returns. With no
  `<issue>` it first runs the unattended lane: prepare, PR cap, select.
- `--inline [--review]`: the **inline lane**; before `prepare`, read
  [small-lane.md](reference/small-lane.md#the-inline-lane) for its rules.
- `--rework ["<feedback>"]`: the **rework run**, reworking the PR an earlier
  run left at the merge gate; before `run-file init`, read
  [rework.md](reference/rework.md). Attended only; `--inline` composes.

Without `--unattended` the run is **attended**: any needed human action stops
and asks, and the claim holds while it waits. **Preparation**, before `run-file
init` in every run but the no-issue lane's inner one: `prepare` (`--unattended`
in that lane). In a **cloud sandbox** (`CLAUDE_CODE_REMOTE=true`), or with that
flag, it runs `tooling --install` then the profile's `## Cloud lane`
`Bootstrap:`, elsewhere a no-op. A `failed` step stops the run, no claim, with
its tail: `tooling` as `host-unreachable`, `bootstrap` as `bootstrap-failed`.

## Compose, don't reinline

Load `tdd` (phase 2), `writing-for-agents` (phase 4, agent-facing docs),
`code-review` (phase 4) and `find-docs` (any API claim, the profile's `Pinned:`
libraries included: [implement.md](reference/implement.md#consult-current-docs))
through the Skill tool, and tell any composed skill with an unattended mode that
the run is unattended: it has no other way to know. **Read**
`show-me` (phase 6, the Change outline) instead;
[pr-body.md](reference/pr-body.md) says why.

## The pipeline

Work the phases in order, keeping the main thread on orchestration and
decisions. **First**, read
[reference/context-discipline.md](reference/context-discipline.md): defaults
that keep a long run from bloating the window, and what it still requires. Then
the run's **first write**: after `prepare`, an issue's `read-issue` and
before phase 0, `run-file init <issue|slug> --from-profile` initializes the Run
file holding the ten-item checklist; an issue's run adds `--criteria -`,
per [implement.md](reference/implement.md#phase-1-criteria-the-run-owes). Each
phase below flips it with `run-file next`, `open`, `close` or `skip`, and closes
once its `Done when:` holds, not before.

**A turn never ends on a plan line.** It ends on a stop from the table below
(the merge gate included) or on a wait its last line names: a background job or
subagent whose notification resumes the run. A line saying what comes next is
followed by doing it in the same turn. When that job or subagent comes back,
announce its end in one line before the next step, so the human sees the run
move on.

**A phase runs the mechanic it names**, rather than re-deriving what that
mechanic wraps. **Every gating call goes through a mechanic**, a host call a
later phase, stop, merge or release depends on: one no mechanic performs is a
**Ship defect** for the summary, never hand-rolled, and never holds the clean
gate. Any other uncovered call is a **direct call** through `gh api` or `az
rest`, logged first with `run-file call`; worked-around friction is a **Ship
note**. [reference/mechanics.md](reference/mechanics.md) lists the gating calls,
each phase's mechanic and their shared contract, `--help` included.

**0 · Isolate.** `preflight <issue>`, then `isolate`, per
[isolate.md](reference/isolate.md).
**Done when:** `preflight` answered `ok: true`, `isolate` printed its path, any
`## Worktree` `Bootstrap:` ran green, and, attended, the session is in that path
(`EnterWorktree`, or
[adopted](reference/isolate.md#adopting-the-launched-worktree)).

**1 · Understand.** Too vague to plan from the `read-issue` result: stop
`ambiguous`, with no claim taken. Otherwise **claim before any work**:
`manage-issue <issue> take --fresh` (bare in a rework or resumed run, holding
it already), held until merge; later stops follow the stop table. Then grep
each anchor the issue cites and settle the criteria, per
[implement.md](reference/implement.md#phase-1-spec-precedence).
**Done when:** `claim: taken`, anchors grepped, the Run file holds the criteria.

**2 · Implement.** Classify, record the grade, implement test-first and log
every departure, per
[implement.md](reference/implement.md#phase-2-classify-then-implement-test-first).
**Done when:** the applicable tests are green (red first, per class), the
tripwires and in-PR requirement have landed, `Grade:` is recorded, a small-lane
diff is counted, and every departure and adjacent find so far is in the Run
file with its disposition.

**3 · Verify.** Run each applicable verification, per
[verify.md](reference/verify.md).
**Done when:** `run-file close 3` took a `--result` for every Verification,
each applicable one `pass`, `deferred-to-ci` or `unexercised`; any other result
is recorded, then takes its named stop. Or `run-file skip 3` recorded what
skipped it.

**4 · Sync docs, then self-review.** Docs-sync, the `writing-for-agents` pass,
then the self-review loop, per
[implement.md](reference/implement.md#phase-4-docs-sync-and-the-writing-pass).
**Done when:** every report that fired has its Report file on disk and its path
in the Run file, every finding carries a disposition, `close 4` took the
evidence lines, and docs-sync landed or is skipped in one line.

**5 · Local gate.** `base-fresh`, `carry-check`, then the gate, per
[phase-steps.md](reference/phase-steps.md#phase-5-local-gate).
**Done when:** `base-fresh` answered `fresh: true`, `carry-check` `ok: true`,
and the recorded verdict reads `verdict: pass` with `gates.secrets` present.

**6 · Open PR.** `open-pr`, then `reflect`, per
[pr-body.md](reference/pr-body.md#opening-it).
**Done when:** `read-pr` shows an open, non-draft PR carrying the headings the
body owes, and `reflect` posted.

**7 · Reviewers.** A bounded round loop per reviewer under `## Reviewers`, per
[review-loop.md](reference/review-loop.md).
**Done when:** every reviewer carries an exit word, every thread `poll-pr`
returned is replied to and resolved per `Resolve:`, every section the rounds
grew is rewritten, the title matches `Grade:`, any local review a cap, docs-only
or not-landed round earned is dispositioned (its `Past-cap:` or `Docs-only:`
line recorded where one applies), `close 7` took each reviewer's
`Round:` and `Stop:` lines, and `read-pr` shows a `## Review` line per reviewer.

**8 · CI.** `ci-wait <pr>`, overlapping phase 7, per
[phase-steps.md](reference/phase-steps.md#phase-8-ci).
**Done when:** `ci-wait` answered `green`, each `missing_legs` entry one
`No-checks legal:` admits, or `no-checks` where it admits that.

**9 · Merge gate.** The summary, then the merge or the return, per
[merge-gate.md](reference/merge-gate.md).
**Done when:** attended, `merge` and every `update-issue-body` exited 0 with no
`false`, then `close 9`, then `ExitWorktree` unless adopted, then `cleanup
<issue|none>` exited 0 with no `false`; unattended, `comment-pr` posted.

## The stops

The **merge gate** stops as phase 9 says; the unattended lane posts and returns.
**The stop table below is authoritative.** Everything else, triaging your own
findings, fixing, re-running, is autonomous. Two guardrails hold around that:

- **Red is fixed or reported.** Retry a failure before the merge gate while
  retries make progress (two attempts is a fair mark); once they stop, it is
  `red-after-retry: <what>`, and one saying the approach is wrong stops at
  once. Either way **stop and report** with the concrete evidence and, if
  cheap, a verified-working alternative, so the report is a fast yes.
- **Every stop has a name**, reported verbatim, with the claim action below.

| Stop | Reason | Claim |
|---|---|---|
| Profile missing or invalid, host unreachable | `profile missing`, `profile invalid: <detail>`, `host-unreachable` | no claim |
| A skill ship composes is not installed at its pin, or its pin is malformed | `skill missing: <skill>; run <install line>`, `skill off pin: <skill> at <ref>, pinned <sha>; run <install line>`, `skills lock unreadable: skills-lock.json; repair it, then re-run preflight`, `composes pin invalid: <entry>; want <form>` | no claim |
| Preflight not actionable | `issue #<n> not found or unreadable`, `closed`, `is a pull request`, `already claimed`, `existing PR`, `existing branch`, `worktree exists`, `not triaged: run /triage first`, `ready-for-human: attended only` | no claim |
| Issue too vague to plan | `ambiguous` | no claim |
| Another run claimed it after preflight | `contested: <others>`, `held: another run of this identity holds it` | `contested`: no claim; `held`: the other run's |
| A `--rework` run not admitted, or with nothing to rework | `not reworkable: <detail>`, `no feedback` | unchanged |
| Change outgrows one PR, or needs a redesign the issue did not scope | `needs-split` | attended: ask; unattended: hand back |
| A find shows the issue is mis-specified | `mis-specified` | attended: ask; unattended: hand back |
| Verification prerequisite missing | `hand-off` (attended waits, claim holds) / `blocked-verification` | attended: hold; unattended: hand back |
| Local gate verdict `unavailable` | `local gate unavailable: <gates>` | attended: ask; unattended: hand back |
| Carried file changed | `carried file modified: <file>` | attended: ask; unattended: hand back |
| Red after retries | `red-after-retry: <what>` | attended: ask; unattended: hand back |
| The branch fell behind its base before the merge | `stale-base: behind <n> on <base>` | attended: holds while you merge the base in; unattended: hand back |
| The PR is closed at the merge gate | `pr-closed: <state>` | attended: ask; unattended: hand back |
| The PR's head moved after the merge gate read it | `head-moved: <current> != <gated>` | attended: holds while the gate re-reads the new head; unattended: not reached, the lane runs no merge |
| `merge` refused before the host call | `no-gate-record: ...`, `checks-unsettled: ...`, `cloud-sandbox: ...` | attended: holds while the admit step or `ci-wait` is re-run, a `cloud-sandbox` ends with the summary for a human to merge; unattended: not reached, the lane runs no merge |
| `--inline` with `--unattended` or free text | `inline refused` | no claim |
| `prepare` failed its Cloud lane `Bootstrap:` | `bootstrap-failed` | no claim |
| Open PRs at or above the profile's `PR cap:` | `pr-queue-full` | no claim |
| No issue passes selection | `nothing-ready` | no claim |
| The host's blocker or sub-issue query exists and failed | `blockers-unavailable` | no claim |
| Merge gate reached | none: the run's success | holds until merge |

Hand-back is `manage-issue <issue> handback "<reason>"`; the rules, attended
and unattended, are in
[phase-steps.md](reference/phase-steps.md#hand-back).

## The run contract

Whoever launched the run:

- A turn that ends only to wait on the run's own background work (a check, CI, a
  subagent) ends with this line, word for word, as its last line,
  without the backticks: `Waiting on my own background work.`
- A run that starts in a worktree on a branch ending `-<issue>` works there, on
  that branch, and makes no other: the PR is found by that branch.
- The last message ends on the PR URL and the gate state, or on the stop reason
  before the gate, never the waiting line.
- After a stop at a merge-gate hold, a later message in the thread may carry a
  ruling on that hold: act on it, not as a new task. A ruling never goes into
  the PR body, except a waiver that passed the gate, which the body names.

## The lanes

Every run is **full lane** until the change proves it **small**: all three keys
hold, as you judge them at phase 2. When unsure, it is not small.

1. **No public-surface change.** The public surface is the profile's
   `## Public surface`; `Default.` means the exported or published API, CLI
   flags and exit codes, config schema, file formats and documented behavior.
2. **Provable without the real thing.** A unit or regression test fully proves
   it, or the class is `docs` and there is no behavior to test; either way no
   verification in the profile's `## Verification` applies.
3. **Contained.** No new dependency, none of the profile's `Tripwires:`
   fired, and the diff within the size small-lane.md describes.

Behavior change is allowed: a bugfix is one. A diff touching a guard,
allowlist or deny rule is full lane, inline under `--review` aside. Small or
inline: read [reference/small-lane.md](reference/small-lane.md) first.

## Model tiers

The tier table in
[context-discipline.md](reference/context-discipline.md#model-tiers) is each
subagent's default model. Every subagent prompt names its scratch directory,
which is not inherited.

## Working standards

These bind every edit a run makes, in every repo, regardless of a personal
`~/.claude/CLAUDE.md`, `~/.claude/rules/` or personal skills:

- **Simplest shape that solves the problem.** No speculative features,
  configurability or abstraction for single-use code; if the diff could be a
  third the size, rewrite it.
- **Surgical.** Touch only what the issue needs, every changed line tracing to
  it or to a logged deviation, matching existing style, leaving adjacent code,
  comments and formatting alone.
- **Root cause, not symptom.** No temporary patches, no swallowed errors, no
  `TODO` standing in for the fix; if the real fix is out of scope, say so and
  file it.
- **Callers found, dependencies justified.** Before changing a contract, find
  its callers and tests and update each one the change breaks; justify a new
  dependency in the PR body; [implement.md](reference/implement.md)'s phase 2
  says how.
- **Comments record the why.** Document public APIs and non-obvious
  constraints, invariants and workarounds; skip narrative comments on internals.
- **Concise PR body, always the why.** State what changed and why; the reader
  has the diff for the how. The merge summary is exempt: write it uncompressed.
