# Phase 9: the merge gate

## Contents

- [The summary](#the-summary)
- [A tracker issue on Targets:](#a-tracker-issue-on-targets)
- [Attended: post, then wait](#attended-post-then-wait)
- [Filing a Ship defect](#filing-a-ship-defect)
- [Unattended: post to the PR, then return](#unattended-post-to-the-pr-then-return)

The one guaranteed human stop. Your job is to make the call a ten-second yes or
no by laying out everything the human would want to check.

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
  <name> (<trigger>, <n> rounds): <reviewed | not reviewed: <reason> | not invoked: <primary> reviewed>[, <N> denied calls (run <url>[, run <url>…])]
    (the denied-calls clause as review-loop.md's Review line carries it, only where N > 0)
    - <finding> → <fixed in <sha> | declined: reason | filed: #<n>>
    ...                                        (or: clean, no findings)
    (a fallback that ran opens with: fallback for <primary>: not reviewed: <reason>)
    (a Gating: yes reviewer's declined finding: override needed: <finding>, <evidence>)

Local gate:  <derived from the gate's JSON: <gate> <✓ | ✗ | deferred-to-ci | unavailable> · ...>
Docs-sync:   <ran: files | skipped: reason>
Tracker:     <none | one block per drafted section:>
  #<n> `## <section>`:
  <the drafted section, verbatim>
  (unattended: the command a human runs after merging, per the tracker section)
CI:          <leg> → <green | state> · ...     (from the profile's Legs:)
Issues filed: <#n <title>, ... | none>  ·  linked: <#n <title>, ... | none>
Ship defects: <none | one block per defect:>
  - <missing write, missing gating read, or wrong prose> (phase <n>)
    draft for Gharib89/skills: <title> · <draft path>
Direct reads: <none | <call> · <why>, ...>     (from the Run file's ## Direct reads)
Timing:      <`run-file timing`'s `row`, verbatim>

Ready to merge. Reply "merge" to squash-merge, close the issue, and clean up.
(with a Ship defect draft: Reply "file defects" to file the drafts at Gharib89/skills.)
```

**Every row is grounded in a result from this run**: `Local gate:` is the
gate's `gates` object, `CI:` is `ci-wait`'s output, `Issues filed` is this
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
`Gharib89/skills`, written to `<scratchpad>/ship-<issue>/defect-<k>.md` when it
is met: a title, then a body naming Ship's version, the mechanic or prose at
fault and what the run did instead, and no organization identifier, credential
or client context, because the source repo is public. A gap in the ship profile
rather than in Ship is a **profile defect**: an adjacent find of this repo,
filed here through phase 2's dispositions and listed under `Issues filed`, not
on this row. Where this repo is the source repo, a Ship defect is an adjacent
find already (its profile's `## Triage`) and the row names its number instead of
a draft.

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
<draft>` per tracker draft, `<section>` verbatim from the heading `read-issue` returned.
Re-read the issue first: a section that no longer matches its base is
redrafted, posted, and written on the human's explicit "yes". `created: true`
for a section the draft meant to replace means the name missed: re-run with the
returned heading, and name the stray section as a Ship defect. Exit 1 means
nothing was written: a Ship defect for the summary, with the tracker draft attached.

The unattended lane runs no merge, so under each draft the summary gives the
command a human runs after merging, from a file they save the draft to,
`.claude/skills/ship/scripts/update-issue-body.sh <n> --section "<section>"
--body-file <file>`, once they have checked the section has not changed.

## Attended: post, then wait

Post the summary in the conversation and **wait**. Merge only on an explicit
"merge", and the word is exact: a typo, a synonym, or approval of some other
part of the summary is asked back, because merging is the step no later phase
undoes. Never an auto-merge flag either: it can merge the instant CI is green,
before a reviewer lands.

**On approval**, from the worktree, `merge <pr> <issue|none> [--worktree
<path>]`. Its header carries what it does and what each refusal protects
against: `pr-closed: <state>` and `stale-base: behind <n> on <base>` merge
nothing (for the second, rebase, re-run the local gate and come back to this
gate); otherwise it squash-merges with the PR title as the subject, closes the
issue, deletes the remote branch, fast-forwards the local base, and releases the
claim and strips `ready-for-agent`, so a reopened issue goes back through
triage. Then each drafted tracker section, then `cleanup <issue|none>`, which
removes the worktree and force-deletes the local branch.

**If the human says no or wants changes**, treat the note as the next round of
work: apply it on the same branch, re-run the local gate, come back to this
gate. Do not re-open the whole pipeline.

## Filing a Ship defect

A Ship defect draft reaches the source repo on the human's word alone ([ADR 0004](https://github.com/Gharib89/skills/blob/main/docs/adr/0004-cross-repo-writes-reach-the-source-repo-on-the-humans-word.md)),
and "merge" is not that word: it approves the PR, not publishing the run's
context to a public repo. On "file defects", or a word naming one draft, run
`file-issue --repo Gharib89/skills --title "<title>" --body-file <draft> --label
needs-triage` per Ship defect draft and put its answer on the row: the number filed, the
candidates it answered with instead, each read the way phase 2 reads one, or,
on exit 1 with a `command`, that command verbatim for the human to run where
the write succeeds. Before or after the merge, either order holds.

## Unattended: post to the PR, then return

`comment-pr <pr> --body-file` with the summary, then **return** with the PR
link. Do not wait, poll, or merge; the claim stays on the issue, which carries
the open PR, so later fires skip it until a human merges. The last line becomes
"Ready to merge: a human merges from the PR." A Ship defect's draft is never
filed from here, and the comment drops the "file defects" line: it carries each
draft verbatim under the command a human runs from a file they save it to,
`.claude/skills/ship/scripts/file-issue.sh --repo Gharib89/skills --title
"<title>" --body-file <file> --label needs-triage`. Detail in
[unattended.md](unattended.md).
