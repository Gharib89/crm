# Alternatives to SkillsBench for evaluating one private agent skill

Researched 2026-07-17 against primary sources (GitHub repos/READMEs via API, official docs, Anthropic engineering posts). All maintenance claims checked against live `pushed_at` / commit data on that date.

## TL;DR

For evaluating ONE private, domain-specific skill (the shipped `crm` skill) whose tasks must hit a **live Dynamics 365 org**, the ranking is:

1. **DIY: pytest + claude-agent-sdk (or headless `claude -p`)** — the only option where the live-backend setup/teardown problem is already solved in this repo (the existing `crm/tests/e2e/` fixtures, profiles, and coverage gate). The with/without-skill A/B is a first-class switch (`--bare` skips skill auto-discovery; `--plugin-dir`/settings load it), and `--output-format json` reports `total_cost_usd` per run. Claude-family only, but that matches how the skill is actually consumed.
2. **Anthropic skill-creator (eval + benchmark modes)** — zero-setup, purpose-built with-skill vs without-skill dual-run with blind comparator agents, pass-rate/time/token deltas, and an HTML viewer. Best for interactive iteration on the skill; weaker as a repeatable CI gate (agent-orchestrated, not deterministic).
3. **promptfoo** — the best third-party fit if a cross-model / cross-harness matrix is ever wanted: side-by-side provider matrix, a Claude Agent SDK provider, exec providers that can wrap any agent CLI, `trajectory:*` assertions and model-graded rubrics; live backend is fine because providers are arbitrary code, but stateful setup/teardown is your glue.

inspect-ai (+ inspect_swe) is the credible heavyweight alternative when cross-harness rigor matters; terminal-bench/Harbor and SkillsBench itself are container-first public-benchmark machinery (overkill, and docker-isolation fights the live-org requirement); HAL is **archived** (July 1, 2026); openai/evals is dormant. Verdict: build the eval as a pytest module on the existing live-e2e infrastructure, and use skill-creator's benchmark loop for authoring-time iteration.

## Context

- **Under evaluation:** one agent skill — `crm/skills/` (`SKILL.md` router + `reference/*.md`) shipped inside the `crm` Python CLI for Microsoft Dynamics 365.
- **Question the eval must answer:** does loading the skill measurably improve an agent's success on realistic `crm`/D365 tasks vs the same agent without the skill (and ideally: across models, and across agent harnesses)?
- **Hard constraint:** tasks need a **live D365 backend** — real HTTP, credentials, mutating operations, per-task setup/teardown against a live org. Docker-isolated replay environments do not capture this. This is a private eval, not a public benchmark.

---

## 1. Anthropic's own skill-eval guidance and tooling

**What it measures / eval model.** The `skill-creator` skill (anthropics/skills) gained explicit **Eval** and **Benchmark** support ("Improving skill-creator" blog post, and the SKILL.md itself). Evals live in `evals/evals.json`: prompt + expected output + named, objectively-verifiable assertions. Benchmark mode is a **dual-run architecture**: "For each test case, spawn two subagents in the same turn — one with the skill, one without" (baseline is `without_skill` for new skills, `old_skill` snapshot for improvements). A grader subagent writes `grading.json` (pass/fail + evidence per assertion); `scripts.aggregate_benchmark` produces `benchmark.json` with pass rate, mean±stddev time and tokens, and the **delta between with_skill and baseline**. An optional blind **comparator agent** judges two outputs without knowing which is which. An HTML benchmark viewer shows outputs, grades, and stats.

Complementary guidance: the engineering posts "Equipping agents for the real world with Agent Skills" (start from observed failures, build skills incrementally, evaluate on representative tasks) and "Demystifying evals for AI agents" (evaluate harness+model together; start with 20–50 tasks from real failures; code graders vs LLM judges vs humans; grade the artifact, not the path; pass@k vs pass^k; read transcripts). The platform docs' skill best-practices page is explicit that "there is not currently a built-in way to run these evaluations" outside skill-creator — you own the harness. `claude -p` headless mode is the documented substrate for scripted runs (see option 8).

