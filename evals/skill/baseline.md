# Skill-eval baseline — effectiveness trend

A periodic run of the behavioral skill eval (ADR 0015, Machine B) appends one **dated
per-target row** below per fire, via `python -m evals.skill.both_runner --update-baseline`.
**Effectiveness drift = this trend sliding** — a human reads it; nothing here gates CI and
no threshold blocks anything.

Each row records one target's run: `pass-rate` is the percentage and `scored` the raw
`passing-trials / total-trials` fraction (it widens with `--repeat`, which runs each task
N times to smooth variance). A target whose host did not answer (on-prem with the VPN
down) lands a `—` row whose `notes` say why, so the gap is visible rather than silently
omitted. Coverage across the two targets is the **union** of what each reachable leg scored.

Rows are append-only, oldest first. See `evals/skill/README.md` for how to run it and
`docs/agents/skill-eval-routine.md` for the periodic cadence.

| date | target | profile | pass-rate | scored | repeat | notes |
|------|--------|---------|-----------|--------|--------|-------|
| 2026-06-26 | cloud | agent-cloud | 100% | 9/9 | 1 |  |
| 2026-06-26 | onprem | agent-on-prem | 67% | 4/6 | 1 | 2 fails are harness defects, not skill drift (#584): optionset predicate hardcodes `new_` prefix (org uses `ag_`); trial-import-diagnosis precondition `agtrial8` unseeded |
