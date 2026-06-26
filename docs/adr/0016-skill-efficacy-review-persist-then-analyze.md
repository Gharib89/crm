---
status: accepted
---

# Skill-efficacy review: persist the run, judge it afterwards

## Context

ADR 0015's Machine B answers *can an agent do the work?* — a deterministic end-state
predicate scores each task pass/fail, and an optional inline `--analyze` pass (#572) hands
the transcript + org state + verdict to Claude for a qualitative read of *why* a task
failed (and is the only score for a diagnostic task). That pass-rate is the trend a human
reads in `baseline.md`.

It does **not** answer the next two questions a skill author actually has:

- *Did the **skill** help?* — pass-rate is absolute; ADR 0015 deliberately dropped the A/B
  control arm, so a green run says "the agent could," never "because of the skill."
- *Did it reach the goal efficiently?* — fewest, most-appropriate `crm` commands, no
  trial-and-error, no `--help` loops the skill should have pre-empted.

Three things block answering them today:

1. **The set/both/front-door path persists no transcript.** `result.json` is
   `SetResult.to_dict()` → `TaskOutcome` dicts (`{task_id, status, target, reason, …}`).
   The transcript lives only on the single-task `RunResult` and is printed to stdout by
   `python -m evals.skill.runner`, never saved. There is nothing durable to analyze later.
2. **The captured transcript can't show *how* it ran.** `_run_agent` captures `claude -p`
   **stdout = final text only** — no tool calls, no command sequence, no turn/cost metrics.
   "Most efficient command / least effort" is not in the data.
3. **`--analyze` is correctness-shaped and single-task-only.** It judges *why pass/fail*;
   the set runner *skips* diagnostic tasks and has no analyze hook at all.

## Decision

Add a **skill-efficacy review** as a second evaluation, distinct from #572's correctness
verdict, built on a **persist-then-analyze** split. Two new domain terms:

- **Correctness verdict** (existing, #572) — *did the agent reach the goal?* Unchanged.
- **Skill-efficacy review** (new) — *did the skill help the agent get there efficiently?*
  Runs on every task, pass or fail; never changes the pass/fail gate.

### Capture (the `run` step)

The default agent command becomes
`claude -p --dangerously-skip-permissions --output-format stream-json --verbose --model <model>`,
so stdout is the full JSONL event stream — every `tool_use`, every `tool_result`, and a
final `result` event with `num_turns` / `total_cost_usd` / `duration_ms` / `usage`.

**Every run** writes a durable run dir `evals/skill/runs/<UTC-ts>/<task-id>.json` holding
`{prompt, raw_trace, commands[], metrics, correctness_verdict, skill_sha, efficacy_review?}`.
`commands[]` is the parsed ordered list of `crm` invocations pulled from the trace — the
spine of the efficiency question — so reporting needs no re-parse. `efficacy_review` is
absent until the review step fills it. `result.json` + `run.log` are unchanged and additive.

The run dir is **gitignored** (`evals/skill/runs/`): the trace carries live-org GUIDs and
the org machine fingerprint, and this is a public repo.

### Review (the `review` step — post-hoc, no agent, no live org)

`python -m evals.skill review` reads the latest run dir and, per task, calls Claude (the
reviewer, defaulting to `--model opus` — a judgment task) to produce a **structured**
review: graded axes (*goal reached* / *command economy* / *skill adherence*, each
`good|weak|bad` + one line), a **skill-lift verdict** (`helped|neutral|hindered`), and a
**skill-fix suggestion** (the payoff: the concrete skill edit that would have helped, or
`none`). It writes each review back into the task record **and** emits `report.md` in the
same step (table + skill-fixes digest clustered across tasks). Filters: `--task <id>`,
`--failed-only` (default all); `--run <dir>` targets an older run.

The reviewer reads `crm/skills/` **live from disk** (it runs in the operator's env, not the
sandbox) rather than snapshotting skill text into each record; the run record stamps the
skill **git SHA** for provenance. Re-running `review` after editing the skill therefore
re-judges the saved traces against the **new** skill — the "did my fix help?" loop, at zero
live cost.

### Measured lift (hybrid, opt-in)

The judged review is the always-on default. Measuring lift needs a second, skill-**absent**
agent run, which only the `run` step can produce: `run --counterfactual [--task X]`, or a
task's frontmatter `counterfactual: true` (the per-task "always measure this one" knob),
runs both legs. Mechanically: `provision_isolation` skips `crm skill install` for the
absent leg and `verify_isolation` check 5 flips to assert the skill is *absent*. When both
legs exist the review compares them (commands/turns/success); otherwise it is judged-only.

### Tracked record (promote-on-demand)

`report.md` stays gitignored (per-run, churny, LLM-derived from GUID traces — "should be
clean" ≠ "clean by construction"). A separate tracked **`evals/skill/efficacy.md`** holds
the durable trend, written only by `review --record` (a human gate), through a **GUID-shape
assert** (GUID regex + the `…00155d…` fingerprint) so any org-derived leak fails loudly
instead of landing in a commit. It carries only org-agnostic content: axis tallies and the
clustered skill-fix suggestions (about the skill, not the org). This mirrors the
`baseline.md`-is-tracked precedent and the org-agnostic-eval discipline (#586).

## Considered options

- **Inline-only analysis (extend `--analyze` globally)** — rejected. The agent would re-run
  every time you want a fresh read, and an interesting failure is gone once `result.json`
  is written. Persist-then-analyze lets you run fast, judge later, and re-judge old traces
  against an edited skill for free.
- **`--output-format json` (final object + metrics, no per-tool detail)** — insufficient:
  "most efficient command" needs the per-`tool_use` sequence, which only `stream-json` emits.
- **Store the raw trace only, no parsed index** — rejected. The `crm` command sequence is
  what the efficiency question is *about*; locking it inside an unparsed blob forces a
  re-parse for every metric and bloats the reviewer prompt.
- **Snapshot skill text into each record** — rejected for read-live + SHA stamp: smaller
  records, and re-analysis judges the current skill (the improvement loop) with the SHA
  flagging run/review divergence.
- **Track the raw `report.md`** — rejected. It is per-run, churny, and LLM-derived from
  GUID-laden traces; tracking unguarded output is the exact leak path the repo's
  no-org-fingerprint rule warns against. A guarded, human-promoted `efficacy.md` gives the
  trackable record without the risk.
- **Always-on A/B counterfactual** — rejected as the default (2× live D365 cost per task,
  ADR 0015's reason for dropping it). Kept as opt-in so lift is measurable when it matters.
- **Name the new subcommand `analyze`** — rejected for `review`: `--analyze` already names
  the correctness verdict; a distinct verb keeps the two evaluations from sharing one word.

## Consequences

- This revives ADR 0015's deferred control arm as an **opt-in** `--counterfactual`, not the
  default — attribution is available without paying for it on every run.
- The run dir is gitignored, so the efficacy data is **local-only** by default; the tracked
  trend exists solely through the human-gated, GUID-guarded `efficacy.md`.
- The reviewer's quality depends on the trace being complete; `stream-json --verbose` is
  load-bearing, and a future change to the agent command that drops it silently blinds the
  review.
- Two evaluations now share the harness; the term split (**correctness verdict** vs
  **skill-efficacy review**) and the verb split (`--analyze` vs `review`) are the guard
  against them being conflated.
- The skill-fix digest turns every eval into actionable skill-improvement signal, closing
  the loop ADR 0015 left open: `run → review → read report → edit skill → re-review`.
