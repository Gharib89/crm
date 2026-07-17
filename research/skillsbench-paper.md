# SkillsBench (arXiv 2602.12670) — methodology, metrics, task design

Wayfinder research ticket: [#875](https://github.com/Gharib89/crm/issues/875) (map: #874).
Researched: 2026-07-17.

Sources (all claims trace to these; the paper was revised materially between v1 and v4 — **v4 is reported as canonical** here, with v1 deltas flagged):

- Abstract page: <https://arxiv.org/abs/2602.12670> (v4, revised 2026-06-14)
- Full text: <https://arxiv.org/html/2602.12670v4> (and <https://arxiv.org/html/2602.12670v1> for v1 deltas)
- Code: <https://github.com/benchflow-ai/skillsbench> (Apache 2.0) + runner <https://github.com/benchflow-ai/benchflow>

## 1. What SkillsBench is

"SkillsBench: Benchmarking How Well Agent Skills Work Across Diverse Tasks" — the first benchmark treating **Agent Skills** (structured packages of procedural knowledge in the Anthropic `SKILL.md` format, augmenting agents at inference time) as first-class evaluation artifacts. Core proposal: **paired evaluation** — run the identical task suite with and without the Skills and measure the delta ("skill lift").

77 authors; lead authors Xiangyi Li (BenchFlow), Yimin Liu (BenchFlow/OSU), Wenbo Chen (Amazon); last author Dawn Song. Contributors span UC Berkeley, Stanford, CMU, Oxford, Princeton, UCLA, UCSD, ByteDance, Amazon, plus individuals from OpenAI, Anthropic, Google, and the OpenHands team. Driven by the **BenchFlow** org (benchflow.ai).

## 2. Task design

- **87 tasks across 8 domains** (v4): Software Engineering (16), Natural Science (14), Industrial & Physical Systems (14), Office & White Collar (14), Finance & Economics (9), Mathematics & OR (8), Cybersecurity (7), Media & Content Production (5). (v1: 84 tasks / 11 domains — inventory rebalanced between revisions.)
- **Sourcing:** crowdsourced — 400 candidate submissions from 142 contributors (~1,400-member community), filtered through four automated gates (structural integrity, oracle execution, instruction provenance via AI-text detection + human labeling, leakage detection) then human review; **22% acceptance rate**.
- **Task format** — each task is a self-contained package:
    - `task.md` — human-authored instruction with YAML frontmatter
    - `environment/` — Dockerfile + data, with optional `environment/skills/` holding the paired Skill packages
    - `oracle/solve.sh` — reference solution
    - `verifier/test.sh` + `test_outputs.py` — pytest assertions
- **Skill format:** file-system packages with a required `SKILL.md` plus optional scripts and reference files — exactly the Anthropic skill layout. Skills are **paired per task by curation**: each task ships the specific curated Skill(s) relevant to it.
- **Difficulty tiers** by estimated specialist completion time: Core <60 min (6 tasks), Extended 1–4 h (53), Extreme >4 h (28).

## 3. Conditions (how skill lift is measured)

Three conditions:

1. **No-Skills baseline** — task instruction only.
2. **Curated Skills** — the task's full `environment/skills/` directory is present on disk; the agent discovers and uses it (skills are not force-injected into the prompt).
3. **Self-Generated Skills** — the agent first authors its own skill packs using Anthropic's `skill-creator`, then solves with only those packs (run on 3 dedicated configurations only).

**No wrong-skill / irrelevant-skill control is reported**, and the authors flag the missing **length-matched baseline** (gains could partly be "more context" rather than procedural structure) as a limitation.

## 4. Scoring

- **Deterministic programmatic verifiers**, not LLM-as-judge: pytest-based for 85 of 87 tasks; `test.sh` writes the reward to `/logs/verifier/reward.txt`.
- **Binary pass/fail per trial** — a task passes iff all assertions succeed (reward = 1). No partial credit.
- **Metrics:** task-macro pass rate (average per task across trials, then mean across the fixed 87-task frame), plus **normalized gain** (Hake formula from physics-education research): `g = (pass_skill − pass_vanilla) / (1 − pass_vanilla)`.

## 5. Statistics

- **3 trials per (configuration × task × condition) cell** in v4; **9,396 public trajectories** total. (v1: 5 trials/task, 7,308 trajectories.)
- **95% confidence intervals** per configuration-task pair, shown in figures. Temperature 0 where the harness allows (per v1). No formal significance testing beyond the CIs.

## 6. Models and harnesses

**18 model-harness configurations** (v4) across 4 harnesses: **OpenHands** (primary — 15 of 18 configs), **Claude Code**, **Gemini CLI**, **Codex CLI**. Models include GPT-5.5 (on both OpenHands and Codex), Claude Opus 4.7/4.8 and Sonnet 4.6, Gemini 3.1 Pro/Flash-Lite and 3.5 Flash, GLM 5.1, Kimi K2.6, MiniMax M3/M2.7, DeepSeek V4 Pro/Flash, Grok 4.3, GPT-5.4 Mini. (v1's smaller run: 7 configs — Claude Code with Opus 4.5/4.6, Sonnet 4.5, Haiku 4.5; Gemini CLI with Gemini 3 Pro/Flash; Codex with GPT-5.2.)

## 7. Key findings

- **Headline:** curated Skills lift task-macro pass rate **33.9% → 50.5% (+16.6 pp; 25.5% normalized gain)**; per-configuration gains range **+4.1 to +25.7 pp**. Best absolute with-Skills score: OpenHands + GPT-5.5 at 67.3%; biggest gain: OpenHands + GLM 5.1 (+25.7 pp).
- **Per-domain variance is large:** Natural Science +28.8 pp, Media & Content +24.1, Cybersecurity +18.9, Industrial +15.7, Finance +14.2, Office +12.6, **Software Engineering only +11.6** (v1 reported SWE as low as +4.5), Mathematics & OR +9.7.
- **Fewer, focused skills win:** 1 Skill +18.0 pp, 2–3 Skills +19.0 pp, **≥4 Skills only +10.1 pp**. By length: compact +19.0 and standard-length +21.5 pp vs detailed +14.5 pp and **comprehensive/exhaustive documentation +0.7 pp** (near-zero). Abstract phrasing: "Focused Skills with at most three modules outperform larger or exhaustive bundles."
- **Self-generated skills hurt:** −8.1 to −11.5 pp vs baseline on the three dedicated configs (v1: −1.3 pp average), where curated skills on the same configs added +18.2 to +24.8 pp. Agents cannot substitute self-authored procedural knowledge for expert curation.
- **Skills compress the model-scale gap:** smaller models with Skills match/beat larger models without (e.g., MiniMax M2.7 + Skills at 34.9% beats GLM 5.1 no-Skills at 32.7%; v1: Haiku 4.5 went 11.0% → 27.7%).
- **Skills can hurt:** 13 of 87 tasks show negative deltas (largest −7.4 pp). The top-10 highest-lift tasks average **+67.0 pp** (extreme: one task went 1.9% → 94.4%) — lift is heavily concentrated in tasks with genuinely non-guessable procedural knowledge.
- **Invocation ≠ resolution:** a high skill-invocation rate does not guarantee the task gets solved.

## 8. Stated limitations

Terminal/containerized tasks only (no GUI, multi-agent, or very-long-horizon work); containerization gives state isolation but not perfect determinism or contamination immunity; no length-matched baseline, so "procedural structure vs more context" is not fully disentangled; limited model/harness set; benchmark Skills are top-quartile quality (≥9/12 on their internal rubric) whereas the **ecosystem mean is 6.2/12** — published lifts are an upper bound relative to typical in-the-wild skills.

## 9. Infrastructure

- Repo <https://github.com/benchflow-ai/skillsbench> contains the task packages in the layout above (`tasks/<id>/{task.md, environment/{Dockerfile,skills/}, oracle/solve.sh, verifier/{test.sh,test_outputs.py}}`), plus `tasks-extra/` for credential-dependent tasks.
- Runner is the **BenchFlow CLI**: `bench tasks check tasks/<id>` validates a task; `bench eval run --tasks-dir tasks/<id> --agent <agent> --sandbox docker` runs an eval. `CONTRIBUTING.md` documents the full task structure, metadata, and review checklist.

## 10. What adopting this would mean for evaluating the crm skill

The crm agent skill (`crm/skills/`: thin `SKILL.md` router + `reference/*.md` loaded on demand) is a *single* domain-specific skill, not a corpus — and the methodology transplants cleanly:

- **The unit of measurement is already per-skill/per-task.** Each SkillsBench task pairs one small skill set with one task; the aggregate benchmark is just many pairs. Nothing requires breadth — `bench eval run --tasks-dir tasks/<one-task>` evaluates a single pairing.
- **Minimum viable eval implied by the paper:** a handful of tasks exercising the skill, each with (a) a written instruction, (b) a reproducible environment — they use Docker; a sandboxed D365 org/profile (e.g. the ephemeral `agent-cs-trial` target) is the analog for a CLI-tool skill, (c) an oracle solution, (d) a deterministic programmatic checker (pytest asserting on outputs/org state). Run under **paired conditions (skill present vs absent) × ≥3 trials each**, score binary pass rates, report the delta (optionally Hake normalized gain).
- **Design validation:** the paper's strongest finding directly endorses the crm skill's existing shape. Compact/standard-length skills with ≤3 modules gained +19 to +21.5 pp while exhaustive documentation gained +0.7 pp — matching the house rule of never restating what `crm describe`/`--help` already says. Lift concentrates where the required procedure is non-guessable; skills restating discoverable info yield near-zero or negative deltas (13/87 tasks regressed).
- **Task selection matters most:** expect measurable lift only on tasks embedding workflow/gotcha knowledge the model cannot derive from `--help` (e.g. the JSON contract, multi-step solution workflows, paging/annotation traps) — not on tasks the CLI's own help makes guessable. Measure discovery (was the skill invoked?) separately from resolution, since invocation does not guarantee success.
- **Caveats to carry over:** the paper has no wrong-skill or length-matched control — add one if lift must be attributed to content rather than context volume. Binary scoring means each task must be designed so "done" is programmatically checkable against the org. At 3–5 trials per cell, per-task conclusions are noisy; aggregate over several tasks before drawing conclusions.

**Version pin:** v1 (2026-02-13) and v4 (2026-06-14) differ materially (84→87 tasks, 11→8 domains, 7→18 configs, 5→3 trials, self-gen −1.3 → ~−10 pp). Anything citing this paper should pin the version; this doc reports v4 as canonical.
