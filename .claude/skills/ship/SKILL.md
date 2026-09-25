---
name: ship
description: >-
  Drive one tracker issue to a merge-ready PR in a single run, stopping only at
  the human merge gate. Use when the user wants to ship an issue, or to run the
  unattended lane.
argument-hint: "[issue-number] [--unattended]"
metadata:
  version: 8.9.0
  profile-schema: 3
  composes: mattpocock/skills:tdd mattpocock/skills:writing-for-agents mattpocock/skills:code-review upstash/context7:find-docs humanlayer/skills:show-me
---

# ship

Drive one issue from nothing to a **merge-ready PR**, hands-off, stopping only
at the merge gate, so the human runs `/ship <issue>`, walks away, and comes back
to a PR implemented test-first, verified against the real thing the repo
integrates with, self-reviewed, reviewed by every reviewer the repo names,
CI-green, and summarized for a ten-second approve. This skill is **generic**: it
knows how to ship and nothing about the repo, and every repo fact comes from the
**ship profile**, `docs/agents/ship.md`. The copy under `.claude/skills/ship` is
a **derived copy**, changed in its source repo `Gharib89/skills` and refreshed
through the command the repo's `### Ship` block in CLAUDE.md carries.

**Version.** The harness strips this file's frontmatter on load, so read the
version once, at the start of the run, with
`sed -n 's/^  version: //p' <base directory>/SKILL.md`, then print
`ship <version>` in the run header, the first line of the first reply, and again
in the merge summary, so every PR records which ship produced it.

## Argument and flags

`$ARGUMENTS`:

- `<issue>`: the issue number (work item id on Azure DevOps). Omitted with no
  flag: ask which issue.
- Free text instead of a number: the task spec itself. No issue fetch, claim,
  `Closes` or reflect, nor the `Done when:` clauses naming them; `none` is the
  issue argument to `preflight`, `isolate`, `open-pr`, `merge` and `cleanup`.
- `--unattended`: the **unattended run**, detailed in
  [reference/unattended.md](reference/unattended.md). No human is present: a
  blocked stop hands back instead of asking, the sandbox clone is the isolation,
  and the merge gate posts the summary as a PR comment and returns. With no
  `<issue>` it first runs the unattended lane: prepare, PR cap, select.

Without `--unattended` the run is **attended**: any needed human action stops
and asks, and the claim holds while it waits. **Preparation**, before `run-file
init` in every run but the no-issue lane's inner one: `prepare` (`--unattended`
in that lane). In a **cloud sandbox** (`CLAUDE_CODE_REMOTE=true`), or with that
flag, it runs `tooling --install` then the profile's `## Cloud lane`
`Bootstrap:`, elsewhere a no-op. A `failed` step stops the run, no claim, with
its tail: `tooling` as `host-unreachable`, `bootstrap` as `bootstrap-failed`.

## Compose, don't reinline

Load `tdd` (phase 2), `writing-for-agents` (phase 4, agent-facing docs),
`code-review` (phase 4) and `find-docs` (any API claim) through the Skill tool
when their moment comes, taking each one's logic from the skill itself, and tell
any composed skill with an unattended mode that the run is unattended,
explicitly, because it has no other way to know. **Read** `show-me` (phase 6,
the Change outline) instead; [pr-body.md](reference/pr-body.md) says why. The
frontmatter's `composes` line names all five with each one's source repo, and is
what phase 0 checks: a skill added here is added there too, or the run still
fails at the phase that uses it.

## The pipeline

Work the phases in order, keeping the main thread on orchestration and
decisions. **First**, read
[reference/context-discipline.md](reference/context-discipline.md): the
delegation rule, the levers that keep a long run from bloating the window, and
your **required first action, the Run file** holding the ten-item checklist.
Phase 0 starts once the Run file exists, and each phase below flips it with
`run-file open`, `close` or `skip`: a phase closes once the bound in its
`Done when:` sentence is met, and not before.

