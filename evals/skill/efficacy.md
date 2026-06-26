# Skill-efficacy trend

The durable, **tracked** trend of the skill-efficacy review (issue #588, ADR 0016) —
the counterpart to `baseline.md`, which tracks the *correctness* pass-rate. Where
`baseline.md` answers *can an agent do the work?*, this answers *did the **skill** help,
and how efficiently?*

Appended to **only** by `python -m evals.skill review --record` (a human gate), and only
through a GUID-shape assert (`review.guard_org_agnostic`) so no org-derived content — a
Dataverse GUID or the org MAC fingerprint — can ever land here from an LLM-derived line.
Each section carries only what is about the *skill*, not the org: the per-axis
good/weak/bad tallies, the skill-lift tally, and the clustered skill-fix suggestions.

The per-run `report.md` (full per-task detail, LLM-derived from the GUID-laden traces)
stays gitignored under `runs/`; this file is the promoted, org-agnostic summary.

<!-- review --record appends dated sections below -->
