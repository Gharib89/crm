# Phase 9 — the merge gate

This is the merge gate — the one guaranteed human stop (rationale in the autonomy
contract in SKILL.md). Your job is to make that call a 10-second yes/no by laying
out everything they'd want to check.

## Post this summary, then stop

```
## /ship summary — #<issue>: <title>

PR:        <url>  (<branch> → <default-branch>)
Issue:     <one-line restatement of what was asked>
Lane:      <full | small — skipped: integrated test, local suite (CI), self-review (if auto-bot)>

Implementation
  - <what was built, 1–3 lines>
  - tests added/updated: <files / count>

Deviations from plan
  - <departure: what + why, conservative option taken>   (or: None — plan held)

Integrated tests
  - target(s) run: <which, e.g. on-prem / cloud / both>  → <pass | handed to you>
  - <anything skipped and why>

Self-review (code-review skill)
  - <comment> → <fixed | rejected: reason | n/a>
  ...

Automated review   (one block per reviewer — lanes need the per-reviewer split)
  - round-1 reviewer: <clean | threads dispositioned>   (never re-requested)
  - iterating reviewer: <converged: quiet on latest push | n/a — no push-triggered bot>
  - <comment> → <fixed | rejected: reason | n/a>
  ...

Local gate:  tests <✓/✗> · lint <✓/✗> · type <✓/✗> · docs <✓/✗> · security-scan <✓/✗/n/a>
Docs-sync:   <ran: files | skipped: reason>
CI:          <checks> → <green | state>

Ready to merge. Reply "merge" to squash-merge, delete the branch, and clean up.
```

Then **wait.** Do not merge until the user explicitly says so. Never use an
auto-merge flag while a review could still be pending — it can merge the instant
CI is green, before a review lands.

## On approval

Run `scripts/merge-and-verify.sh <pr> <issue>` — it squash-merges via REST with
the PR title as the squash subject (release tooling reads it, so the title must
already be the Conventional-Commit line), re-verifies the PR actually merged,
deletes the remote branch, and confirms the linked issue closed (closing it if
the `Closes #<issue>` keyword didn't).

Then clean up the local workspace: a squash-merged branch isn't an ancestor of
the default branch, so local branch deletion needs a force delete, and exiting
the worktree should discard its now-orphaned changes.

## If the user says no / wants changes

Treat their note as the next round of work: apply it on the same branch, re-run
the local gate, and come back to this gate. Don't re-open the whole pipeline.
