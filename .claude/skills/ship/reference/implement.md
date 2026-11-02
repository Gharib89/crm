# Phases 1, 2 and 4: understand, implement, self-review (detail)

## Contents

- [Phase 1: spec precedence](#phase-1-spec-precedence)
- [Phase 1: anchors the issue cites](#phase-1-anchors-the-issue-cites)
- [Phase 1: a criterion no mechanic can perform](#phase-1-a-criterion-no-mechanic-can-perform)
- [Phase 1: criteria the run owes](#phase-1-criteria-the-run-owes)
- [Phase 2: classify, then implement test-first](#phase-2-classify-then-implement-test-first)
- [Phase 2: verify the spec's external-system claims before building on them](#phase-2-verify-the-specs-external-system-claims-before-building-on-them)
- [Phase 2: delegate execution, keep judgment](#phase-2-delegate-execution-keep-judgment)
- [Phase 2: needs-split](#phase-2-needs-split)
- [Phase 2: adjacent finds](#phase-2-adjacent-finds)
- [Phase 4: triage and depth checks](#phase-4-triage-and-depth-checks)
- [Phase 4: the self-review loop](#phase-4-the-self-review-loop)
- [Consult current docs](#consult-current-docs)

## Phase 1: spec precedence

A later triage brief or authoritative comment can *supersede* the issue body,
and nothing else can. An **authoritative comment** is one `read-issue` marks
`writer: true`, its author having write access to the repo; a rework run's PR
comment takes the same grade. A **triage brief**
is the `## Agent Brief` comment triage posts, and it counts only when it is
`writer: true` too. A comment marked `false` or `null` (the host could not
answer, always on Azure DevOps) is context, never spec, in every lane. Where the
latest brief or authoritative comment conflicts with the body (scope reduced, an
option chosen, an axis dropped), it wins and the body's original acceptance
criteria no longer bind. Name that comment, by author and date, in the
deviations log, and expect a reviewer reading the stale body to flag "missing"
requirements; reject those in phases 4 and 7 with the comment as evidence.

## Phase 1: anchors the issue cites

An issue that names a file, a heading or a step number in another skill or
file records what its author believed when writing it. Grep each one in the
worktree before planning, because a run that builds on a wrong anchor spends
its reviewer rounds unwinding it. Where the tree contradicts one, the tree
wins. A wrong anchor that changes no acceptance criterion goes in the
deviations log alone. One that changes a criterion rewrites the issue body:

1. Rewrite the issue section the anchor sits in to match the tree with
   `update-issue-body <issue> --section <name> --body-file <path>`, the
   original section kept below the rewrite in a column-0 `<details>` block. The
   mechanic carries that `<details>` record through every later write to the
   section, so a later file holds the new content alone. An anchor in
   the preamble, which the mechanic leaves alone, is restated in the section
   whose criteria build on it.
2. Build against the rewritten criteria, and log the substitution in the
   deviations log.

A contradiction that leaves the issue nothing to build is the `mis-specified`
stop instead.

## Phase 1: a criterion no mechanic can perform

A criterion that asks for a host action no mechanic performs (a repo setting, a
branch protection, an installed app) is one the run cannot meet, and the run
never shrinks the issue's scope on its own authority: the criterion stays
verbatim in the issue body.

- **Unattended**: deliver the rest, log the gap in the deviations log, and list
  the criterion on the merge summary's `Unmet criteria` item with the action a
  human must take. Merging then needs the human's explicit waiver of each one
  ([merge-gate.md](merge-gate.md#unmet-criteria)).
- **Attended**: ask the human which way before building; an answer that the
  issue is wrong is the `mis-specified` stop, and one to build the rest makes
  it an unmet criterion as above.

## Phase 1: criteria the run owes

An issue's run reads the issue first, `read-issue <issue>`, a read that claims
nothing (a failed read stops the run there, `host-unreachable`), and settles the
effective spec by [spec precedence](#phase-1-spec-precedence): the body's
criteria, less each a later `writer: true` brief or comment drops or revises,
plus the criteria it adds. `run-file init <issue> --from-profile --criteria -`
takes that text as a quoted heredoc, so the shell expands none of the issue's
own words. That form works only before isolation: a session already isolated
writes the text with the Write tool and passes `--criteria <file>`, since the
guard refuses a heredoc naming git
([context-discipline.md](context-discipline.md#once-isolated)). Init records
each checkbox line (`-`, `*`, `+` or a numbered item) as an
entry under the Run file's `## Criteria`, `- <n>: <text>`. A heading naming
criteria (`## Acceptance criteria`) whose list holds no checkbox gives its
top-level list lines instead; each criteria heading then gives its own kind,
and a checkbox under no criteria heading (a task list) is none. So pass the
text with its headings, and where that list holds a checkbox, write each
criterion a brief or comment adds as a checkbox too.
Text yielding none writes no section and
warns: record each criterion with `run-file note Criteria <text>`. It refuses
the text `null`. Phase 0 reuses this read. A free-text run has no issue and
passes none.
An issue's init without `--criteria` warns when the issue holds criteria, and
`run-file gate summary` then holds the gate (`criteria:` in `held_by`) until
each is recorded with `run-file note Criteria <text>`.
A phase-1 anchor rewrite that changes a criterion adds each replacement
criterion with `run-file note Criteria <text>`, which writes it under the next
number, owing its own line, and the original's `Criterion` line names them.

Every entry is owed one line, anywhere in the file, written by the run as it
settles the criterion: `Criterion <n>: met: <proof>`, the proof a test, a
command or a grep, never a bare "done", or `Criterion <n>: not met: <reason>`,
the host action a human must take where that is the reason. `run-file note`
refuses a `Criterion` line in any other form (`met at <sha>:` included), which
the gate would read as unmet: put the sha inside the proof. The last line for
an `<n>` decides, so a later `met` replaces an earlier `not met`. A criterion is
met by what the diff and its checks show, never by the summary's account of
them: one the run declined, deferred or covered with a "gap" is `not met`.
`run-file gate clean` holds the merge on each entry that is `not met` or has no
line, and on each `<n>` a `Criterion <n>:` line names without an entry, unless
its last line is `met` ([merge-gate.md](merge-gate.md#unmet-criteria)).

`run-file close 5` refuses an entry with no line or a placeholder `not met`
(pending, todo, tbd, later, wip, open, unknown). One settling after phase 5 is
written at that close `Criterion <n>: not met: <what is outstanding, and the
step or human action that settles it>`; that step writes `met: <proof>`,
replacing it.

## Phase 2: classify, then implement test-first

Classify the change into one of three classes. By default, say the class and
the skip path it implies ("classified `docs`: skipping TDD; phase 3 runs only
the Verifications that apply"), so a wrong label is a visible decision now, not
a silently skipped verification later.

- **`docs`**: markdown, comments, manifest text with no logic. **Skip TDD**;
  do not manufacture a contrived test. Commit `docs:`.
- **`code`**: a feature or bugfix in behavior. Invoke the `tdd` skill
  **autonomously**: red, then green, **without pausing to confirm seams**
  (you are intentionally overriding tdd's checkpoint; the merge gate is the
  review point). A new test goes where the repo's tests for that area live, and
  a new test file is registered wherever the runner discovers files, which the
  profile's `Small node:` syntax tells you.
- **`infra`**: tooling or a refactor where a strict red-green is awkward: the
  change *is* the build, CI, a fixture or the test harness. Extract the logic
  into a testable seam and unit-test its **observable behavior** there; the
  real run in phase 3 is the integration proof.

When in doubt between `code` and `docs`, treat it as `code` and write the test.

**Callers found, dependencies justified.** Before the first edit to a
function's signature, return value or behavior, grep the changed symbol across
the tree: its callers, the tests that exercise it, fixtures, config, exports,
and string or dynamic references. A caller the change breaks is in scope and is
updated, its test, validation and error handling kept, and a fix lands once in
the shared code rather than once per caller; when the callers are many, weigh
the [needs-split](#phase-2-needs-split) tells instead. A caller the change
leaves working stays untouched, and only one showing a concrete problem of its
own is an adjacent find, taking one of the
[dispositions](#phase-2-adjacent-finds).
Prefer the standard library or what the repo already installs; a new dependency
names the requirement neither meets in the deviations log and in a bullet under
the PR body's `## Special things to note`.

**Tripwires are not a class.** Whatever the class, the profile's `Tripwires:`
and `In-PR requirement:` land in the same change, or a later phase goes red with
no phase explaining why.

## Phase 2: verify the spec's external-system claims before building on them

When the issue asserts a *causal mechanism* about something outside this
repo's code (a library behavior, a platform's response, an OS path rule),
treat it as a **hypothesis**, and confirm it against the real thing with the
cheapest probe *before* writing the fix around it; the profile's `Claims to
probe:` lines are this repo's examples. A probe that contradicts the brief is an
early `mis-specified` stop, not a phase-3 surprise.

## Phase 2: delegate execution, keep judgment

**Judgment** (the classification, the lane keys, spec interpretation,
external-claim probes, design choices, the brief) stays in the main thread on
the judgment tier. **Execution** (the failing test, then the green,
from a settled plan) is mechanical-tier work: delegate it to ONE subagent when
the plan is settled and the edit spans files you have not opened; where you can
point at the files, execute inline. Keep it inline when the issue is
exploratory, the spec is still settling, or the change touches schema or
architecture: a plan-implement loop across a subagent boundary loses too much.

Hand the subagent the worktree path, its `execute` scratch directory, the plan,
the test command and the conventions the edit needs. Require back a diff
summary, the tests added or updated, and a **structured deviations list**,
which lands verbatim in the merge summary. Three hard rules in its prompt:

- Every Edit/Write path carries the **worktree prefix**; the inherited guard
  refuses a write to the main checkout, so such a refusal is a wrong path to
  correct, not a form to work around.
- Each Bash call is **one plain command** run with the worktree as its cwd,
  per [context-discipline.md](context-discipline.md#once-isolated).
- It runs only the **targeted test nodes**; the phase-5 gate stays with the
  orchestrator.

## Phase 2: needs-split

Whether the change outgrows one PR, or needs a redesign the issue did not
scope, is judgment, and it often shows before the diff size does. Tells worth
weighing, as examples rather than thresholds:

- **The same workaround recurring**: another copy of one shim, guard or special
  case, which the design the issue assumed should have made unnecessary.
- **Escape-hatch types**: an `any`, a cast, an untyped map or a stringly-typed
  field added only so the change fits the code around it.
- **A wide blast radius**: one signature, file format or vocabulary value whose
  change must reach many call sites in lockstep (ten or so is a fair mark).

The split proposal is a set of tickets, each landing green on its own. Where
the change reshapes something callers reach, those tickets follow expand,
migrate, contract: **expand** adds the new shape beside the old one with no
caller switched; **migrate** moves the callers over in batches sized by the
blast radius, one ticket per batch; **contract** removes the old shape once
nothing reaches it. Give each ticket its title, its criteria and the ticket it
is blocked by.

## Phase 2: adjacent finds

A find outside the issue is folded, filed or noted, and phases 4 and 7 send
their own out-of-scope findings back here. A find showing the issue itself is
wrong is the `mis-specified` stop instead.

- **Fold** a find that fits what this PR is about and does not raise its
  release grade (a `fix` PR that would become a `feat` files it instead). Log
  the deviation and name the fold under the PR body's `## Special things to
  note`. Folded lines count toward the inline lane's 20-line cap.
- **File** a find worth an issue that fails the fold's test through a real
  `file-issue` call made inside the run, with the profile's triage marker, and
  leave it. A find is filed when the call's answer says `filed: true`; report
  `filed` or `refused` by quoting that answer. The mechanic
  answers `filed: false` with candidates when an open issue's title shares three
  or more tokens with yours, and each candidate is **read**: the same finding is
  linked in the deviations log rather than refiled, and `comment-issue` posts
  any evidence this run adds to it; a different one is refiled with
  `--distinct-from`. A find citing a path this PR already changes is refused
  with `in_diff`, since a find there folds unless it fails the fold's test;
  a find that does fail it is filed in that same call with
  `--outside-scope "<reason>"`.
- **Note** a find not worth a fold or an issue (a typo outside the diff, a
  cosmetic nit): record `Noted: <find>` in the Run file when it is met, so the
  merge summary can list it after a compaction. A filed find is recorded the
  same way, as `Filed: <issue link>: <find>`, from the `file-issue` answer's own
  link.

The merge summary lists every issue filed, every candidate linked and every
noted find.

## Phase 4: triage and depth checks

**Auto-triage** every finding: harden rather than rip out capability, verify
nits against the pinned versions, reject known non-issues, fix the valid ones,
and record a one-line disposition per finding. A decline whose reason asserts
how the code behaves ("the caller already guards it") is worth a probe before it
stands: `run-file probe <ref> --issue <issue> -- <command>...`, `<ref>` naming
the finding (no `: ` in it), runs the command at the checkout top and writes
what it printed as a `Probe:` line under `## Evidence`, and output that does not
bear the claim out turns the decline into a fix. Two rails on rejecting: an
assertion about **what exists in the repo** is checked against `origin/HEAD`
rather than the worktree, which may predate a merge; and a finding's **evidence
and its claim are separate**, so a reviewer citing the wrong commit for a real
primitive is still right.

Then read the diff yourself against the depth checks in the coding-standards
file the Standards axis reads, and the sub-files it routes to, by their leading
words: a vocabulary the change extends, a rule-shaped prose change, new
pattern-matching code, a new test run with its fix reverted, removed lines with
no new home, a fix landed after review, a diff that lowers the bar,
a contract the change alters, a dependency the change adds, and any the repo
adds beside them. How deep each goes is judgment. Two aids: `dropped-lines`
lists removed blocks with no matching added line, and a new pattern matcher is
worth cases that must be refused (a near-miss token, quoted, indented,
unbalanced and unreadable input).

## Phase 4: the self-review loop

Refactoring belongs to review, not to the red-to-green loop, so the
self-review repeats while it still finds work. The bound is preflight's
`self_review_cap`, the profile's `Self-review cap:` line as preflight reads it,
3 where the line is absent; after a compaction, `preflight none` answers it
again, `none` skipping the issue's own checks. Every lane runs the same loop.

- **Round 1** is the phase's first dispatch: both axes, Standards and Spec,
  over the whole diff, the `writing-for-agents` pass where it fired, and the
  depth checks above. Every fix any of them leads to is round 1's. The Spec
  axis, the pass and the depth checks run in round 1 only.
- **Round 2 and up** is one `code-review` subagent, Standards axis only, over
  the previous round's **fix-only diff**: write the head into the Run file
  before triaging round `<n>`, where a compaction cannot lose it, commit its
  fixes, and hand the next round `git diff <head>..HEAD`. It
  is dispatched with round 1's Standards prompt, only its diff and Report file
  changing, and triaged as round 1 is: it waits for the Report file, and a file
  that never arrives once retries stop helping is
  `red-after-retry: standards`. Its Report file is
  `<scratch>/standards/standards-report-<n>.md`, `<n>` the round number, so no
  round overwrites another's.
- **The loop stops** at the first round whose fix-only diff is empty (no
  finding fixed: each declined, filed or linked, or none raised), or at the
  round the cap allows, its findings dispositioned in full. A cap of 1 is the
  single round, which a clean round 1 also ends at.

Each round leaves `Round: self-review <n>: <text>`, its outcome in a line, and
the loop leaves `Stop: self-review: <tree unchanged|cap at <sha>>`: `tree
unchanged` when the last round changed no file, even at the cap, and `cap at
<sha>` when the rounds reached it with the last fixes unread, `<sha>` the HEAD
right after committing the last round's fixes. That holds `run-file gate clean`
until a host reviewer read them: one stopped `tree unchanged` at a head
covering the final head, or any whose last stop is stamped at that commit or a
descendant of it.

## Consult current docs

While implementing or triaging findings (phases 2, 4 and 7), verify API claims
against **current** docs through the `find-docs` skill and the extra `Sources:`
the profile names. For every library on the profile's `Pinned:` line, read the
installed version from the repo's manifest and confirm the claim against that
version before acting on it; a remembered API the installed version lacks is a
regression.
