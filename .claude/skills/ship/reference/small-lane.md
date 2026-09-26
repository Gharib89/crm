# The small lane: the reduced spine

A change that passes all three lane keys (SKILL.md, *The lanes*) skips the
ceremony that cannot matter and keeps every check. What it drops is planning
breadth alone. The floor is the same in every repo.

## The size cap

Key 3's cap is **200 changed lines**, added plus deleted, as
`git diff --numstat origin/HEAD...HEAD` counts them. Excluded: test files the
run wrote or edited for its red-first tests, and files the run regenerates
rather than authors (a derived copy the refresh line writes, `skills-lock.json`,
a Tripwire's rebuilt bundle). Agent-facing prose counts. The run never edits a
CHANGELOG (ADR 0003), so no rule covers one.

**Measured, never estimated.** Phase 2 announces the lane as a prediction. The
count is taken at phase 2's `Done when:`, with every edit committed, and again
before every later push, in attended and unattended runs alike. Over the cap
revokes to the full lane.

## What collapses

Keys 1 and 2 already make phase-3 verification and the phase-4 docs-sync gate
no-ops by construction. The `writing-for-agents` pass keeps its own trigger.
On top of that:

- **Local gate (phase 5)**, `base-fresh` first as always: with **one** proving
  test node, `--small <node>`, the repo's `secrets` gate plus that node, or, for
  a `docs`-class change with no such test, the changed document, written in the
  profile's `Small node:` syntax. With **more than one**, the full local gate.
  Lean on CI for the rest: a red CI on a small change is a cheap round-trip.
- **At most one *requested* round per on-request reviewer**, fallback included,
  whatever its `Cap:`. `auto-once` and `on-push` reviewers behave as in the full
  lane; ship does not control when they fire.
- **Subagents:** only `code-review`'s axes; map, execute, verify and the
  `writing-for-agents` pass run inline.

## The floor: stands in every lane

1. Isolation (phase 0)
2. `base-fresh` and the local gate, `--small <node>` or full as above
3. The **self-review, unmodified**: the only check that reads the diff against
   the issue, since a reviewer reviews standards with the issue out of view,
   and it carries the two rejection rails
4. Non-draft PR with `Closes #<issue>` above the first `## ` heading
5. CI green plus every reviewer per its trigger
6. The merge gate

Adjacent finds are still filed: an unfiled find is lost and filing costs one
call.

## Revocable, one way

Any of these **downgrades to the full lane** for the remaining phases: the diff
counted over the size cap, CI red on behavior, a reviewer or the self-review
flags a real bug, the local gate's floor check hits, or the change turns out to
touch the public surface. Downgrade means: run the skipped verifications and
docs-sync, add the missing test or docs, run the full local gate, and apply
full-lane review terms, from there on. Downgrading once is cheap; shipping a
non-small change as small is the failure.
