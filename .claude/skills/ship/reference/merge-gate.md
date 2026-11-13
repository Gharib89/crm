# Phase 9: the merge gate

## Contents

- [The summary](#the-summary)
- [Unmet criteria](#unmet-criteria)
- [Attended: post, then wait](#attended-post-then-wait)

The human stop by default; an attended profile can opt into merging on a
clean gate. Lay out everything the human would want to check in either case.

**Write it uncompressed.** A session-wide output style or personal brevity rule
does **not** apply to this summary. It is the evidence a human approves an
irreversible squash-merge on, and it carries that evidence itself: in a cloud
fire, the Run file lives in the sandbox clone and does not outlive it.
Keep organization identifiers, credentials and live-system names out of it; the
repo may be public.

## The summary

Lay it out for the case in front of you: no layout is fixed. Head it with the
issue number, its title and `ship <version>`. What it carries is fixed, because
the human approves an irreversible merge on it; the names below are what other
references call each item, not a layout:

- **PR and lane**: the PR URL with its branch and base, what was asked in a
  line, and the lane, with any revocation and why.
- **Implementation**: what was built, the tests added or changed, and any
  tripwire that fired.
- **Deviations from plan**: the phase-2 deviations log verbatim, or that the
  plan held.
- **Unmet criteria**: each criterion not met, from `gate summary`'s
  `unmet_criteria_line` (a run with no `## Criteria` lists them from the issue),
  with its reason (the action a human must take, for a host action), or
  `no line` where the run recorded none ([Unmet criteria](#unmet-criteria)).
- **Verification**: each applicable verification's phase-3 result and what
  ran, or the reason the PR body gives for none applying.
- **Self-review**: its rounds and stop, each phase-4 report with its path, and
  each finding with its disposition.
- **Review**: each reviewer's trigger, exit and rounds, any calls it was denied
  with each run's link, each finding with its disposition (fixed in a commit,
  declined with the reason, filed), the reason a fallback ran, and any
  `Gating: yes` reviewer's declined finding as an override the human decides
  on, with its evidence.
- **Local gate**: each gate's status, at the head the verdict covers
  ([below](#attended-post-then-wait)).
- **Docs-sync**: the files it changed, or why it was skipped.
- **Tracker**: each drafted section verbatim, per
  [tracker-targets.md](tracker-targets.md).
- **CI**: each `Legs:` leg's state, any red check outside the legs and any
  check missing from `Legs:`.
- **Issues filed**, each by number, title and link, **linked**, each by number
  and title, and each **noted** adjacent find.
- **Ship defects**, each with the phase it was met in and its draft's title
  and path (or its number, filed here), **Ship notes** and **Direct calls**,
  below.
- **Timing**: `run-file timing`'s `row`, verbatim.
- **Host protections**: `protections <pr>`'s `line`, verbatim: what the host
  itself enforces on the base branch (protection, required checks and reviews,
  admin bypass), read-only. Its reader is the human who answers the merge word:
  no script reads it, so `unknown` or `no`
  adds no stop and no prompt, and a repo that opts into unattended merge has
  already chosen its policy (ADR 0011).
- **Clean gate held by**: `gate summary`'s `clean_gate_held_by_line`, `none`
  on a clean gate.

It ends with the reply the human gives: "merge" to squash-merge, close the
issue and clean up; with an unmet criterion, "merge" with an explicit waiver of
each one; with a Ship defect draft, "file defects" to file the drafts at
Gharib89/skills.

On a clean gate the summary ends with exactly the line below in place of the
reply lines, written once `merge` answers `merged: true`:

```
Merged on a clean gate: <PR url>
```

A posted message cannot be edited, so the run posts the summary with no reply
line, runs the merge sequence, then posts that line
last. In an opted-in run the reply lines
appear only on `clean: false` or when `merge` refuses, the refusal named under
them. A step after the merge that fails (a tracker write, cleanup) is reported
beneath the merged line; report a completed merge only after the mechanic
confirms it.

**Every item is grounded in a result from this run**: `Local gate` is the gate's
`gates` object from the verdict the Run file recorded, `CI` is `ci-wait`'s
output, `Issues filed` is this run's `file-issue` return values (filed numbers
on one side, the candidates it answered with instead on the other), and
`Verification` and the tests are the phase-3 and phase-2 results. On an
`unexercised` result, what ran names the **subject that did not exist** rather
than a command. A value you cannot point to a tool result for is written
`unverified`: a find the run reports filed or refused with no `file-issue`
answer behind it is one. A **tally of the run's own work** has no tool to
re-run, so it is **written to the Run file at the moment it happens** and read
back from there, which is what survives a compaction: a `file-issue` result
when the call answers, a Ship defect, Ship note or `Noted:` find when it is
met, and a reviewer's round when it is dispositioned
([review-loop.md](review-loop.md)).
`Ship defects` lists every gap in Ship that broke or misled the run: a gating
call no mechanic performs, or a mechanic or prose whose wrong answer the run
acted on. Friction the run worked around by judgment (a rule that did not fit
the case, prose that left it guessing) goes under `Ship notes` instead, one line
each and no draft, and so does a direct call made in two runs where this run can
see the other, per [mechanics.md](mechanics.md#which-calls-need-a-mechanic);
record `Ship-note: <what>` in the Run file when it is met. Every direct call
goes under `Direct calls`, read from the Run file's `## Direct calls`. Each
defect carries a **drafted issue** for the source repo, `Gharib89/skills`,
written to `defect-<k>.md` beside the Run file when it is met: a title, then a
body naming Ship's version, the mechanic or prose at fault and what the run did
instead, and no organization identifier, credential or client context, because
the source repo is public. A gap in the ship profile rather than in Ship is a
**profile defect**: an adjacent find of this repo, filed here through phase 2's
dispositions and listed under `Issues filed`, not under `Ship defects`. Where
this repo is the source repo, a Ship defect is an adjacent find already (its
profile's `## Triage`) and the item names its number instead of a draft. In
either case, record `Ship-defect: <detail>` in the Run file when the defect is
met, even if it is fixed or filed during the run, so the item survives a
compaction; neither that line nor a draft holds the clean gate, since a defect
that breaks a gating step already fails that step's evidence.

**The body is the short form of this record** ([pr-body.md](pr-body.md)), each
pair written from one result so the two cannot disagree: `Deviations from plan`
is the phase-2 log verbatim, of which `## Special things to note` carries the
folded subset; `Issues filed`, `Ship defects` and `Ship notes` are the full set
that `## Needs attention` lists one line each; each reviewer's `Review` detail
is the per-finding record behind its `## Review` line; and each `Verification`
result is the same phase-3 result as its line in `## Verification`. The
`Self-review` rounds and stop are read from the Run file's `Round: self-review`
and `Stop: self-review:` lines, and its reports from the phase-4 Report files; a
report whose file is no longer on disk is `unverified`. A Ship defect or Ship
note met after phase 7's body rewrite is written into `## Needs attention` with
`update-pr-body <pr> --section "Needs attention" --body-file <file>`, then
`read-pr`, before the summary, so that pair still agrees.

**A wrong title is fixed before the merge**, with `update-pr-title`, before the
summary is posted: the merge freezes the PR title as the squash subject, so the
human should read the title that will land.

## Unmet criteria

An **unmet criterion** is any issue criterion the run did not meet: one
asking for a host action no mechanic performs, which stays verbatim in the issue
([spec-gaps.md](spec-gaps.md#a-criterion-no-mechanic-can-perform)), or
one the run could not prove. Each carries `Criterion <n>: not met: <reason>` in
the Run file ([implement.md](implement.md#phase-1-criteria-the-run-owes)), or
is owed that line and has none, and the `Unmet criteria` item lists it verbatim
with its reason, which is the action a human must take where it is a host
action; the gap is also in the deviations log. The item is the human's to
clear: **merging needs an explicit waiver of each unmet criterion, beside the
merge word**: "merge" alone, or a waiver naming fewer than all of them, is asked
back. `gate clean` reads the same lines, holding on each criterion not met or
with no line, so `Merge: on-clean-gate` posts the summary with the reply line
and waits. A run whose Run file has no `## Criteria` section (a free-text run,
an `init` given no `--criteria`, or criteria text yielding none) gives
`gate clean` only its `Criterion <n>:` lines to read: with an unmet criterion
listed, wait the same way, whatever it answers.

## Attended: post, then wait

Read the optional `Merge:` line under the profile's `## PR`. An absent line
or `Merge: Default.` means post the summary in the conversation and **wait**
for the exact word "merge"; a near miss is asked back. `Merge: on-clean-gate`
means evaluate the clean gate below, which holds on an unmet criterion; the
human's waiver beside "merge" clears it
([Unmet criteria](#unmet-criteria)). Any other value holds for the human as a
profile error. An inline-lane run reads it as `on-clean-gate` for its own PR.
The unattended branch never evaluates this option.

**Every attended run, opted in or not, takes the summary's `Unmet criteria` and
`Clean gate held by` lines from `run-file gate summary <pr> --issue <issue>`**
(`--file <run.md>` for a record addressed by path), never from its own account
of the run. Save the final settled `ci-wait` answer (not `pending`) to
`ci-wait.json` beside the Run file after phase 8; the verb reads that file and
the head `read-pr <pr>` returns, takes no ci-wait or head from the caller, and
exits 2 naming the step to run where either is absent. It answers `gate
clean`'s `{clean, held_by}` for that head (exit 0 clean, 1 held) plus
`unmet_criteria_line` and `clean_gate_held_by_line`, the two lines to paste.
The unattended lane keeps its own path and does not use it.

For an opted-in attended run that answer is the decision; `run-file gate clean
<ci-file> --head <head_sha> --issue <issue>` answers the same for a ci-wait file
saved elsewhere or a head already in hand. The mechanic writes nothing, and
`run-file --help` lists what holds the gate.

On `clean: true`, post the summary with no reply line, then run the merge
sequence below without waiting for a reply. On `clean: false`, post it with
`Clean gate held by: <clean_gate_held_by_line>` and the usual reply line, then
wait for "merge". Exit 2 is an unreadable decision: name the error and wait.
An auto-merge flag is never used: authorization is the recorded
gate, after reviewers have finished.

**The gate's verdict is cited, not re-run, while it still describes the PR.**
Phase 5 records each verdict with `run-file gate record` against the head it ran
on; here, `run-file gate read --head <head_sha>`, the head `read-pr` returns,
answers `current`. On `current: true`, cite the recorded verdict: the
`Local gate` item reads the `gates` object that answer returns, not a re-run. On
`current: false`, re-run the gate from the worktree and record it again, because
a commit the gate never saw is in the PR: this is the final head's gate, the
one phase 7's round fixes wait for. Where the gate cannot run here, the item
names the head the verdict ran on and how many commits behind the PR head it
is, from that answer's `behind`, or that the distance is unknown when `behind`
is `null`. `non_leg_failing` on the `CI` item is red the profile does not ask
for, so it does not hold the merge, and an `unlisted` check is profile drift
for the human to add to `Legs:` or remove.

**On explicit approval or a clean opted-in gate**, first record what admits the
merge: `run-file gate admit clean <ci-file> --head <head> --issue <issue|slug>`
on a clean gate (`<ci-file>`: the saved `ci-wait.json`'s full path; it records
only where `gate clean` is clean), or `run-file gate admit human --head <head>
--issue <issue|slug>` on the human's word "merge" over a held gate; it covers
unmet criteria, not a red leg (`checks-unsettled`). `<slug>` is `--run`'s. Any
`update-pr-body` or `update-pr-title` after the settled `ci-wait` re-fires
every workflow that triggers on `edited` against the same head, so re-run
`ci-wait <pr> --sha <head>` and save its answer before the admit step. Then
`merge <pr> <issue|none> <head> [--worktree <path>] [--run <slug>]`, `<head>`
the `head_sha` this gate read from `read-pr`, so the merge lands only the head
the summary describes; a `none` run names its Run file's slug with `--run`. Its
header carries what it does and what each refusal protects against:
`pr-closed: <state>`, `head-moved: <current> != <gated>`, `stale-base: behind
<n> on <base>`, `cloud-sandbox`, `no-gate-record` and `checks-unsettled` merge
nothing and stop at this gate, including after a clean-gate decision; on one,
read [merge-gate-branches.md](merge-gate-branches.md#merge-refusals) for how it
comes back here.

Then each drafted tracker section ([tracker-targets.md](tracker-targets.md)). A
Ship defect draft not yet filed goes into the closing message the way the
[unattended lane](unattended.md#the-merge-gate-post-to-the-pr-then-return)
carries it, because `cleanup` deletes the directory the drafts live in; nothing
waits on it. Then `run-file close 9` and set the task to the returned `mirror`,
then `ExitWorktree` with `action: "keep"` (`cleanup` owns the removal), and last
`cleanup <issue|none>`.

**If the human says no or wants changes**, or says "file defects", read
[merge-gate-branches.md](merge-gate-branches.md) before acting on it.
