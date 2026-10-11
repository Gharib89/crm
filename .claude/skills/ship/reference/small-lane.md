# The small lane: the reduced spine

A change that passes all three lane keys (SKILL.md, *The lanes*) skips the
ceremony that cannot matter and keeps every check. The floor is the same in
every repo.

## The size cap

Key 3's cap is **200 changed lines**, added plus deleted, as
`git diff --numstat origin/HEAD...HEAD` counts them. Excluded: test files the
run wrote or edited for its red-first tests, and files the run regenerates
rather than authors (a derived copy the refresh line writes, `skills-lock.json`,
a Tripwire's rebuilt bundle). Agent-facing prose counts. The run never edits a
CHANGELOG.

**Measured, never estimated.** Phase 2 announces the lane as a prediction. The
count is taken at phase 2's `Done when:`, with every edit committed, and again
before every later push, in attended and unattended runs alike. Over the cap
revokes to the full lane.

## A guard is full lane

A diff touching a guard, an allowlist or a deny rule is full lane, whatever its
size, unless it runs inline under `--review` (below). Predict it at phase 2
with the keys, and count it as failing key 2.

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
   the issue, and it carries the two rejection rails
4. Non-draft PR with `Closes #<issue>` above the first `## ` heading
5. CI green plus every reviewer per its trigger
6. The merge gate

Adjacent finds are still filed: an unfiled find is lost and filing costs one
call.

## Revocable, one way

Any of these **downgrades to the full lane** for the remaining phases: the diff
counted over the size cap, CI red on behavior, a reviewer or the self-review
flags a real bug, the local gate's floor check hits, or the change turns out to
touch the public surface or a guard, allowlist or deny rule. Downgrade means:
run the skipped verifications and docs-sync, add the missing test or docs, run
the full local gate, and apply full-lane review terms, from there on.

## The inline lane

The **inline lane** is the small lane's tier for a fix triage already has in
hand, entered only by `/ship <issue> --inline [--review]`. Invoking it is the
human's approval to merge this one PR on a clean gate, and nothing wider. With
`--unattended`, or free text in place of an issue, stop `inline refused` before
`prepare`, printing the usage line `/ship <issue> --inline [--review]`.

- **Keys:** the small lane's three, plus a **20-line cap** counted the way the
  size cap above counts its 200, at the same moments. A guard, allowlist or
  deny-rule diff stays inline with `--review`, and is full lane without.
- **Kept from above:** the floor, the local gate, subagents, adjacent finds.
- **Reviewers:** without `--review`, request no round. Each on-request
  reviewer, fallback included, records `Stop: <reviewer>: inline lane` with no
  `Round:` line, and its `## Review` line reads `not reviewed: inline lane`.
  With `--review`, each on-request reviewer that is no fallback takes the small
  lane's one requested round, and a fallback stands in per `Fallback-for:`.
  `auto-once` and `on-push` reviewers behave as in every lane.
- **Merge:** phase 9 takes merge-gate.md's `on-clean-gate` branch for this PR.
- **Named:** the run header, the `## Review` section and the merge summary say
  `inline lane`. Phase 2 writes `Lane: inline` in the Run file: `gate clean`
  honours an `inline lane` stop only while the last `Lane:` line reads `inline`.

**Revocation runs inline, then small, then full.** One of the small lane's keys
failing at phase 2 makes the run full lane. The count passing 20, at phase 2 or
later, drops it to the small lane; a trigger under *Revocable, one way* (a guard
touch under `--review` excepted) drops it straight to the full lane. The
approval goes with the lane: append `Lane: small` or `Lane: full`, phase 9
follows the profile's `Merge:` line, and each reviewer takes that lane's rounds,
appending its `Round:` and `Stop:` lines after any `inline lane` stop, since the
last `Stop:` decides. Say so in the next reply and in the merge summary.
