# Phase 2 — classify, then implement test-first

First **classify the change** into one of three classes — this decides whether
TDD applies and (later, phase 7) the review ceiling. **Announce the class and the
skip path it implies** — e.g. "classified `docs` → skipping TDD and the phase-3
integrated test, going straight to the local gate" — so a wrong label is a
visible decision now, not a silently-skipped verification later. Later phases
refer back to this class by name.

- **`docs`** — markdown, comments, config text with no logic: **skip TDD** (no
  behavior to red→green; the phase-5 docs build + link check is the
  verification). Mark the commit `docs:`. Don't manufacture a contrived test.
- **`code`** (feature / bugfix): invoke the `tdd` skill **autonomously** —
  red→green→refactor **without pausing for plan approval** (you're intentionally
  overriding tdd's plan-approval checkpoint; the merge gate is the review point).
- **`infra`** (tooling / refactor where a strict red→green is awkward — the
  change *is* a test harness, build script, or fixture): don't force a contrived
  red. Extract the logic into a testable seam and unit-test its **observable
  behavior** through that seam; let the real run (phase 3) be the integration
  proof.

When in doubt between `code` and `docs`, treat it as `code` and write the test.

## Verify the spec's external-system claims before building on them

If the issue asserts a *causal mechanism* about something outside the code — an
API/SDK behavior, a platform/version constraint, "the server does X / honors Y" —
treat it as a **hypothesis, not a fact** and confirm it against the real target
with the cheapest possible probe (one live read / export / call) *before* writing
the fix around it. A triage brief's root cause is frequently a plausible guess;
building on a wrong one means implementing the fix, having phase 3 disprove it,
and rebuilding from scratch. Verifying up front collapses that loop — and if the
probe contradicts the brief, that's an early stop-and-report, not a phase-3
surprise.

## Phase 1 detail — spec precedence

A later triage brief / authoritative comment can *supersede* the issue body —
when they conflict (scope reduced, an option chosen, an axis dropped), the latest
authoritative spec wins, and the body's original acceptance criteria no longer
bind. Note this explicitly, because a review bot reading the stale body will flag
"missing" requirements you deliberately cut — you'll reject those in phases 4/7
with this as the reason.

## Phase 3 detail — run where it failed

Run the project's integrated/live tests **for only what you touched** — never the
whole suite — and create or update those tests as part of the work. **Run on the
environment the bug was actually reported against:** if the issue names a specific
target / version / config, test *there* — a different environment may auto-heal
the bug (e.g. a server that silently rewrites the bad input) and hand you a
misleading green. Green ≠ fixed unless it's green where it failed. If live creds
aren't available, print the exact command + required setup, hand it back, and
wait for the user to confirm it passed.