**A phase runs the mechanic it names**, rather than re-deriving what that
mechanic wraps; [reference/mechanics.md](reference/mechanics.md) maps mechanic
to phase and carries the contract they share, `--help` included, which is where
a mechanic's flags come from. **Every host call you make goes through a
mechanic**: a host operation no mechanic performs is a **Ship defect**, which
goes on the merge summary's `Ship defects:` row for the human to carry upstream,
rather than into a hand-rolled call or an issue filed to another repo.

**0 · Isolate.** [reference/isolate.md](reference/isolate.md) carries what
preflight proves, what it refuses, the profile it loads and its schema check,
and why the worktree is made the way it is. Run `preflight <issue>`, adding
`--unattended` in an unattended run; a bare call admits a `ready-for-human`
issue and the flag turns it into a stop. Read its `reasons`: empty with
`ok: true` is the admission, every entry is a row of the stop table below, and a
push-permission `unknown` is a warning on stderr that you carry to the merge
summary rather than a stop; record in the Run file each `reviewers[]` row
reading `review_on_push: false`, which phase 7 passes on. Then `read-issue
<issue>`, whose result is phase 1's input and from which the branch `<type>`
and `<slug>` are derived. Then
`isolate <issue> <type> <slug>` with the profile's `Carry:` files, or
`isolate ... --in-place` unattended, which leaves branch
`<type>/<slug>-<issue>`, `<type>` matching the issue (`feat`, `fix`, `docs`,
...). Every edit, commit and the PR happen from that branch, from the path
`isolate` printed, and you **commit as you go**, because the PR needs real
commits. A `Bootstrap:` under `## Worktree` runs once here, after isolate.
**Done when:** `preflight` answered `ok: true` with an empty `reasons`,
`isolate` printed a worktree path (or `in_place: true`) on branch
`<type>/<slug>-<issue>`, and a `Bootstrap:` under `## Worktree`, where the
profile carries one, ran green.

**1 · Understand.** Work from phase 0's `read-issue` result: title, body,
labels, assignee, state, comments, open blockers. Derive what success looks
like and write it into the Run file, as criteria a later phase can check. A
later authoritative comment supersedes the body (**spec precedence**, detailed
in [reference/implement.md](reference/implement.md)). Too vague to plan: stop
`ambiguous`, with no claim taken. Otherwise **claim before any work**:
`manage-issue <issue> take`, idempotent, which assigns you and posts the fixed
comment `🤖 Claimed by a ship run: implementation in progress.` The claim holds
until merge; every stop after this point follows the stop table.
**Done when:** `manage-issue <issue> take` answered `claim: taken`, and the Run
file carries the success criteria.

**2 · Implement.** [reference/implement.md](reference/implement.md) carries the
classes, the TDD override, external-claim probes and the judgment/execution
split. Classify `docs` / `code` / `infra`; **announce the class, the skip path
it implies, and whether the three lane keys hold**; announce the applicable
verifications from `## Verification` (their `Applies when:` lines are prose you
judge here) or the skip. Then implement test-first per class; `Tripwires:` and
`In-PR requirement:` land in this change whatever the class. Keep a
**deviations log** from the first edit: whenever the territory forces a departure
from the issue, brief or plan, resolve it by the conservative option, log what and
why, keep going; the log lands verbatim in the merge summary, and is folded to
what a reviewer would act on in the PR body's `## Special things to note`. An
**adjacent find** takes one of implement.md's three dispositions and no fourth.
If the core work balloons (the diff outgrows one PR, or the fix demands a
redesign the issue did not scope), stop `needs-split` with a split proposal.
**Done when:** the applicable tests are green (red first, per class),
`Tripwires:` and `In-PR requirement:` have landed, a small-lane diff is counted
under the cap, the Run file's deviations log carries every departure so far,
and every adjacent find carries one of the three dispositions.

