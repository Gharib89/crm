# Phase 9: the merge gate

## Contents

- [The summary](#the-summary)
- [A tracker issue on Targets:](#a-tracker-issue-on-targets)
- [Attended: post, then wait](#attended-post-then-wait)
- [Unattended: post to the PR, then return](#unattended-post-to-the-pr-then-return)

The one guaranteed human stop (`## The stops` in SKILL.md). Your job is
to make the call a ten-second yes or no by laying out everything the human
would want to check.

**Write it uncompressed.** A session-wide output style or personal brevity rule
does **not** apply to this summary. It is the evidence a human approves an
irreversible squash-merge on, and in the unattended lane it is the only record
of the run. Keep organization identifiers, credentials and live-system names out
of it; the repo may be public.

## The summary

```
## /ship summary: #<issue>: <title>   (ship <version>)

PR:        <url>  (<branch> → <default branch>)
Issue:     <one-line restatement of what was asked>
Lane:      <full | small: skipped <phase 3 verifications, docs-sync>>

Implementation
  - <what was built, 1 to 3 lines>
  - tests added/updated: <files / count>
  - tripwires: <none fired | <what was rebuilt or bumped>>

Deviations from plan
  - <departure: what and why, conservative option taken>   (or: None, plan held)

Verification                                   (one row per applicable entry)
  - <name>: <pass | fail | deferred-to-ci: <CI leg> | unavailable | unexercised>   <what ran>
  (or: none applicable: <reason>)

Self-review (code-review skill, the review gate)
  - <axis|pass>: <n> findings  ·  report: <path>   (one row per phase-4 report)
  - <finding> → <fixed | rejected: reason>
  ...

Review                                         (one block per reviewer)
  <name> (<trigger>, <n> rounds): <converged | converged, override needed | degraded: <reason> | not invoked: <primary> converged>[, <N> denied calls (run <url>[, run <url>…])]
    (the denied-calls clause as review-loop.md's Review line carries it, only where N > 0)
    - <finding> → <fixed in <sha> | declined: reason | filed: #<n>>
    ...                                        (or: clean, no findings)
    (a fallback that ran opens with: fallback for <primary>: degraded: <reason>)

Local gate:  <derived from the gate's JSON: <gate> <✓ | ✗ | deferred-to-ci | unavailable> · ...>
Docs-sync:   <ran: files | skipped: reason>
Tracker:     <none | one block per drafted section:>
  #<n> `## <section>`:
  <the drafted section, verbatim>
  (unattended: the command a human runs after merging, per the tracker section)
CI:          <leg> → <green | state> · ...     (from the profile's Legs:)
Issues filed: <#n <title>, ... | none>  ·  linked: <#n <title>, ... | none>
Ship defects: <none | one line per defect:>
  - <missing operation or wrong prose> (phase <n>)
Timing:      start→PR <m>m · PR→gate <m>m · per phase: 0 <m> · 1 <m> · 2 <m> · 3 <m> · 4 <m> · 5 <m> · 6 <m> · 7 <m> · 8 <m>
             (from `run-file timing`: its `row` is this line verbatim, and a
             field the mechanic could not compute reads `unverified` in place
             of a number.)

Ready to merge. Reply "merge" to squash-merge, close the issue, and clean up.
```

Every row is grounded in a result from this run: `Local gate:` is the gate's
`gates` object verbatim, `CI:` is `ci-wait`'s output, `Issues filed` is the set
of `file-issue` return values from this run in every lane, the numbers it filed
on one side and the candidates it answered with instead on the other (not a
recalled count; an implausible count is the human's signal), and `Verification`
and test counts are read from the phase-3 and phase-2 results, not recalled. The
empty case writes the same reason phase 6 wrote into the PR body's `##
Verification` section, one of the three [pr-body.md](pr-body.md) names. On an
`unexercised` row, `<what ran>` names the **subject that did not exist** rather
than a command; the row is a record for the human to weigh, and it stays a
record: a degraded exit, a hand-back and a `Ship defects:` row are all something
else. A value you cannot point to a tool result for is written as `unverified`.
`Ship defects:` lists every Ship defect the run met (a host operation no
mechanic performs, prose that promised what a mechanic does not do), each with
the phase it was met in and written to the Run file at that moment the way a
deviation is, so the row is a record, not a recollection. The run files it to no
other repo; the human carries the row upstream.

**This summary is the record; the PR body is the short form of it.** Four
sections of the body have a counterpart block here ([pr-body.md](pr-body.md)),
and each pair is written from one result so the two cannot disagree:

- `Deviations from plan` is the phase-2 log **verbatim**; the body's `## Special
  things to note` carries only the subset that changes how the reviewer reads
  the diff, folded by the claim they share.
- `Issues filed` and `Ship defects:` are the full set; the body's `## Needs
  attention` is the same set, one line each.
- Each `Review` block is one reviewer's per-finding outcomes; the body's `##
  Review` line for that reviewer is the fixed counts line
  [review-loop.md](review-loop.md) fixes, and it points here for the detail.
- Each row of the `Verification` block and its line in the body's `##
  Verification` section are the same phase-3 result in the same format. This
  block is where the human reads them at the gate; the section is where they
  outlive the run.

The `Self-review` block is read from the phase-4 Report files
[context-discipline.md](context-discipline.md) has each subagent write, one row
per report naming the file its findings came from: both `code-review` axes, and
the `writing-for-agents` pass where it fired. A row whose file is no longer on disk
takes the `unverified` rule above rather than the transcript.

**Every count in this summary and in the PR body that measures the tree is a
measurement.** Write the number with the command that produced it beside it,
run on the PR head, and count a word with `grep -ow` so a longer word carrying
it as a substring does not inflate the total. A **tally of the run's own work**
is the exception, and the only one: no command reproduces a round count, a
finding outcome or a past round's run URL, so each is **written to the Run file
at the moment it happens**, the way a deviation and a Ship defect already are.
That is one rule over three records, each written by the phase that produces
it: a `file-issue` result, filed number or linked candidate, when the call
answers; a Ship defect when it is met; and a reviewer's round, with one line
per finding and its disposition, and one carrying its numeric
`reviewer_run.denied` and run URL where `poll-pr` returned one, when that round
is dispositioned, which
[review-loop.md](review-loop.md) carries at the bullet that does it. This
paragraph is the **read-back** rule: each is read from the file here rather
than recalled, which is what makes them survive a compaction. The PR body's fixed
`## Review` line is entirely such a tally, which is why it carries no
commands.

**A wrong title is fixed before the merge, not after.** The merge freezes the
PR title as the squash subject, so a subject that no longer matches what the
run built is corrected with `update-pr-title <pr> --title "<subject>"` before
the summary is posted, so the human reads the title that will land.

## A tracker issue on Targets:

A `Targets:` entry naming an issue by number (`#<n>`, such as `map issue #1`) is
met in phase 4 by a **drafted section**, not a file edit: the new content of
each `## ` section the change affects, one draft per section, written to
`<scratchpad>/ship-<issue>/tracker-<n>-<k>.md` beside the Run file (`<n>` the
tracker issue, `<k>` the draft's ordinal) and named with its heading in the Run
file's `## Design and plan`. Beside each draft, save the section as `read-issue`
returned it, as `tracker-<n>-<k>.base.md`. On Azure DevOps that body is the
description's HTML: for the `<pre>` block ship writes, the markdown is the text
inside it, entities decoded. The summary's `Tracker:` block shows each draft
verbatim, so the human approves the text with the merge. The unit is the whole
section, because `update-issue-body` replaces nothing smaller.

A drafted section is written after the merge and never before, so no issue
records code that has not landed: once `merge` answers `merged: true`, and
before `cleanup`, run `update-issue-body <n> --section "<section>" --body-file
<draft>` per draft, `<section>` taken verbatim from the heading `read-issue`
returned. Re-read the issue first: a section that no longer matches its base is
redrafted, posted, and written on the human's explicit "yes". `created: true`
for a section the draft meant to replace means the name missed: re-run with the
heading `read-issue` returned, and name the stray section as a Ship defect. Exit
1 (an Azure DevOps description ship did not write, a refused write) means
nothing was written: a Ship defect for the summary, with the draft attached.

The unattended lane runs no merge, so ship writes no section: under each draft
the summary gives the command a human runs after merging, from a file they save
the draft to, `.claude/skills/ship/scripts/update-issue-body.sh <n> --section
"<section>" --body-file <file>`, once they have checked the section has not
changed since the draft.

## Attended: post, then wait

Post the summary in the conversation and **wait**. Merge only on an explicit
"merge", and the word is exact: a typo, a synonym, or approval of some other
part of the summary is asked back rather than read as the word, because merging
is the step no later phase undoes. The word is the whole trigger, so never an
auto-merge flag either: it can merge the instant CI is green, before a reviewer
lands.

**On approval**, from the worktree, `merge <pr> <issue|none> [--worktree
<path>]`. It reads the PR first and refuses `pr-closed: <state>` for one that is
neither open nor already merged, merging nothing. Then it proves the branch has
seen every commit on its base and refuses `stale-base: behind <n> on <base>` if
not, merging nothing either; rebase, re-run the local gate, and come back to
this gate. [merge](../scripts/merge.sh) carries what each refusal is protecting
against. Then it squash-merges with the PR title as the squash subject, re-reads
the PR to confirm the merge took, confirms the issue closed and closes it
explicitly if the link did not fire, deletes the remote branch and proves the
deletion, fast-forwards the local base branch from the checkout that holds it
(reporting a diverged local base and leaving it alone, retrying a transient
`index.lock` from a concurrent status and leaving it in place), and **releases
the claim and strips `ready-for-agent`**, so a reopened issue goes back through
triage instead of being refused forever. The two issue steps are the
issue-backed run's: `merge <pr> none` has no issue to close and no claim to
release, so it skips both and its JSON carries none of `issue_closed`,
`claim_released` and `ready_for_agent_removed`. Then each drafted tracker
section, per the section above. Then `cleanup <issue|none>`:
removes the worktree and force-deletes the local branch (a squash-merged branch
is not an ancestor of the default branch). Carried files stay in the worktree it
removes. Any `false` in `merge`'s or `cleanup`'s JSON, or a nonzero exit: re-run
the mechanic that owns the step before reporting done, and a step no mechanic
re-does is a Ship defect for the summary. Done is `merge`, every
`update-issue-body` (none where the run drafted none) and `cleanup` exited 0.

**If the human says no or wants changes**, treat the note as the next round of
work: apply it on the same branch, re-run the local gate, come back to this
gate. Do not re-open the whole pipeline.

## Unattended: post to the PR, then return

`comment-pr <pr> --body-file` with the summary. Then **return** with the PR
link. Do not wait, poll, or merge; the claim stays on the issue, which carries
the open PR, so later fires skip it until a human merges. The line
"Ready to merge. Reply ..." becomes "Ready to merge: a human merges from the
PR." A drafted tracker section is not written: the summary carries the command
for it ([A tracker issue on Targets:](#a-tracker-issue-on-targets)). Detail in
[unattended.md](unattended.md).
