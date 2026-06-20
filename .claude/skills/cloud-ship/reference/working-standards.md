# Working standards

The operator's global coding philosophy, reproduced here. It does **not** live in
the repo's own `CLAUDE.md` (the clone carries only that, plus `ship` / `tdd` /
`review`, which cover tests, gates, and the merge flow) — so this file carries it
into the fire. These standards fill the engineering-judgment layer those assume.
Hold them through the whole fire.

- **Don't build the wrong thing.** Surface tradeoffs; if the issue is ambiguous or
  underspecified, STOP and report rather than guessing. (This is also `ship`'s
  phase-1 ambiguity rail — in a fire, "report" means the blocked hand-off.)
- **Simplicity first.** Minimum code that solves the problem — no features beyond
  what was asked, no abstractions for single-use code, no configurability that
  wasn't requested, no error handling for impossible cases. "Would a senior
  engineer call this overcomplicated?"
- **Surgical changes.** Touch only what you must. Read a file before editing it and
  grep for callers before changing a function. Don't improve / refactor adjacent
  code; match existing style. Remove only orphans your change created; mention
  unrelated dead code, don't delete it. Every changed line must trace to the issue.
- **Plan first** on multi-step or architectural work; delegate research / parallel
  analysis to subagents to protect context. Find **root causes** — no symptom
  patches, no TODO-as-excuse, no commented-out blocks, no swallowed errors. Run an
  elegance pass on your own non-trivial new code (not adjacent existing code).
- **Comments:** document public APIs and non-obvious WHY (constraints, invariants,
  workarounds); skip narrative comments on internals.
- **Keep the PR and disposition summary concise, but always include the WHY.**