**3 · Verify.** [reference/verify.md](reference/verify.md) carries the
result words, the `Without it:` dispositions and what `unexercised` is. For each
applicable verification, run its `Run:` line **scoped to what you touched**, on
the environment the issue was reported against; green elsewhere is not fixed.
A missing prerequisite takes the entry's `Without it:` line, of which
`defer-to-ci` is the only unattended-safe one and the only one that continues,
because `Also proven by CI:` names the leg you will watch in phase 8. Noisy runs
go to a cheap-tier subagent, given the `verify` scratch directory, returning the
result plus failing lines. `docs` class and the small lane skip this phase.
**Done when:** every applicable verification carries one result word, or
`run-file skip 3` recorded the class or the lane that skipped it.

**4 · Sync docs, then self-review.** Docs first, so the review reads the docs
edits as part of the diff. **Docs-sync fires only when the public surface or
observable behavior changed**: bring the profile's `Targets:` in line, folding
the edits into this change, a tracker issue's as a [drafted section](reference/merge-gate.md#a-tracker-issue-on-targets).
Skip it for internal refactors, a bugfix restoring documented behavior,
test-only or tooling changes, and comments, and say so in one line at the merge
gate. **The `writing-for-agents` pass has a trigger of its own**, and it still
fires where docs-sync is skipped: whenever the diff touches a target on the
profile's `Agent-facing:` line, at the judgment tier, in the `writing` scratch
directory, over every agent-facing file in the diff.
Human prose takes the mechanical pass. With docs-sync's edits landed, load
`code-review`, then dispatch this pass and its two axes in one turn and end it,
each naming its Report file per
[reference/context-discipline.md](reference/context-discipline.md). Small lane:
dispatch the axes, then run this pass inline, writing its Report file before any
disposition.

