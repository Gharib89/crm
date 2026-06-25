---
status: proposed
---

# Evaluating the `crm` skill: a static coverage gate plus a behavioral effectiveness eval

## Context

The `crm` skill ships to users who have **only the skill, not the repo** (`crm skill
install` copies `crm/skills/` into a harness dir). It is deliberately sparse: a thin
`SKILL.md` router over 14 `reference/*.md` files that states *only* workflows, gotchas, and
the JSON contract, and defers every flag/choice/default to `crm describe` / `crm <group>
--help` (ADR 0009). We want three things from it, indefinitely: that it stays **effective**,
that it **does not drift** as the 276-leaf CLI evolves, and that an agent armed with it can
**do everything the CLI can do**.

Nothing today proves any of that. `test_skill_bundle.py` guards *structure* (router ≤250
lines, the 14 reference files present + linked, no repo-only paths). The e2e coverage gate
(`@covers` + `walk_commands`, floor >100) guards that every D365 *command* has a live test —
not that the *skill* covers it. A manual 8-trial protocol
(`docs/research/2026-06-skill-trial-plan.md`) is the only behavioral check, and it is
narrative and unautomated. No test compares skill prose against the CLI; nothing scores
whether an agent succeeds.

The three goals decompose into four distinct things to prove, and no single mechanism
proves all four:

- **discoverability** — every command is reachable *through the skill's routing*;
- **executability** — an agent can actually carry the work out;
- **structural drift** — the skill never references a command that no longer exists, and a
  newly added command group is never left unrouted;
- **effectiveness drift** — the skill's real-world success rate doesn't quietly erode.

Discoverability and structural drift are *static and deterministic*. Executability and
effectiveness drift are *behavioral, expensive, and non-deterministic*. Forcing them into
one machine yields either a flaky CI gate or a static check that can't see whether the skill
actually works.

## Decision

Build **two machines**, split along the static/behavioral line.

### Machine A — skill-coverage gate (static, deterministic, per-PR CI)

A single citation parser feeding two assertions and one waiver list. Runs in the offline
pytest suite, so it **blocks every PR** like the e2e coverage gate it mirrors.

1. Parse every `` `crm <group> [verb]` `` citation out of `SKILL.md` + `reference/*.md`
   once. Anchor on literal commands in code spans, treating only `<group> ∈ known-groups` as
   a citation (incidental prose like "create a contact" must not register as a command).
2. Get the live command catalogue from the existing `walk_commands()` lazy-loader walk —
   offline, no subprocess, no installed binary.
3. **Dead-reference:** every cited command resolves in the catalogue.
4. **Completeness (group granularity):** every real top-level group appears in the cited set
   *or* in an explicit `WAIVED` set (each waiver carries a reason, e.g. `completion`,
   `doctor`, `service-document` — groups `--help` fully covers and the skill needn't route).

The gate fires on a **new group** (the skill must add a route) and on a **stale citation**
(a dead command). It **passes silently on new verbs and flags under an already-routed
group** — `describe`/`--help` already cover those, so tripping on them would fight the
skill's intentional sparseness (ADR 0009). Lives in `crm/tests/skill_coverage.py`
(parser + `WAIVED`) + `crm/tests/test_skill_coverage_gate.py`, mirroring `coverage.py` /
`test_e2e_coverage_gate.py`. Complements, does not replace, `test_skill_bundle.py`.

### Machine B — skill effectiveness eval (behavioral, on-demand + periodic, never CI)

- **Isolation (the validity keystone).** The eval agent gets **only** the installed skill +
  the `crm` binary + `gh` — no repo, no `CLAUDE.md`, no memory. That is exactly what a real
  user has; running with the repo present tests the repo, not the skill.
- **Target:** both `agent-cloud` and `agent-on-prem`, coverage taken as the **union**,
  reusing the e2e `D365_E2E_PROFILE` mechanism. Cloud gives always-on breadth; on-prem is the
  priority target and catches the divergent gotchas the skill exists to encode.
- **Tasks:** ~12–15 realistic, multi-command **workflows, one+ per reference domain**
  (records, metadata, customizations, solutions, automation, security, …), seeded by
  formalizing the existing 8-trial plan. Discoverability is *not* re-proven here — Machine A
  owns that; Machine B samples *executability*.
- **Scoring:** a **programmatic end-state assertion** (`crm --json query/get` → expected org
  state) is the deterministic pass/fail gate; an **optional** pass hands the transcript +
  final org state + verdict to Claude to analyze *why* a task failed or stumbled (and to
  score diagnostic tasks that have no clean end-state). **Absolute pass-rate** — no A/B
  control arm.
- **Cadence:** on-demand before a `crm/skills/` change; plus a **periodic scheduled** run
  that appends pass-rate to a tracked `baseline.md`. **Effectiveness drift = the trend
  sliding.** Lives in a new `evals/skill/` at the repo root (`tasks/*.md`, a runner,
  `baseline.md`), **not shipped in the wheel.**

## Considered options

- **Leaf- or flag-level coverage gate** (skill must mention all 276 commands / never restate
  a flag) — rejected. It fights the router design (ADR 0009): the skill would be forced to
  enumerate what it deliberately delegates to `describe`, and the gate would run permanently
  red on every new verb.
- **Explicit declared `group → reference_file` map** for completeness — rejected in favor of
  deriving coverage from the citation parser. The parser is needed for dead-reference
  detection anyway; a second hand-maintained map is just another artifact to drift.
- **A/B lift (run each task with *and* without the skill; metric = pass-rate delta)** —
  considered and **deliberately not adopted**, to halve the runs and keep the harness simple.
  The accepted cost: absolute pass-rate measures "can the agent do the work," **not** "did
  the skill cause it." Recorded so the trade-off is a known choice, not an oversight — if
  attribution ever matters, the control arm is the way back.
- **Mock target for the behavioral eval** — rejected. A mock can't reproduce the real D365
  quirks (publish-before-read, XAML BOM, on-prem `0x80045002` locks) the skill exists to
  encode, so it would pass tasks a real org fails.
- **Blocking per-PR CI for the behavioral eval** — rejected. Live orgs, VPN for on-prem, and
  full agent-run latency/flakiness make it unfit as a required check. The static gate is the
  per-PR blocker; the behavioral harness runs out-of-band.

## Consequences

- The static gate is the instant structural-drift guard and is cheap to keep green; it
  honors ADR 0009 because new verbs and flags never trip it — only new groups and dead
  citations do.
- The behavioral eval's validity rests entirely on **isolation**. Spawning an agent with the
  skill but *without* repo access is the hardest engineering bit; if isolation leaks, the
  eval silently measures the repo and over-reports.
- Absolute pass-rate means a green run says "an agent could do these tasks," not "because of
  the skill." Effectiveness-drift detection therefore relies on a human reading the periodic
  `baseline.md` trend — there is no hard pass-rate threshold gating anything.
- The citation parser carries a false-positive risk (incidental prose mistaken for a command
  citation); its anchoring rule (code-span + known-group) is load-bearing and worth a unit
  test of its own.
- This extends ADR 0009's "states only what `describe`/`--help` cannot" invariant with the
  enforcement it was missing: Machine A mechanically guards the *reachability* half, Machine
  B periodically measures the *workflow* half.
