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
pass** (see below); issue #573 added the **both-targets runner** that unions
`agent-cloud` + `agent-on-prem` coverage, skips an unreachable target, and appends a
periodic **baseline trend** (see below); issue #585 wrapped them in the `run` **front
door** (see below); and issue #588 (ADR 0016) added the **skill-efficacy review** — a
second, post-hoc evaluation that judges whether the skill *helped* the agent reach the
goal efficiently (see "Skill-efficacy review" below).

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
  and the `D365_E2E_ALLOW_HOST` prod-host guard; plus `probe_reachable`, the
  short-timeout reachability check that lets the both-targets runner skip a downed VPN.
- `runner.py` — orchestrates one task: isolate → verify → seed → agent → score → cleanup.
- `__main__.py` — the `python -m evals.skill run` **front door** (#585): a thin wrapper
  that routes `--target` to the set/both runner, defaults the agent command past the
  permission-gate footgun, derives the cloud allow-host, and writes `result.json` +
  `run.log` automatically. See "Easiest invocation" below.
- `set_runner.py` — runs the **whole set** against one target: target-gates each task
  (skips off-target ones), scores the rest, reports per-task verdicts + pass-rate.
  `--repeat N` runs each scored task N times to smooth variance (the pass-rate becomes a
  fraction over all trials). Emits **live progress to stderr** as each task resolves (#585).
- `both_runner.py` — runs the set against **both** targets, unions the coverage, skips an
  unreachable one, and (`--update-baseline`) appends a dated per-target row to `baseline.md`.
  Prints a per-leg header and inherits the set runner's live progress (#585).
- `baseline.md` — the tracked effectiveness **trend**: one dated per-target pass-rate row
  per periodic run, read by a human for drift (ADR 0015). Never a CI gate.
- `analyze.py` — the optional Claude analysis pass (`build_analysis_prompt` +
  `run_analysis`), a seam unit-testable offline without invoking Claude.
- `trace.py` — parses a `stream-json` agent trace into the efficiency signal: the ordered
  `crm` command sequence (`parse_commands`) and the run metrics (`parse_metrics`) (#588).
- `record.py` — the durable per-task run record (`TaskRunRecord`) written under `runs/`,
  plus its read/write helpers and the skill-SHA provenance stamp (#588).
- `review.py` — the **skill-efficacy review** (`build_review_prompt` / `parse_review` /
  `review_records` / `build_report` / the `efficacy.md` GUID guard), a seam unit-testable
  offline without invoking Claude (#588).
- `efficacy.md` — the tracked **skill-efficacy trend**, appended only by `review --record`
  through a GUID-shape guard (the efficacy counterpart of `baseline.md`, ADR 0016).
- `runs/` — **gitignored** durable run dirs (`<UTC-ts>/<task-id>.json` + `report.md`); they
  carry live-org GUIDs and the org machine fingerprint, so they are never tracked (#588).
- `test_runner_smoke.py` / `test_set_runner.py` / `test_target.py` / `test_both_runner.py` /
  `test_trace.py` / `test_record.py` / `test_review.py` / `test_isolation.py` — offline smoke
  tests (parse tasks, dry-run isolation, set-level gating/aggregation, reachability
  classification, both-targets orchestration, trace parsing, run-record persistence, and the
  review prompt/parse/guard — all via stubs, no agent, no org).

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

## Easiest invocation — `python -m evals.skill run` (#585)

The front door wraps the runners below with sane defaults so a run is hard to misuse —
no hand-set agent command, allow-host, target env var, or `> result.json 2> run.log`:

```bash
python -m evals.skill run --target onprem --model sonnet   # one target
python -m evals.skill run --target both                    # both (the default)
python -m evals.skill run --target cloud --repeat 3
```

- **`--target cloud|onprem|both`** picks the standing profile(s) (`agent-cloud` /
  `agent-on-prem`) and the underlying runner; `both` is the default.
- The agent command defaults to **`claude -p --dangerously-skip-permissions --model sonnet`**
  — the permission-gate footgun is gone, and `sonnet` is the baseline (the harness measures
  the skill, not the model). `--model <m>` swaps the model; `--agent-cmd <cmd>` overrides the
  whole command (and `--model` is then rejected, not silently appended).
- The cloud **allow-host is derived** from the resolved `agent-cloud` profile, so no
  `D365_E2E_ALLOW_HOST` paste is needed for the standing cloud target (an already-set value
  is respected).
- **`result.json` + `run.log` are written automatically** (to the current dir, or `--out <dir>`)
  and their paths are printed at the end.

It **composes** the existing `set_runner` / `both_runner` entry points (below), which keep
working unchanged for finer control.

## Live progress (#585)

The set and both runners emit human-readable progress to **stderr** as each task resolves —
a `[done/total] STATUS id (target)` line, a rolling per-target `pass / fail (of N runnable)`
tally, a `trial k/N` tick per trial under `--repeat`, and (both runner) a per-leg header.
Progress is **stderr-only**, so the stdout `--json` / `> result.json` document is byte-for-byte
unchanged. It defaults **on at a terminal, off under redirect / non-TTY**; `--quiet` forces it
off and `--progress` forces it on (e.g. to capture it into a redirected `run.log`). Both
runners accept the flags; the front door always captures progress into `run.log` regardless.

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
CRM_EVAL_AGENT_CMD='claude -p --dangerously-skip-permissions' \
    python -m evals.skill.runner evals/skill/tasks/records-create-verify.md
```

The runner prints a JSON result (`passed`, `reason`, `isolation_checks`, the captured
`transcript`) and exits non-zero only on a scored failure. Cleanup runs
unconditionally, so the org is left clean whether the task passed or failed.

### Agent authentication

`CRM_EVAL_AGENT_CMD='claude -p --dangerously-skip-permissions'` drives headless Claude
Code as the agent. `--dangerously-skip-permissions` lets the headless agent execute its
tools (`Bash` to invoke `crm`, plus `Read`/`Skill` to load the skill) without an
interactive approval — otherwise every `crm` call is gated and the task false-fails. The
flag bypasses **all** approval, so the agent runs commands with your normal filesystem
access: the harness withholds the routes to the repo/memory (`isolation.py`) but does
**not** hard-sandbox the filesystem (containers/namespaces are out of tracer scope), so
run it only against a throwaway target you're willing to let an agent drive. The analyzer
command below stays bare `claude -p` (it reads stdin and emits text, no tools). The sandbox
hands the agent a fresh `HOME`, so it can't see your real Claude Code login — the
harness therefore copies **only** your credentials file
(`~/.claude/.credentials.json`, or under `$CLAUDE_CONFIG_DIR` if you set it) into the
sandbox `HOME`. A normal **Claude subscription login is enough — no
`ANTHROPIC_API_KEY` required.** Nothing else from your config dir rides along (no
`CLAUDE.md`, no memory, no settings), and `CLAUDE_CONFIG_DIR` is scrubbed from the
agent's environment, so the repo/memory isolation the eval depends on is preserved.

With no credentials file (an API-key-only setup) the copy is a no-op and the agent
falls back to `ANTHROPIC_API_KEY` from your environment, as before. This applies
identically to the single-task runner and the set runner below.

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
CRM_EVAL_AGENT_CMD='claude -p --dangerously-skip-permissions' \
    python -m evals.skill.set_runner          # add --json for the machine-readable result
```

The target is inferred from the profile's auth scheme (OAuth → cloud, NTLM → on-prem),
exactly as for the single-task runner. To run **both** targets in one go, see below.

## Run both targets + the baseline trend (`both_runner`, #573)

The both-targets runner loops the two standing profiles (`agent-cloud`, then
`agent-on-prem`), runs the set against each **reachable** one, and reports coverage as the
**union**. A target whose host does not answer (on-prem with the VPN down) is **skipped
with a message**, never failed — so a cloud-only run still succeeds and lands its rows.
`--repeat N` runs each task N times per target to smooth run-to-run variance.

```bash
# Both targets, 3 trials per task, append the dated rows to baseline.md:
D365_E2E_ALLOW_HOST=<your-org>.crm.dynamics.com \
CRM_EVAL_AGENT_CMD='claude -p --dangerously-skip-permissions' \
    python -m evals.skill.both_runner --repeat 3 --update-baseline   # --json for the raw result
```

Each profile is pointed at via `D365_E2E_PROFILE` internally (saved/restored, so your env
is untouched); creds are read from your real `CRM_HOME` (read-only) and re-seeded per leg,
as the single-target runner does. The exit code is non-zero only if a **reachable** target
scored a failure/error — an unreachable target is a skip, not a failure.

**`baseline.md` is the effectiveness trend.** `--update-baseline` appends one dated
per-target row (pass-rate as a percentage **and** the raw `passing/total` trial fraction; a
skipped target lands a `—` row whose `notes` say why). A human reads the trend for drift;
**nothing here gates CI** and no threshold blocks anything (ADR 0015). The periodic cadence
that fires this is wired as a scheduled routine — see
[`docs/agents/skill-eval-routine.md`](../../docs/agents/skill-eval-routine.md).

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
CRM_EVAL_AGENT_CMD='claude -p --dangerously-skip-permissions' \
    python -m evals.skill.runner evals/skill/tasks/records-create-verify.md --analyze
```

**Diagnostic tasks** declare no `expect` predicate (see
`tasks/diagnostic-data-quality.md`) — there is no clean end state to assert, so the
analysis pass is their *only* score. The analyzer is asked to end with a
`VERDICT: PASS` / `VERDICT: FAIL` line; the runner parses it into `passed` and the
process exit code, so a diagnostic task is genuinely scored (and a failed analyzer,
or one that emits no verdict, is surfaced rather than silently passing). Running a
diagnostic task without `--analyze` is refused up front (nothing to score). A
diagnostic task may still declare an `end_state.query` so its final org state is
fetched and fed to the analyzer.

## Skill-efficacy review (persist-then-analyze, #588, ADR 0016)

The `--analyze` pass above and the pass-rate answer the **correctness verdict**: *did the
agent reach the goal?* The skill-efficacy review answers the two questions a skill author
actually has — *did the **skill** help, and did it reach the goal efficiently?* — as a
**second, post-hoc** evaluation built on a **persist-then-analyze** split. It never changes
the pass/fail gate.

### Capture — every `run` persists a durable record

The front door's default agent command is now
`claude -p --dangerously-skip-permissions --output-format stream-json --verbose --model <model>`,
so the captured trace is the full JSONL event stream (every `tool_use` + a final `result`
event with `num_turns` / `total_cost_usd` / `duration_ms`). **Every** `python -m evals.skill
run` writes a durable run dir:

```
evals/skill/runs/<UTC-ts>/<task-id>.<target>.json   # {prompt, raw_trace, commands[],
                                                    #  metrics, correctness_verdict,
                                                    #  skill_sha, target, efficacy_review?}
```

The filename is keyed by task **and** target, so a `--target both` run persisting both legs
into one dir never overwrites an `either` task's cloud and on-prem records; the skill-absent
counterfactual leg adds a `.counterfactual` suffix.

`commands[]` is the parsed, ordered list of `crm` invocations — the spine of the efficiency
question. `runs/` is **gitignored**: a trace carries live-org GUIDs and the org machine
fingerprint, and this is a public repo. A custom `--agent-cmd` that drops `stream-json
--verbose` silently blinds the review.

### Review — judge a saved run, no agent, no live org

```bash
python -m evals.skill review                 # judge the latest run dir
python -m evals.skill review --run evals/skill/runs/<ts> --failed-only --task <id>
```

`review` reads the saved records and, per task, routes
`{prompt, crm command sequence, metrics, correctness verdict, the live skill text}` to a
reviewer command (`$CRM_EVAL_REVIEW_CMD` / `--review-cmd`, defaulting to
`claude -p --model opus` — a judgment task) for a **structured** verdict: three graded axes
(*goal reached* / *command economy* / *skill adherence*, each `good|weak|bad`), a
**skill-lift** call (`helped|neutral|hindered`), and a **skill-fix** suggestion (the
concrete skill edit that would have helped, or `none`). It writes each verdict back into the
record and emits `report.md` (a per-task table + the clustered skill-fixes digest) into the
run dir.

The reviewer reads `crm/skills/` **live from disk**; the record stamps the skill **git SHA**.
So after editing the skill you can re-run `review` over the *same* saved traces to ask "did
my fix help?" at zero live cost — the `run → review → read report → edit skill → re-review`
loop.

### Measured lift — the opt-in counterfactual

The judged review is the always-on default. To *measure* (not just judge) lift, the `run`
step can add a skill-**absent** leg per task:

```bash
python -m evals.skill run --target cloud --counterfactual          # whole set, both legs
python -m evals.skill run --target cloud --counterfactual --task <id>
```

or a task's frontmatter `counterfactual: true` (the per-task "always measure this one"
knob). The absent leg provisions isolation **without** `crm skill install` and verifies the
skill is genuinely absent; its record lands beside the present leg as
`<task-id>.counterfactual.json`. When both legs exist `review` compares them; otherwise it
is judged-only. The counterfactual doubles live D365 cost per task, so it is opt-in (the
reason ADR 0015 dropped the always-on A/B arm).

### Tracked trend — `efficacy.md` (promote-on-demand)

`report.md` stays gitignored (per-run, LLM-derived from GUID-laden traces). The durable,
**tracked** trend lives in `efficacy.md` — the efficacy counterpart of `baseline.md` —
appended **only** by `review --record` (a human gate) and **only** through a GUID-shape
assert (a Dataverse GUID or the `…00155d…` org MAC fingerprint fails the write loudly). It
carries only org-agnostic content: the per-axis tallies, the lift tally, and the clustered
skill-fix suggestions.

```bash
python -m evals.skill review --record        # judge + append the org-agnostic trend
```