**Adoption cost.** Near zero — the skill-creator plugin is already installed in this environment; runs happen inside a normal Claude Code session. No infra, no docker.
**Cross-model / cross-harness.** Claude-family only, and effectively single-harness (Claude Code / Claude.ai; on Claude.ai it degrades to serial runs without baselines). Model can be varied per subagent run only crudely.
**With/without-skill A/B.** Yes — this is the *core* design (dual-run + blind comparator). The most natural A/B of any option surveyed.
**Live-backend fit.** Good: subagents run in the real working environment, so they can invoke the installed `crm` CLI against a live profile. But setup/teardown discipline is manual (you'd pre/post-seed the org yourself), and runs are agent-orchestrated — reproducibility and CI-gating are weak (timings/tokens captured only from task notifications).
**Maintenance / license.** anthropics/skills is actively maintained by Anthropic; repo is open source (skills repo published by Anthropic; skill-creator updated with eval support, announced on claude.com blog).
**Verdict:** best authoring-time loop; not a CI gate.

## 2. SkillsBench (the reference point)

**What it is.** benchflow-ai/skillsbench — "SkillsBench evaluates how well skills work and how effective agents are at using them." Maintained by the **BenchFlow team**; paper at arXiv:2602.12670; leaderboard + docs at skillsbench.ai. 87 expert-curated tasks across professional domains; v1.1 released 2026-06-14; repo very active (last push 2026-07-16), Apache-2.0, ~1.5k stars.
**Eval model.** Agent pass rate on containerized tasks, compared **with skills vs without skills** (the site reports ~32.6% average normalized gain from skills across 25 model×harness configs). Tasks are BenchFlow-native packages: `task.md`, `environment/Dockerfile` + `environment/skills/`, `oracle/solve.sh` (reference solution), `verifier/test.sh` + `test_outputs.py`. Run via the BenchFlow SDK: `uv tool install benchflow`, `bench tasks check`, `bench eval run`, API keys via env vars.
**Cross-model / cross-harness.** Strong — its headline result covers OpenHands (default), **Claude Code**, and **Gemini CLI** harnesses across GPT-5.5 / Claude Opus / Gemini / DeepSeek-class models.
**Usable by outsiders for a private skill?** Technically yes: the task format is open, tasks run locally under docker, and there is even a `tasks-extra/` convention for credential-dependent tasks (included via `--no-default-excludes`). But you would be authoring a full BenchFlow task package (Dockerfile, oracle solution, verifier) per eval case, and the machinery assumes container-isolated, reproducible environments — the opposite of a stateful live D365 org. Contribution flow (CONTRIBUTING.md review checklist) targets the public benchmark.
**With/without-skill A/B.** Yes, natively — that is the benchmark's whole design.
**Live-backend fit.** Poor. Docker-first; a live mutating org breaks its reproducibility model. Credentials can be injected (tasks-extra), but setup/teardown against a shared live org is not what the harness manages.
**Verdict:** the right *reference design* (with/without conditions, verifier scripts, oracle solutions) but the wrong runtime for a private live-backend skill eval.

## 3. Terminal-bench / Harbor

**What it is.** terminal-bench (now under the **harbor-framework** org; `laude-institute/terminal-bench` redirects) is "a benchmark for LLMs on complicated tasks in the terminal": tasks = instruction + docker environment + test scripts + reference solution (`task.yaml` config: timeouts, test scripts, difficulty). Its README directs new users to **Harbor** — "framework from the creators of Terminal-Bench for evaluating and optimizing agents," which runs Terminal-Bench 2.0. Both Apache-2.0 and very active (harbor pushed 2026-07-17; terminal-bench 2026-07-11; 3.2k / 2.5k stars).
**Eval model.** Containerized task execution; agent runs inside the environment; verifier tests decide pass/fail; results aggregated as pass rates over trials. `harbor run -d "<dataset@version>" -m "<model>" -a "<agent>"`.
**Cross-model / cross-harness.** Excellent — first-class agent adapters including **Claude Code, Codex CLI, OpenHands** (and "evaluate arbitrary agents"); model is a separate `-m` axis. Anthropic's own "Demystifying evals" post recommends Harbor for "containerized environments at scale."
**Private task sets.** Yes — you can "build and share your own benchmarks and environments" and run local datasets; nothing forces publication.
**With/without-skill A/B.** Not a built-in concept. You'd encode it as two dataset variants (skill baked into the environment image vs not) or two agent configs — workable but manual.
**Adoption cost.** Moderate-high for a solo maintainer: docker per task, environment images containing the `crm` CLI, secrets injection, and writing verifiers. Containers *can* reach a live D365 over the network, but Harbor manages the container lifecycle, not your org state — live setup/teardown is still all yours, now with extra docker layers in between.
**Verdict:** the best option if cross-harness (Claude Code vs Codex CLI) comparison on many tasks ever becomes the goal; overkill and docker-shaped friction for one private skill against a live org.

## 4. HAL — Holistic Agent Leaderboard

**What it is / measured.** princeton-pli/hal-harness (Princeton SAgE group): standardized harness + leaderboard for running agents across 11+ benchmarks (SWE-bench Verified, USACO, AppWorld, tau-bench, CORE-bench…) with **cost-controlled evaluation by default** — accuracy plus token/cost tracking and traces, without modifying agent code. Local (conda), Docker, or Azure VM execution; `hal-eval` CLI; custom benchmarks were extensible via `hal/benchmarks/`.
**Status — disqualifying.** The repository was **archived on 2026-07-01** and is read-only ("retiring active PRs… focusing our current work on agent reliability"); the leaderboard stopped accepting submissions. No license file is detected via the GitHub API.
**Fit.** Even before archiving, HAL was a multi-benchmark leaderboard harness aimed at published agent comparisons — heavy adoption cost, no skill A/B concept, no story for a private live-backend task set.
**Verdict:** ruled out (archived).

## 5. OpenAI Evals

**What it is.** openai/evals — "a framework for evaluating LLMs… and an open-source registry of benchmarks." YAML-registry evals over JSONL data; basic and model-graded eval classes; the Completion Function Protocol nominally supports tool-using agents. MIT-licensed per the README (GitHub's API reports the license as non-standard/NOASSERTION).
**Maintenance — effectively dormant.** Verified via commit history: the last substantive product commit is 2024-09-30; a Dec 2024 README change points users to the **hosted OpenAI Dashboard evals**; the only 2025–2026 activity is housekeeping (removing a suite with defunct dependencies, pinning CI actions, last push 2026-04-14). Not archived, but no feature development for ~22 months — "maintenance mode" is a fair description.
**Fit.** OpenAI-model-centric; no agent-harness adapters (no Claude Code/Codex CLI concept); no sandbox/environment model for CLI tasks; no skill A/B. **Verdict:** ruled out.

## 6. inspect-ai (UK AI Security Institute)

**What it is / measures.** UKGovernmentBEIS/inspect_ai — "a framework for large language model evaluations" by the UK AI Security Institute. Tasks = dataset + solver (agent) + scorer. Built-in agents (ReAct, deep agent, human-baseline), multi-turn tool use, **sandboxing (docker, k8s)**, and model-graded plus code-based scorers; 200+ prebuilt evals. MIT, extremely active (6.7k+ commits, 246 tags, pushed 2026-07-17).
**Cross-model.** Broad first-party provider support (Anthropic, OpenAI, Google, and many others — provider-string per run), so the same task can be swept across models.
**Cross-harness.** Yes, uniquely rigorous: the **Agent Bridge** runs third-party frameworks (OpenAI Agents SDK, LangChain, Pydantic AI) as Inspect agents, and the companion **inspect_swe** package (meridianlabs-ai) exposes **Claude Code, Codex CLI, Gemini CLI, OpenCode, Mini SWE Agent** as standard Inspect agents — e.g., `claude_code()` as the task solver. That makes "same task, Claude Code vs Codex CLI, any model" a config change.
**With/without-skill A/B.** Not built in, but clean to express: parameterize the task (or the sandbox image / working dir contents) on skill-installed vs not, and compare scores.
**Live-backend fit.** Workable: sandboxes are optional (local execution is supported), solvers/scorers are arbitrary Python, and nothing stops tasks from hitting live HTTP with real credentials; per-sample setup/teardown is your Python. Cost: a real framework to learn (tasks, solvers, scorers, eval logs/viewer) — days, not hours, for a solo maintainer.
**Verdict:** the credible "serious" alternative; choose it over DIY only if cross-harness comparisons or larger eval suites are actually planned.

## 7. promptfoo

**What it is / measures.** promptfoo/promptfoo — "Test your prompts, agents, and RAGs." Declarative YAML evals: a matrix of providers × prompts × test cases with per-test assertions (deterministic checks, `llm-rubric` model-graded metrics, and **`trajectory:*` assertions** over normalized tool-call spans — including `trajectory:goal-success`, where a judge decides whether the traced workflow completed the task). MIT, very active (pushed 2026-07-17).
**Agent/CLI support.** First-party **Claude Agent SDK provider** (configurable tools, permissions, MCP servers, working dir; documented for running Claude Code in containers/VMs); guides for evaluating coding agents (Codex, Claude, OpenCode) and — directly on point — a **"Test Agent Skills" guide**; Python/exec custom providers can wrap any CLI (e.g., a `claude.js`/shell provider invoking `claude -p` or `codex exec`), and multi-turn sessions are supported by feeding a list of user turns through a stateful provider.
**Cross-model / cross-harness.** Native strength: one config lists several providers (Anthropic, OpenAI, Google, custom endpoints) and the web viewer renders a side-by-side matrix. Cross-harness works via one provider per agent CLI.
**With/without-skill A/B.** Natural: define two providers — same agent, one with the skill dir loaded (settings/plugin-dir/cwd), one bare — and every test row is scored against both, side by side.
**Live-backend fit.** Fine in principle (providers are arbitrary code, so they can run the real `crm` CLI against a live org), but promptfoo has no notion of per-test environment setup/teardown — you write hooks/glue for org seeding and cleanup, and parallel test rows against one shared live org need care.
**Adoption cost.** Low-moderate: `npx promptfoo eval`, no docker required, no hosted requirement (cloud/red-team products are optional).
**Verdict:** best third-party fit; picks up the cross-model matrix cheaply, at the cost of hand-rolled statefulness.

## 8. DIY: pytest + claude-agent-sdk

**What it is.** anthropics/claude-agent-sdk-python (MIT, very active — v0.2.121 released 2026-07-17; Claude Code CLI now bundled in the wheel). `query()` / `ClaudeSDKClient` run the full Claude Code agent loop programmatically; `ClaudeAgentOptions` controls `system_prompt`, `allowed_tools`/`disallowed_tools`, `permission_mode`, `cwd`, `max_turns`, `setting_sources`, hooks. Equivalent shell substrate: `claude -p` headless mode — `--allowedTools`, `--settings <file-or-json>`, `--append-system-prompt`, `--output-format json` (result JSON includes **`total_cost_usd`** and per-model cost breakdown), and structured output via `--json-schema`.
**The with/without-skill switch is first-class.** By default `claude -p` "loads the same context an interactive session would," including skills discovered in the working directory or `~/.claude`; **`--bare` skips auto-discovery of hooks, skills, plugins…** and is the documented mode for scripted/CI calls. So: baseline = `--bare` (or a cwd with no skill installed); treatment = load the skill via `--plugin-dir`, a prepared `.claude/skills/` cwd, or `crm skill install` into an isolated `CLAUDE_CONFIG_DIR`/home. In the Python SDK the same axis is `setting_sources`/`cwd`.
**Eval model.** Whatever pytest asserts: task success verified against the **live org** (query it back after the run), pass@k via repeated trials, wall time and `total_cost_usd` captured per run; an optional LLM-judge step can be one more SDK call. This mirrors Anthropic's "Demystifying evals" advice: 20–50 tasks from real failures, code graders on the produced artifact, read the transcripts.
**Adoption cost — lowest of all for THIS repo.** The hard 80% (live-org fixtures, profile wiring, setup/teardown, mutation-safe targets, an e2e coverage-gate culture) already exists in `crm/tests/e2e/` and the `live-e2e` skill. The new work is one pytest module that shells the agent (SDK or `claude -p`) at a task prompt twice (skill on/off) and asserts on org state — no docker, no new framework, no hosted service.
**Cross-model / cross-harness.** Models: any Claude model via the model option (sweep sonnet/haiku/opus). Harnesses: Claude Code only — though the identical pattern extends to `codex exec` as a second subprocess if a cross-harness datapoint is ever needed.
**Verdict:** the recommended primary approach.

## Other credible options (brief)

- **Braintrust** — hosted eval + observability platform; named in Anthropic's "Demystifying evals" tooling appendix ("offline + production observability"). Hosted dependency; SDK-level evals (scorers over your own task runner). Fine, but adds a SaaS for something pytest already covers here.
- **LangSmith / Langfuse** — same appendix ("LangChain integration" / "self-hosted alternative"); LangChain has a first-party blog post "Evaluating Skills" showing skill A/B evals on LangSmith. Both are tracing-first platforms: you still write the agent runner; they store/score/compare runs. Reasonable UI-for-results add-on, not a harness.
- **Harbor cookbook** (harbor-framework/harbor-cookbook) — worked examples for custom Harbor benchmarks, the fastest path into option 3 if it's ever chosen.

## Comparison table

| Option | Measures | Adoption cost (solo) | Cross-model | Cross-harness | Skill A/B | Live-D365 fit | Verdict |
|---|---|---|---|---|---|---|---|
| skill-creator eval/benchmark | assertion pass rate, time, tokens, with/without delta, blind comparator | ~zero (already installed) | Claude only | Claude Code only | **native** | OK (runs in real env; manual teardown; not CI-able) | Use for authoring-time iteration |
| SkillsBench (BenchFlow) | pass rate with vs without skills, docker verifier scripts | high (full task packages + docker) | yes (many models) | yes (OpenHands, Claude Code, Gemini CLI) | native | poor (container-first) | Reference design, wrong runtime |
| terminal-bench / Harbor | containerized task pass rate over trials | high (docker envs + verifiers) | yes (`-m` axis) | **best** (Claude Code, Codex, OpenHands, arbitrary) | manual (env variants) | poor-moderate | Only if cross-harness at scale becomes the goal |
| HAL | multi-benchmark accuracy + cost | n/a | yes | yes | no | n/a | **Archived 2026-07-01 — ruled out** |
| OpenAI Evals | registry evals, model-graded | moderate | OpenAI-centric | none | no | poor | Dormant since ~2024 — ruled out |
| inspect-ai (+inspect_swe) | task/solver/scorer; code + model-graded scorers | moderate-high (real framework) | broad | yes (claude_code(), codex_cli() via inspect_swe) | expressible | workable (local sandbox, arbitrary Python) | Serious alternative if suites grow |
| promptfoo | provider-matrix + assertions incl. trajectory + llm-rubric | low-moderate | **native matrix** | via providers (Agent SDK provider, exec CLI wrappers) | natural (two providers) | workable (glue for state) | Best third-party fit |
| DIY pytest + claude-agent-sdk / `claude -p` | anything pytest asserts; pass@k; cost via `total_cost_usd` | **lowest here** (reuses live-e2e infra) | Claude models only | Claude Code (extendable to `codex exec`) | first-class (`--bare` vs skill loaded) | **best** | **Recommended** |

## Sources

- SkillsBench repo: https://github.com/benchflow-ai/skillsbench (README, task structure, bench CLI; GitHub API: Apache-2.0, pushed 2026-07-16)
- SkillsBench site/leaderboard: https://skillsbench.ai (conditions, 25 model×harness configs, OpenHands/Claude Code/Gemini CLI, ~32.6% gain); paper: https://arxiv.org/abs/2602.12670
- skill-creator SKILL.md (eval/benchmark modes, dual-run, grader/comparator): https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md
- Anthropic blog — skill-creator evals: https://claude.com/blog/improving-skill-creator-test-measure-and-refine-agent-skills
- Anthropic engineering — Agent Skills: https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- Anthropic engineering — Demystifying evals for AI agents (task counts, graders, pass@k/pass^k, Harbor/Braintrust/LangSmith/Langfuse appendix): https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- Claude platform docs — skill best practices ("no built-in way to run these evaluations"): https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- Headless `claude -p` docs (`--bare` skips skill discovery, `--allowedTools`, `--settings`, `--plugin-dir`, `total_cost_usd` in JSON output): https://code.claude.com/docs/en/headless
- claude-agent-sdk-python (options, bundled CLI, v0.2.121 2026-07-17): https://github.com/anthropics/claude-agent-sdk-python
- terminal-bench (harbor-framework org, task format, "check out harbor… Terminal-Bench 2.0"): https://github.com/laude-institute/terminal-bench (redirects to harbor-framework/terminal-bench)
- Harbor (agents: Claude Code/OpenHands/Codex CLI; `harbor run -d -m -a`; custom benchmarks): https://github.com/harbor-framework/harbor ; docs: https://harborframework.com/docs ; cookbook: https://github.com/harbor-framework/harbor-cookbook
- HAL harness (archived 2026-07-01, read-only; cost-controlled evals; conda/docker/Azure): https://github.com/princeton-pli/hal-harness ; https://hal.cs.princeton.edu/about
- openai/evals (README; commit history: last substantive commit 2024-09-30, Dec 2024 README points to hosted Dashboard evals, 2025–26 housekeeping only — verified via GitHub commits API): https://github.com/openai/evals
- inspect_ai (MIT, UK AISI, agents/sandboxing/scorers): https://github.com/UKGovernmentBEIS/inspect_ai ; docs: https://inspect.aisi.org.uk/agents.html
- inspect_swe (Claude Code / Codex CLI / Gemini CLI as Inspect agents): https://meridianlabs-ai.github.io/inspect_swe/
- promptfoo (MIT, active): https://github.com/promptfoo/promptfoo ; Claude Agent SDK provider: https://www.promptfoo.dev/docs/providers/claude-agent-sdk/ ; coding-agents guide: https://www.promptfoo.dev/docs/guides/evaluate-coding-agents/ ; **Test Agent Skills guide**: https://www.promptfoo.dev/docs/guides/test-agent-skills/ ; model-graded metrics: https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/ ; providers: https://www.promptfoo.dev/docs/providers/
- LangChain blog — Evaluating Skills (LangSmith-based skill evals): https://www.langchain.com/blog/evaluating-skills
- GitHub API repo metadata (pushed_at / archived / license), retrieved 2026-07-17 for: openai/evals, benchflow-ai/skillsbench, harbor-framework/{terminal-bench,harbor}, UKGovernmentBEIS/inspect_ai, promptfoo/promptfoo, princeton-pli/hal-harness, anthropics/claude-agent-sdk-python
