---
status: accepted
---

# 0017 — Test-suite audit & prune (coverage-diff as the deletion gate)

## Context

The offline suite had grown to **4598 tests** (170 files, ~63k LOC) and we wanted to know whether that number reflects real value or accumulated drift, redundancy, and low-value scaffolding — plus where the coverage blind spots are. The worry: a big test count that doesn't pull its weight, and silent gaps.

## Decision

Audit the whole offline suite with a **conservative-by-category** bar and **execute the high-confidence prune in the same pass** (no deferred report — the deep map *is* the execution spec). Method:

- **Classify** every offline test file with one LLM agent per file against a fixed taxonomy (trivial / vacuous / tautological → delete · same-equivalence-class duplicates → collapse · drifted = passes-but-asserts-stale-behaviour → fix, delete only if the behaviour is gone). Every actionable finding must carry concrete evidence (test ids + the shared production lines, or the stale assertion vs the current code) — no evidence ⇒ "needs human", never auto-delete.
- **Gate every deletion on a coverage diff.** Run `coverage.py` (line+branch, dev-only, not added to CI) over the production package before and after. *Any* production line that loses coverage means the deleted test was **not** redundant — restore it. This empirically validates the "redundant" classifications instead of trusting them.

Only **high-confidence** findings were executed here; medium/low-confidence ones were deferred to #590.

## What the audit found

The suite is **healthy**: 3344 / 3420 test functions (**97.8 %**) were kept untouched; production line+branch coverage is **91.1 %**; there is essentially no mock-theater (2 call-only assertions suite-wide) and the e2e command gate's `E2E_SKIP` is empty. The "4000+ = bloat" hypothesis was largely false — the value was in dedup, a handful of genuinely-broken tests, and a blind-spot map, not a big delete number.

## What this change did

- **−54 collected tests**: 13 high-confidence deletes (testing Click/dataclass/stdlib, not crm logic) + collapse of 43 same-equivalence-class redundancy clusters.
- **6 drift fixes** — tests that passed for the wrong reason and gave false confidence: a `assert x == x` self-comparison; two `assert dry_backend.dry_run is True` on a read-only property with no setter; three `metadata_cache` tests whose payloads omitted the `schema` key so they hit the schema-version early-return instead of the check they claimed to test.
- **5 orphaned test-only constants/imports** removed (made dead by the deletions).
- Coverage held: **91.107 % → 91.125 %** production line coverage, **zero regressions**. The gate did its job — it caught one bad collapse (`async_ops` `list` vs `list_all` have *separate* owner_id-normalization blocks, so the two tests were not redundant) which was reverted.

## Blind spots (for backlog, not this PR)

91 % production coverage; the uncovered ~9 % splits two ways: **interactive code** (`repl.py`/`repl_skin.py` 46–51 %, the `profile` wizard) which is hard to unit-test and acceptable, and **pure/testable functions worth backfilling** — `core/batch.py::parse_batch_file`, `d365_backend::_parse_batch_response`, `core/apply::validate_spec` branches, several `commands/ribbon` verbs, `commands/view::_parse_width`, `core/relationships::create_many_to_many`.

## Consequences

- The coverage-diff gate is the reusable rule for any future test prune: never delete a test without proving (line+branch) that no production code lost coverage. Note it is necessary but not sufficient — two tests can cover the same lines with different boundary inputs, so the equivalence-class judgement still matters.
- Medium/low-confidence findings (53) live in #590 under the same bar.
- `coverage.py` is intentionally **not** wired into CI; it was a one-off analysis tool for this audit.
