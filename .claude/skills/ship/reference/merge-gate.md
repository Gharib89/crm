# Phase 9 — the merge gate

This is the merge gate — the one guaranteed human stop (rationale in the autonomy
contract in SKILL.md). Your job is to make that call a 10-second yes/no by laying
out everything they'd want to check.

## Post this summary, then stop

```
## /ship summary — #<issue>: <title>

PR:        <url>  (<branch> → <default-branch>)
Issue:     <one-line restatement of what was asked>
Lane:      <full | small — skipped: integrated test, self-review (bot covers), local suite (CI)>

Implementation
  - <what was built, 1–3 lines>
  - tests added/updated: <files / count>

Integrated tests
  - target(s) run: <which, e.g. on-prem / cloud / both>  → <pass | handed to you>
  - <anything skipped and why>

Self-review (review skill)
  - <comment> → <fixed | rejected: reason | n/a>
  ...

Automated review
  - rounds used: <n>/3  (<clean | ceiling reached>)
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

1. Squash-merge the PR and delete the remote branch
   (`gh pr merge <n> --squash --delete-branch`). The squash **subject** is what
   release tooling reads — make sure it's the Conventional-Commit line from the
   PR title (see project instructions).
2. Re-verify the PR actually merged (`gh pr view <n> --json state,mergedAt`)
   before reporting done — don't assume the command took.
3. Confirm the linked issue closed. The `Closes #<issue>` in the squash body should
   auto-close it on merge to the default branch; verify (`gh issue view <issue>
   --json state`) and close it manually if it didn't.
4. Clean up the local workspace: a squash-merged branch isn't an ancestor of the
   default branch, so local branch deletion needs a force delete, and exiting the
   worktree should discard its now-orphaned changes.

## If the user says no / wants changes

Treat their note as the next round of work: apply it on the same branch, re-run
the local gate, and come back to this gate. Don't re-open the whole pipeline.
