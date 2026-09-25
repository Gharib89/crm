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

**Measured, never estimated.** Phase 2 announces the lane as a prediction from
the plan. The count is taken at phase 2's `Done when:`, with every edit
committed, before phase 3 is skipped, and again before every later push. Over
the cap revokes to the full lane, below. The cap applies in attended and
unattended runs alike.

## What collapses

Keys 1 and 2 already make phase-3 verification and the phase-4 docs-sync gate
no-ops by construction: nothing on the public surface changed, nothing the
real thing needs to prove. The `writing-for-agents` pass keeps its own trigger
and does not collapse with docs-sync. On top of that:

- **Local gate (phase 5)**, `base-fresh` first as always: with **one** proving
  test node, `--small <node>`, the repo's security check (the `secrets` gate,
  whatever scanner the gate body wires) plus that node, or, for a `docs`-class
  change that has no such test, the changed document itself, the node written
  in the profile's `Small node:` syntax. With **more than one**, the full local
  gate. Lean on CI for the rest of the suite, lint and types: a red CI on a
  small change is a cheap round-trip.
- **At most one *requested* round per on-request reviewer**, fallback included,
  whatever its `Cap:`; a free round with nothing actionable ends that reviewer's
  loop `converged` with no request. `auto-once` and `on-push` reviewers behave
  as in the full lane; ship does not control when they fire.
- **Subagents:** only `code-review`'s axes; map, execute, verify and the
  `writing-for-agents` pass run inline.

## The floor: stands in every lane

1. Isolation (phase 0)
2. `base-fresh` and the local gate: `--small <node>` (the security check plus
   the test proving the change, or the changed document where the class is
   `docs`), or the full gate where more than one test proves it
3. The **self-review, unmodified**. It is the only check that reads the diff
   against the issue; a reviewer reviews standards against the diff, with the
   issue out of view. It also carries the two rejection rails. It costs two
   judgment-tier axes whatever the diff.
4. Non-draft PR with `Closes #<issue>` above the first `## ` heading
5. CI green plus every reviewer per its trigger
6. The merge gate

Adjacent finds are still filed: an unfiled find is lost and filing costs one
call.

## Revocable, one way

The lane is falsifiable. Any of these **downgrades to the full lane** for the
remaining phases: the diff counted over the size cap, CI red on behavior, a
reviewer flags a real bug, the self-review flags a real bug, the local gate's
floor check hits, or the change turns out to touch the public surface.
Downgrade means: run the skipped verifications and docs-sync, add the missing
test or docs, run the full local gate, and apply full-lane review terms. The run
stays full lane from there on. Downgrading once is cheap; shipping a non-small
change as small is the failure.