**Self-review**, unconditional in every lane: invoke `code-review` against the
diff since `origin/HEAD`, its Standards axis reading the profile's
`## Coding standards` path, its Spec axis reading the issue, each axis prompt
carrying its own scratch directory (`standards`, `spec`). **Triage waits for
every Report file.** An axis whose report fails to arrive is
`red-after-retry: <axis>` after the bounded retry, a stop in place of a disposition written from memory of
what it would have said. **Auto-triage** every finding: harden rather than rip out
capability, verify nits against the pinned versions, reject known non-issues,
fix the valid ones, and record a one-line disposition per finding. Two rails on
rejecting: a claim about **what exists in the repo** is checked against
`origin/HEAD` rather than the worktree, which may predate a merge; and a finding's
**evidence and its claim are separate**, so a reviewer citing the wrong commit
for a real primitive is still right. A valid finding outside the issue is an
adjacent find. Then read the diff yourself against
the depth checks in the coding-standards file the Standards axis reads, by their
leading words: a vocabulary the change extends, a rule-shaped prose change, new
pattern-matching code, a new test run with its fix reverted, a fix landed after
review. Reviewer rounds find these otherwise, serially, at the cost of most of a
run's wall time, and the reverted-fix one escapes them entirely. This
self-review plus green CI is the review gate; phase 7's reviewers add a second
pair of eyes on top of it.
**Done when:** both `code-review` axes and, where it fired, the
`writing-for-agents` pass have a Report file on disk with its path in the Run
file, every finding carries a one-line disposition, and docs-sync either landed
its edits (a tracker issue's as a draft on disk) or is skipped in one line for the merge gate.

**5 · Local gate.** *Precondition:* every applicable verification is `pass`,
`deferred-to-ci` or `unexercised`, or the class is `docs`, **and** every phase-4
finding carries a disposition; otherwise you skipped one, go back. Running the
gate while `code-review` is still out is not parallelism: a finding fixed
afterwards pays for a second gate run and a second pass of whatever the
profile's `Tripwires:` names. Run `base-fresh` first: it proves the branch has
seen every commit on its base, the one thing CI cannot, because CI tests the
merge ref, so a branch that predates a merge still goes green while every "does
this exist?" answer you took from the worktree was pre-merge. Behind: rebase,
re-run, then continue. Confirm every `Carry:` file still matches the main
checkout's copy; a difference is `carried file modified`, because ship has no
business editing untracked secrets. Then run the gate at the profile's
`Location:` from the worktree, inline. Small lane: `--small <node>` for one
proving node, the full gate for more (small-lane.md). The gate owns dependency
install and every check CI runs; its verdict is one JSON object: `verdict`
`pass|fail|unavailable`, per-gate statuses
`pass|fail|deferred-to-ci|unavailable`, `gates.secrets` present in every lane.
Unparseable output or a missing `secrets` key reads as `unavailable`. `fail`:
fix loop. Any `deferred-to-ci`: proceed, and the merge summary names each
deferred gate. `unavailable`: stop `local gate unavailable` with the PR
unopened, leaving phase 6 to a run whose gate answers. **Done when:**
`base-fresh` reports the branch not behind its base, every `Carry:` file matches
the main checkout's copy (vacuous at `Carry: None.`), and the gate's JSON reads
`verdict: pass` with `gates.secrets` present.

**6 · Open PR.** [reference/pr-body.md](reference/pr-body.md) carries what the
body holds, the Change outline, the two halves a write reaches and the read-back.
`open-pr <issue> --title --body-file`, **non-draft** (drafts may not trigger a
reviewer). Title: a Conventional-Commit subject derived from the issue,
honouring `Subject constraints:`; it becomes the squash subject that release
tooling reads, and a title that later proves wrong is fixed with
`update-pr-title <pr> --title`. Body: the repo's template per `## PR`, filled
honestly, through its own headings rather than a raw body that bypasses it;
with no template, the run writes the same seven headings into the body itself,
because `open-pr` synthesizes none of them.
**Every title or body write to an open PR ends with `read-pr <pr>`**, whose
`## ` headings are checked against the ones the body is supposed to carry.
Then `reflect <issue> <pr>` so a human reading the issue sees the PR.
**Done when:** `read-pr <pr>` returns an open, non-draft PR whose headings are
the ones the body owes, and `reflect` has posted the link on the issue.

**7 · Reviewers.** [reference/review-loop.md](reference/review-loop.md) carries
convergence per trigger, the cap as a budget, fallback order, clipped rounds,
`--brief`, `Instructions:` handling and degraded detection. For each reviewer
under `## Reviewers`, drive it to convergence: its `Trigger:` (`auto-once`,
`on-push`, `on-request`) fixes its loop and its convergence test, its `Request:`
fixes how a round is asked for, and the profile's `Cap:` budgets the rounds ship
drives; the brand fixes nothing. Zero reviewers: skip the phase. **Order: every
reviewer whose `Fallback-for:` reads `None.` first, then the fallbacks.** Batch
fixes into one push per round, read each round from `poll-pr` (`--brief` is how
a round is read), then answer each thread with `reply-thread` (`fixed in <sha>`,
or the decline and its reason), and the reviewer's `Resolve:` per thread once
every thread carries a disposition. A finding about the PR body itself is a fix
like any other, through the writes pr-body.md names. Exits: `converged`,
`converged, override needed` (a gating reviewer's declined finding, cited with
evidence), `degraded: <reason>` from the fixed vocabulary
`never-queued | blocked | silent | infra-error | cap-hit | unreachable`, or, for
a fallback whose primary converged, `not invoked: <primary> converged`. Degraded
proceeds to the merge gate on green CI and is reported there rather than handed
back. At exit, one `update-pr-body <pr> --section <name> --body-file <path>`
call per section, in this order: `"Special things to note"` where the rounds
grew the deviations log, `"Needs attention"` where a round filed or linked an
issue or met a defect, and `Review` last, one line per reviewer in the fixed
shape review-loop.md carries. Then the phase-6 read-back while the PR is open.
**Done when:** every reviewer carries an exit word, every thread `poll-pr`
returned carries a reply and, where `Resolve:` is not `None.`, is resolved (none
to carry one, where it answered `threads: unavailable`), and `read-pr` shows a
`## Review` section with one line per reviewer, plus `## Special things to note` and `## Needs attention`, each
required only where a round grew that section's own record.

**8 · CI.** CI runs from PR-open and overlaps phase 7; `ci-wait <pr>` covers it,
reading the profile's `Legs:`. `conflict`: a conflicted PR has no merge ref, so
checks sit pending forever; fetch, rebase onto the base, resolve, re-run
`base-fresh` and the local gate, push. `no-checks` is fine only where
`No-checks legal:` says so. A red leg named on a verification's
`Also proven by CI:` line is that verification failing: back to phase 2. Red
after reviewers converged: fix, push, proceed on green; a lint or flake fix
earns no new on-request round, and an on-push reviewer re-reads it on its own,
so wait for its quiet again. Honour `Push policy:`; a push spends CI minutes and
review quota, so push when the tree changed.
**Done when:** `ci-wait` reports every leg on `Legs:` green, or `no-checks`
where `No-checks legal:` admits it, with `mergeable` not `conflict`.

**9 · Merge gate.** [reference/merge-gate.md](reference/merge-gate.md) carries
the summary's shape, what `merge` does, its two refusals and the tracker drafts.
**Hard stop.** Write the summary per that file, uncompressed. Attended: post it
in the conversation and wait for an explicit "merge": the word is exact, and a
near miss is asked back. On approval run `merge <pr> <issue|none> [--worktree
<path>]`, `update-issue-body` per draft, then `cleanup <issue|none>`; a nonzero
exit, or a `false` in `merge`'s or `cleanup`'s JSON, re-runs the mechanic that
owns the step, and a step no mechanic re-does is a Ship defect for the summary.
Unattended: `comment-pr <pr> --body-file` with the summary, and return.
**Done when:** attended, `merge`, every `update-issue-body` (none where the run
drafted none) and `cleanup` exited 0, with no `false` from `merge` or `cleanup`;
unattended, `comment-pr` posted the summary and the run returned the PR link.

## The stops

One guaranteed stop, the **merge gate**: merging is effectively irreversible, so
a human says merge and ship merges on that word alone: ship never merges on its
own or uses an auto-merge flag. Two conditional pauses in an attended run: the
`ambiguous` stop (phase 1) and a **hand-off** (phase 3). Everything else,
triaging your own findings, fixing, re-running, is autonomous. Two guardrails
hold around that:

- **Red is fixed or reported.** Any failure before the merge gate gets at most
  two fix-and-retry attempts; still red after the second is `red-after-retry:
  <what>`, and a failure saying the approach is wrong stops sooner. Either way
  **stop and report** with the concrete evidence and, if cheap, a
  verified-working alternative, so the report is a fast yes.
- **Every stop has a name**, reported verbatim, with the claim action below. A
  sibling maps the name, a human reads it.

| Stop | Reason | Claim |
|---|---|---|
| Profile missing or invalid, host unreachable | `profile missing`, `profile invalid: <detail>`, `host-unreachable` | no claim |
| A skill ship composes is not installed | `skill missing: <skill>; run <install line>` | no claim |
| Preflight not actionable | `closed`, `is a pull request`, `already claimed`, `existing PR`, `existing branch`, `worktree exists`, `not triaged: run /triage first`, `ready-for-human: attended only` | no claim |
| Issue too vague to plan | `ambiguous` | no claim |
| Change outgrows one PR, or needs a redesign the issue did not scope | `needs-split` | attended: ask; unattended: hand back |
| A find shows the issue is mis-specified | `mis-specified` | attended: ask; unattended: hand back |
| Verification prerequisite missing | `hand-off` (attended waits, claim holds) / `blocked-verification` | attended: hold; unattended: hand back |
| Local gate verdict `unavailable` | `local gate unavailable: <gates>` | attended: ask; unattended: hand back |
| Carried file changed | `carried file modified: <file>` | attended: ask; unattended: hand back |
| Red after retries | `red-after-retry: <what>` | attended: ask; unattended: hand back |
| The branch fell behind its base before the merge | `stale-base: behind <n> on <base>` | attended: holds while you rebase; unattended: hand back |
| The PR is closed at the merge gate | `pr-closed: <state>` | attended: ask; unattended: hand back |
| `prepare` failed its Cloud lane `Bootstrap:` | `bootstrap-failed` | no claim |
| Open PRs at or above the profile's `PR cap:` | `pr-queue-full` | no claim |
| No issue passes selection | `nothing-ready` | no claim |
| The host's blocker query exists and failed | `blockers-unavailable` | no claim |
| Merge gate reached | none: the run's success | holds until merge |

Hand-back is `manage-issue <issue> handback "<reason>"`: unassign, drop
`ready-for-agent`, add `ready-for-human`, comment the reason. In an attended run
you stop and ask; only if the human says stop do you hand back, to the human
queue: never hand back to the agent queue, which loops forever. A hand-back that
prints `claim: released` and still exits 1 did release the claim; read
`handed_back` and report which label is missing rather than a clean stop. One
that prints `{"error": ...}` instead failed before the unassign landed: the
claim is still held and the stop is unresolved.

## The lanes

Every run is **full lane** until the change proves it **small**: all three keys
hold, asserted by you at phase 2 and announced with the class, a call you make
alone. When unsure, it is not small.

1. **No public-surface change.** The public surface is the profile's
   `## Public surface`; `Default.` means the exported or published API, CLI
   flags and exit codes, config schema, file formats and documented behavior.
2. **Provable without the real thing.** A unit or regression test fully proves
   it, or the class is `docs` and there is no behavior to test; either way no
   verification in the profile's `## Verification` applies.
3. **Contained.** No new dependency, none of the profile's `Tripwires:`
   fired, and the diff inside the size cap small-lane.md counts.

Behavior change is allowed: a bugfix is one. Small means narrow, locally
provable, invisible to the public surface and inside the size cap. Small: read
[reference/small-lane.md](reference/small-lane.md) before continuing. Its floor
is the same in every repo, and the lane revokes one way only.

## Model tiers

Use the cheapest model that fits; reserve the strong tier for judgment. Tag
every subagent with a model explicitly and with the scratch directory the
scratch rule in [reference/context-discipline.md](reference/context-discipline.md)
names; neither is inherited.

| Work | Model |
|---|---|
| Investigation and mapping | haiku |
| Phase-2 **execution** from a settled plan; mechanical edits and fixes; the docs-sync pass on human prose; the `code-review` skill's **Spec** axis | sonnet |
| Phase-2 **judgment** (classification, plan, design, the implementation brief); triage of every finding; the `writing-for-agents` pass; the `code-review` skill's **Standards** axis | opus |

When you invoke `code-review`, tier its two axes yourself. Fall back to the
nearest available tier rather than running everything on one model.

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
- **Comments record the why.** Document public APIs and non-obvious
  constraints, invariants and workarounds; skip narrative comments on internals.
- **Concise PR body, always the why.** State what changed and why; the reader
  has the diff for the how. The merge summary is exempt: write it uncompressed
  per [reference/merge-gate.md](reference/merge-gate.md).

## Consult current docs

While implementing (phase 2) or triaging findings (phases 4 and 7), verify API
claims against **current** docs through the `find-docs` skill and the extra
`Sources:` the profile names. For every library on the profile's
`Pinned:` line, read the installed version from the repo's manifest and confirm
the claim against that version before acting on it; a remembered API the
installed version lacks is a regression.
