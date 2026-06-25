# Skill effectiveness eval — Machine B (tracer)

Behavioral eval of the shipped `crm` skill (ADR 0015, governed by ADR 0009). It runs
a task through an agent that has **only the installed skill + the `crm` binary +
`gh`** — no repo, no `CLAUDE.md`, no memory — then scores the result with a
deterministic end-state predicate. Isolation is the validity keystone: if it leaks,
the eval measures the repo, not the skill.

The end-to-end skeleton landed as the **tracer** (issue #570) on a single task;
issue #571 broadened it into a **workflow-per-domain task set** (the `tasks/*.md`
specs below) plus a **set runner** that runs the whole set against one target and
reports an absolute pass-rate; issue #572 added the optional Claude **`--analyze`
pass** (see below). Still to build on it: the both-targets baseline trend (#573).

This tree is **not shipped in the wheel** (excluded in `setup.py`) and is **not
collected by the default `pytest` suite** (`testpaths = crm/tests`), so it never
blocks CI. Run it on demand.

## Layout

- `tasks/*.md` — one task per file: YAML frontmatter (`id`, `domain`, `target`,
  `end_state`, `cleanup`) plus a markdown body that is the **verbatim prompt** fed to
  the agent. `end_state` is optional: a **diagnostic** task omits the `expect`
  predicate and is scored by the `--analyze` pass instead (see below).
- `taskspec.py` — task parsing and the pure end-state predicate (`evaluate_expect`).
- `isolation.py` — provisions and **verifies** the isolated agent context.
- `target.py` — live-target selection, reusing the e2e `D365_E2E_PROFILE` mechanism
  and the `D365_E2E_ALLOW_HOST` prod-host guard.
- `runner.py` — orchestrates one task: isolate → verify → seed → agent → score → cleanup.
- `set_runner.py` — runs the **whole set** against one target: target-gates each task
  (skips off-target ones), scores the rest, reports per-task verdicts + pass-rate.
- `analyze.py` — the optional Claude analysis pass (`build_analysis_prompt` +
  `run_analysis`), a seam unit-testable offline without invoking Claude.
- `test_runner_smoke.py` / `test_set_runner.py` — offline smoke tests (parse tasks,
  dry-run isolation, and set-level gating/aggregation via a stub — no agent, no org).

## Task set & domain coverage

The set samples **executability** — does a skill-only agent carry each multi-command
workflow to its declared end state? — across the skill's reference domains. (Skill
*discoverability* is Machine A's job, #569, not re-proven here.) Each `tasks/*.md`
is a multi-command workflow with a deterministic end-state predicate and cleanup.

| reference domain | task(s) | target |
|---|---|---|
| records | `records-create-verify`, `records-validate-write`, `trial-bulk-load` | cloud, cloud, onprem |
| metadata | `trial-global-optionset` | onprem |
| customizations | `customizations-view-edit`, `trial-customization-workflow`, `trial-webresource-iterate` | cloud, onprem, onprem |
| solutions | `trial-customization-workflow` (export), `trial-import-diagnosis` | onprem, onprem |
| automation | `trial-process-state` | onprem |
| security | `security-role-create` | cloud |
| dup | `dup-rule-create` | cloud |
| connectionrole | `connectionrole-create` | cloud |
| fieldsec | `fieldsec-profile-create` | cloud |
| feedback | `feedback-note-create` | cloud |
| authoring | `authoring-chart-create` | cloud |

The eight trials from `docs/research/2026-06-skill-trial-plan.md` are all formalized
here (TRIAL-3 → `customizations-view-edit`, TRIAL-7 → `records-validate-write`; the
other six keep the `trial-` prefix). On-prem trials are pinned `onprem` because they
exercise a v9.1 quirk or seeded on-prem state, so a cloud run **skips** them (the
on-prem leg is #573's union); the cloud tasks are the sample that runs on the
cloud-ship routine's `agent-cloud` target.

**Domains intentionally not sampled** (no contrived task is added just to tick a box):
`setup` is local profile/connection management with no org-side end state;
`troubleshooting` is diagnostic with no clean programmatic predicate — its qualitative
scoring is the `--analyze` pass (#572), and `trial-import-diagnosis` already touches
it; `customization-lifecycle` (publish/managed lifecycle) is exercised by the export
and publish steps inside the customization trials; `workflow-xaml` is automation-
adjacent and on-prem-heavy, sampled indirectly by `trial-process-state`.

**Known cleanup limitation.** Two on-prem metadata trials
(`trial-customization-workflow`, `trial-global-optionset`) create a custom table /
global option set. The cleanup model deletes *records* by a filter, but a metadata
*definition* must be removed by its logical name via `metadata delete-entity` /
`delete-optionset` — and that name is chosen by the agent at run time, so it cannot be
expressed as a static cleanup step. Those two tasks therefore leave definition residue
that a maintainer clears out of band; widening the cleanup model to cover agent-named
metadata is deliberately left out of this slice.

## Smoke test (offline, no agent, no org)

```bash
pytest evals/skill
```

## Dry run (proves isolation; no agent, no live org)

```bash
python -m evals.skill.runner evals/skill/tasks/records-create-verify.md --dry-run
```

## Full run (isolated agent against a live target)

Point at a target the same way as the e2e suite — name a profile from your real
`CRM_HOME`; its creds are read read-only and re-seeded into a throwaway `CRM_HOME`.
The agent command is yours to wire (the harness does not presume one); the prompt is
fed on **stdin**. For a cloud (`*.dynamics.com`) org, opt in the exact host with
`D365_E2E_ALLOW_HOST`.

```bash
D365_E2E_PROFILE=agent-cloud \
D365_E2E_ALLOW_HOST=<your-org>.crm.dynamics.com \
CRM_EVAL_AGENT_CMD='claude -p' \
    python -m evals.skill.runner evals/skill/tasks/records-create-verify.md
```

The runner prints a JSON result (`passed`, `reason`, `isolation_checks`, the captured
`transcript`) and exits non-zero only on a scored failure. Cleanup runs
unconditionally, so the org is left clean whether the task passed or failed.

## Run the whole set against one target

The set runner runs every `tasks/*.md` spec against the configured target, skips the
tasks gated for the *other* target, scores the rest, and prints a per-task table plus
the absolute pass-rate (`passed / (passed + failed)`; skips and errors excluded). It
exits non-zero if any task failed its predicate or hit a harness error.

```bash
# Offline: parse + prove isolation for every task (no agent, no live org).
python -m evals.skill.set_runner --dry-run

# Live, against one target (cloud here; on-prem tasks are reported as skipped).
D365_E2E_PROFILE=agent-cloud \
D365_E2E_ALLOW_HOST=<your-org>.crm.dynamics.com \
CRM_EVAL_AGENT_CMD='claude -p' \
    python -m evals.skill.set_runner          # add --json for the machine-readable result
```

The target is inferred from the profile's auth scheme (OAuth → cloud, NTLM → on-prem),
exactly as for the single-task runner. Run it once per profile to get the both-targets
union (#573 wires that into a periodic baseline trend).

## Optional Claude analysis pass (`--analyze`, #572)

Off by default — the deterministic `expect` predicate stays *the* gate. When you add
`--analyze`, the runner routes `{task, transcript, final org state, programmatic
verdict}` to Claude for a qualitative read of *why* a task passed or stumbled, and
records it in the result's `analysis` field. The analyzer is a command (like the
agent under test), reading the composed prompt on **stdin**:
`$CRM_EVAL_ANALYZE_CMD`, or `--analyze-cmd`, defaulting to `claude -p`.

```bash
D365_E2E_PROFILE=agent-cloud \
D365_E2E_ALLOW_HOST=<your-org>.crm.dynamics.com \
CRM_EVAL_AGENT_CMD='claude -p' \
    python -m evals.skill.runner evals/skill/tasks/records-create-verify.md --analyze
```

**Diagnostic tasks** declare no `expect` predicate (see
`tasks/diagnostic-data-quality.md`) — there is no clean end state to assert, so the
analysis pass is their *only* score. Running a diagnostic task without `--analyze`
is refused up front (nothing to score). A diagnostic task may still declare an
`end_state.query` so its final org state is fetched and fed to the analyzer.
