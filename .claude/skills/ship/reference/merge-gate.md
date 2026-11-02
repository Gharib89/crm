# Phase 9: the merge gate

## Contents

- [The summary](#the-summary)
- [Unmet criteria](#unmet-criteria)
- [A tracker issue on Targets:](#a-tracker-issue-on-targets)
- [Attended: post, then wait](#attended-post-then-wait)
- [Filing a Ship defect](#filing-a-ship-defect)
- [Unattended: post to the PR, then return](#unattended-post-to-the-pr-then-return)

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
  [A tracker issue on Targets:](#a-tracker-issue-on-targets).
- **CI**: each `Legs:` leg's state, any red check outside the legs and any
  check missing from `Legs:`.
- **Issues filed**, each by number, title and link, **linked**, each by number
  and title, and each **noted** adjacent find.
- **Ship defects**, each with the phase it was met in and its draft's title
  and path (or its number, filed here), **Ship notes** and **Direct calls**,
  below.
- **Timing**: `run-file timing`'s `row`, verbatim.
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
as the last line of the closing message. In an opted-in run the reply lines
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
([implement.md](implement.md#phase-1-a-criterion-no-mechanic-can-perform)), or
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

## A tracker issue on Targets:

A `Targets:` entry naming an issue by number (`#<n>`, such as `map issue #1`) is
met in phase 4 by a **drafted section**, not a file edit: the new content of
each `## ` section the change affects, one draft per section, written to
`tracker-<n>-<k>.md` beside the Run file (`<n>` the
tracker issue, `<k>` the draft's ordinal) and named with its heading in the Run
file's `## Design and plan`. Beside each draft, save the section as `read-issue`
returned it, as `tracker-<n>-<k>.base.md`. On Azure DevOps that body is the
description's HTML: for the `<pre>` block ship writes, the markdown is the text
inside it, entities decoded. The summary's `Tracker` item shows each draft
verbatim, so the human approves the text with the merge. The unit is the whole
section, because `update-issue-body` replaces nothing smaller.

A drafted section is written after the merge and never before, so no issue
records code that has not landed: once `merge` answers `merged: true`, and
before `cleanup`, run `update-issue-body <n> --section "<section>" --body-file
<draft>` per tracker draft, `<section>` verbatim from the heading `read-issue`
returned. Re-read the issue first: a section that no longer matches its base is
redrafted, posted, and written on the human's explicit "yes". `created: true`
for a section the draft meant to replace means the name missed: re-run with the
returned heading, and name the stray section as a Ship defect. Exit 1 means
nothing was written: a Ship defect for the summary, with the tracker draft
attached.

The unattended lane runs no merge, so under each draft the summary gives the
command a human runs after merging, from a file they save the draft to,
`.claude/skills/ship/scripts/update-issue-body.sh <n> --section "<section>"
--body-file <file>`, once they have checked the section has not changed.

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
`run-file --help` lists what holds the gate. Any `tracker-*.md` draft always
holds it, and a `defect-*.md` draft never does (`*.base.md` files are saved
originals, not drafts).

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

**On explicit approval or a clean opted-in gate**, from the worktree, `merge
<pr> <issue|none> <head> [--worktree <path>]`, `<head>` the `head_sha` this
gate read from `read-pr`, so the merge lands only the head the summary
describes. Its header carries what it does and
what each refusal protects against: `pr-closed: <state>`, `head-moved: <current>
!= <gated>` and `stale-base: behind <n> on <base>` merge nothing and stop at
this gate, including after a clean-gate decision. Two of them come back here:

- `head-moved`: the earlier approval covered the old head only. Re-run
  `read-pr`, read the new commits, cite or re-run the gate for the new
  `head_sha` per **The gate's verdict is cited**, wait for `ci-wait` on it, save
  its settled answer to `ci-wait.json`, re-run `run-file gate summary <pr>`,
  rewrite the summary from that answer and ask again, or decide on it when
  opted in.
- `stale-base`: merge the base in, re-run the local gate, save the new head's
  settled `ci-wait` answer and come back to this gate for `gate summary`. A
  merge with no conflict to resolve leaves the reviewer stops and Verification
  passes covering the new head; one that resolved a conflict breaks that
  coverage.

Then each drafted tracker section. A Ship defect draft not yet filed goes into
the closing message the way the [unattended
lane](#unattended-post-to-the-pr-then-return) carries it, because `cleanup`
deletes the directory the drafts live in; nothing waits on it. Then `run-file
close 9` and set the task to the returned `mirror`, and last `cleanup
<issue|none>`.

**If the human says no or wants changes**, treat the note as the next round of
work: apply it on the same branch, re-run the local gate, save the new head's
settled `ci-wait` answer, come back to this gate and re-run `gate summary`. Do
not re-open the whole pipeline. Under `Merge: on-clean-gate` the
change breaks coverage, so `gate clean` holds, naming each piece of evidence
with both heads: every reviewer stop, until an on-push reviewer's own round on
the new head records a new stop
([review-loop.md](review-loop.md#the-round)), and every Verification pass whose
`Paths:` the change touched, or that has none or one matching no file, until
its re-run per
[verify.md](verify.md)'s **Head** records it again. Request no new review from
here; while a stop holds, the run waits for "merge".

## Filing a Ship defect

A Ship defect draft reaches the source repo on the human's word alone, and
"merge" is not that word: it approves the PR, not publishing the run's context
to a public repo. On "file defects", or a word naming one draft, run `file-issue
--repo Gharib89/skills --title "<title>" --body-file <draft> --label
needs-triage` per Ship defect draft and put its answer under `Ship defects`: the
number filed, the candidates it answered with instead, each read the way phase 2
reads one, or, on exit 1 with a `command`, that command verbatim for the human
to run where the write succeeds. Before or after the merge, either order holds.

## Unattended: post to the PR, then return

First the final head's gate, as the attended lane runs it: `run-file gate read
--head <head_sha>`, and on `current: false` the re-run and record
[above](#attended-post-then-wait), so the `Local gate` item describes the head a
human merges. A `fail` there takes phase 5's fix loop; `unavailable` hands back
`local gate unavailable: <gates>` with the PR left open. Then `comment-pr <pr>
--body-file` with the summary, `run-file close 9` with the task set to the
returned `mirror`, then **return** with the PR link. Do not wait, poll, or
merge; the claim stays on the issue, which carries the open PR, so later fires
skip it until a human merges. The last line becomes "Ready to merge: a human
merges from the PR.", and with an unmet criterion it adds "Waive each unmet
criterion, or take its action, before merging." A Ship defect's draft is never
filed from here, and the comment drops the "file defects" line: it carries each
draft verbatim under the command a human runs from a file they save it to,
`.claude/skills/ship/scripts/file-issue.sh --repo Gharib89/skills --title
"<title>" --body-file <file> --label needs-triage`.
