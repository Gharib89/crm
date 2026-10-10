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

Unmet criteria
  - <criterion, verbatim from the issue>: <the host action a human must take>
  (or: None)

Verification                                   (one row per applicable entry)
  - <name>: <pass | fail | deferred-to-ci: <CI leg> | unavailable | unexercised>   <what ran>
  (or: none applicable: <reason>)

Self-review (code-review skill, the review gate)
  - <axis|pass>: <n> findings  ·  report: <path>   (one row per phase-4 report)
  - <finding> → <fixed | rejected: reason>
  ...

Review                                         (one block per reviewer)
  <name> (<trigger>, <n> rounds): <reviewed | not reviewed: <reason> | not invoked: <primary> reviewed>[, <N> denied calls (run <url>[, run <url>…])]
    (the denied-calls clause as review-loop.md's Review line carries it, only where N > 0)
    - <finding> → <fixed in <sha> | declined: reason | filed: #<n>>
    ...                                        (or: clean, no findings)
    (a fallback that ran opens with: fallback for <primary>: <not reviewed: <reason> | capped with findings>)
    (a Gating: yes reviewer's declined finding: override needed: <finding>, <evidence>)

Local gate:  <derived from the gate's JSON: <gate> <✓ | ✗ | deferred-to-ci | unavailable> · ...>[ · at <sha>, <n> commits behind | at <sha>, behind unknown]
Docs-sync:   <ran: files | skipped: reason>
Tracker:     <none | one block per drafted section:>
  #<n> `## <section>`:
  <the drafted section, verbatim>
  (unattended: the command a human runs after merging, per the tracker section)
CI:          <leg> → <green | state> · ...     (from the profile's Legs:)
  [non-leg red: <check>, ...]                  (ci-wait's non_leg_failing, none of them a leg)
  [profile drift: <check>, ... not on Legs:]   (ci-wait's unlisted)
Issues filed: <#n <title>, ... | none>  ·  linked: <#n <title>, ... | none>
Ship defects: <none | one block per defect:>
  - <missing write, missing gating read, or wrong prose> (phase <n>)
    draft for Gharib89/skills: <title> · <draft path>
Direct reads: <none | <call> · <why>, ...>     (from the Run file's ## Direct reads)
Timing:      <`run-file timing`'s `row`, verbatim>
[Clean gate held by: <each held_by reason>]     (opted in, clean: false)

Ready to merge. Reply "merge" to squash-merge, close the issue, and clean up.
(with an unmet criterion: reply "merge" with an explicit waiver of each one.)
(with a Ship defect draft: Reply "file defects" to file the drafts at Gharib89/skills.)
```

On a clean gate the summary ends with exactly the line below in place of the
two reply lines above, written once `merge` answers `merged: true`:

```
Merged on a clean gate: <PR url>
```

A posted message cannot be edited, so the run posts the summary through the
`Timing:` row with no reply line, runs the merge sequence, then posts that line
as the last line of the closing message. In an opted-in run the reply lines
appear only on `clean: false` or when `merge` refuses, the refusal named under
them. A step after the merge that fails (a tracker write, cleanup) is reported
beneath the merged line; report a completed merge only after the mechanic
confirms it.

**Every row is grounded in a result from this run**: `Local gate:` is the
gate's `gates` object from the verdict the Run file recorded, `CI:` is
`ci-wait`'s output, `Issues filed` is this
run's `file-issue` return values (filed numbers on one side, the candidates it
answered with instead on the other), and `Verification` and the test counts are
the phase-3 and phase-2 results. The empty `Verification` case writes the
reason phase 6 wrote into the PR body, one of the three
[pr-body.md](pr-body.md) names. On an `unexercised` row, `<what ran>` names the
**subject that did not exist** rather than a command. A value you cannot point
to a tool result for is written `unverified`. `Ship defects:` lists every host
write or gating read no mechanic performs, and prose that promised what a
mechanic does not do; an informational read made directly goes on `Direct
reads:` instead. Each defect carries a **drafted issue** for the source repo,
`Gharib89/skills`, written to `defect-<k>.md` beside the Run file when it
is met: a title, then a body naming Ship's version, the mechanic or prose at
fault and what the run did instead, and no organization identifier, credential
or client context, because the source repo is public. A gap in the ship profile
rather than in Ship is a **profile defect**: an adjacent find of this repo,
filed here through phase 2's dispositions and listed under `Issues filed`, not
on this row. Where this repo is the source repo, a Ship defect is an adjacent
find already (its profile's `## Triage`) and the row names its number instead of
a draft. In either case, record `Ship-defect: <detail>` in the Run file when
the defect is met, even if it is fixed or filed during the run: a clean gate
requires that no Ship defect was met.

**Counts are measurements; tallies are records.** A count that measures the
tree, here or in the PR body, carries the command that produced it, run on the
PR head, with a word counted by `grep -ow` so a longer word carrying it does not
inflate the total. A **tally of the run's own work** has no command to
reproduce it, so it is **written to the Run file at the moment it happens** and
read back from there, which is what survives a compaction: a `file-issue`
result when the call answers, a Ship defect when it is met, and a reviewer's
round when it is dispositioned ([review-loop.md](review-loop.md)). The PR body's
`## Review` line is entirely such a tally.

**The body is the short form of this record** ([pr-body.md](pr-body.md)), each
pair written from one result so the two cannot disagree: `Deviations from plan`
is the phase-2 log verbatim, of which `## Special things to note` carries the
folded subset; `Issues filed` and `Ship defects:` are the full set that `##
Needs attention` lists one line each; each `Review` block is the per-finding
detail behind that reviewer's `## Review` line; and each `Verification` row is
the same phase-3 result as its line in `## Verification`. The `Self-review`
block is read from the phase-4 Report files, one row per report; a row whose
file is no longer on disk is `unverified`.

**A wrong title is fixed before the merge**, with `update-pr-title`, before the
summary is posted: the merge freezes the PR title as the squash subject, so the
human should read the title that will land.

## Unmet criteria

An acceptance criterion asking for a host action no mechanic performs stays
verbatim in the issue
([implement.md](implement.md#phase-1-a-criterion-no-mechanic-can-perform)). A
run that hits one, unattended or told to build the rest, lists it on the `Unmet
criteria` row, verbatim, with the action a human must take, and logs the gap in
the deviations log. The row is the human's to clear: **merging needs an
explicit waiver of each unmet criterion, beside the merge word**: "merge" alone,
or a waiver naming fewer than all of them, is asked back. No mechanic reads the
row, so with one listed, `Merge: on-clean-gate` does not merge: post the summary
with the reply line and wait, whatever `gate clean` answers.

## A tracker issue on Targets:

A `Targets:` entry naming an issue by number (`#<n>`, such as `map issue #1`) is
met in phase 4 by a **drafted section**, not a file edit: the new content of
each `## ` section the change affects, one draft per section, written to
`tracker-<n>-<k>.md` beside the Run file (`<n>` the
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
means evaluate the clean gate below, unless an unmet criterion is listed, which
waits for the human's waiver ([Unmet criteria](#unmet-criteria)). Any other
value holds for the human as a profile error. The unattended branch never
evaluates this option.

For an opted-in attended run, save the final `ci-wait` JSON beside the Run file
and run `run-file gate clean <ci-file> --head <head_sha> --issue <issue>`
(`--file <run.md>` for a record addressed by path). Read `<head_sha>` from
`read-pr`; the mechanic answers `{clean, held_by}` and writes nothing. It
requires the local gate at that head with every gate `pass` or `deferred-to-ci`
(held while a CI check outside `Legs:` is not green, since a deferral names no
leg), every profile CI leg green at that head, every Verification `pass` or
`n/a` (inapplicable), or `deferred-to-ci` with its `Also proven by CI:` leg
green, and every reviewer's loop stopped on `Stop: tree unchanged` with a
recorded round. A fallback that stopped so answers for a primary that was not
reviewed, or that was capped with findings (dispositioned `Round:` lines
numbered 1 through the primary's `Cap:`); a fallback skipped because its
primary otherwise reviewed adds no condition. Otherwise a loop cut short by
`Cap:`, a docs-only fix, the small lane or `auto-once` holds until its last
round changed no file. Any nonblank `Override:` other than `none`
or `None.`, any `Ship-defect:` record or `defect-*.md` draft, and any
`tracker-*.md` draft holds the gate (`*.base.md` files are saved originals, not
drafts). Deviations alone do not hold it. With no expected CI legs, `no-checks`
is clean only when the profile declares `Legs: None.` and `No-checks legal:
yes`, at the same head.

On `clean: true`, post the summary through the `Timing:` row with no reply line,
then run the merge sequence below without waiting for a reply. On `clean:
false`, post it with `Clean gate held by: <each held_by reason>` and the usual
reply line, then wait for "merge". Exit 2 is an unreadable decision: name the
error and wait. An auto-merge flag is never used: authorization is the recorded
gate, after reviewers have finished.

**The gate's verdict is cited, not re-run, while it still describes the PR.**
Phase 5 records each verdict with `run-file gate record` against the head it ran
on; here, `run-file gate read --head <head_sha>`, the head `read-pr` returns,
answers `current`. On `current: true`, cite the recorded verdict: the
`Local gate:` row reads the `gates` object that answer returns, not a re-run. On
`current: false`, re-run the gate from the worktree and record it again, because
a commit the gate never saw is in the PR: this is the final head's gate, the
one phase 7's round fixes wait for. Where the gate cannot run
here, the row carries `at <sha>, <n> commits behind`, `<n>` being that
answer's `behind`, or `at <sha>, behind unknown` when `behind` is `null`.
`non_leg_failing` on the `CI:` row is red the profile does not ask for, so it
does not hold the merge, and an `unlisted` check is profile drift for the human
to add to `Legs:` or remove.

**On explicit approval or a clean opted-in gate**, from the worktree, `merge
<pr> <issue|none> [--worktree <path>]`. Its header carries what it does and
what each refusal protects against: `pr-closed: <state>` and `stale-base:
behind <n> on <base>` merge nothing and stop at this gate, including after a
clean-gate decision (for the second, merge the base in, re-run the local gate
and come back to this gate); otherwise it squash-merges with the PR title as
the subject, closes the issue, deletes the remote branch, fast-forwards the
local base, and releases the claim and strips `ready-for-agent`, so a reopened
issue goes back through triage. Then each drafted tracker section. Every Ship
defect draft is settled before `cleanup`, which deletes the directory the
drafts live in, so ask for the word on any draft still open. Then `run-file
close 9` and set the task to the returned `mirror`, and last `cleanup
<issue|none>`, which removes the worktree, force-deletes the local branch, and
removes the Run file and the run's Scratch directory, the record's job done;
`cleanup none` leaves both in place, and a run that stopped before this gate
left its record.

**If the human says no or wants changes**, treat the note as the next round of
work: apply it on the same branch, re-run the local gate, come back to this
gate. Do not re-open the whole pipeline.

## Filing a Ship defect

A Ship defect draft reaches the source repo on the human's word alone,
and "merge" is not that word: it approves the PR, not publishing the run's
context to a public repo. On "file defects", or a word naming one draft, run
`file-issue --repo Gharib89/skills --title "<title>" --body-file <draft> --label
needs-triage` per Ship defect draft and put its answer on the row: the number
filed, the candidates it answered with instead, each read the way phase 2 reads
one, or, on exit 1 with a `command`, that command verbatim for the human to run
where the write succeeds. Before or after the merge, either order holds.

## Unattended: post to the PR, then return

First the final head's gate, as the attended lane runs it: `run-file gate read
--head <head_sha>`, and on `current: false` the re-run and record
[above](#attended-post-then-wait), so the `Local gate:` row describes the head a
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
"<title>" --body-file <file> --label needs-triage`. Detail in
[unattended.md](unattended.md).
