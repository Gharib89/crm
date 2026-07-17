# Research: skillsbench.ai — what is concretely adoptable

Wayfinder ticket: [#876](https://github.com/Gharib89/crm/issues/876) (map: #874). Researched 2026-07-17 against primary sources (site, GitHub repos, PyPI, arXiv).

## TL;DR

SkillsBench is a real, runnable, Apache-2.0 benchmark — not just a leaderboard site. It measures how much Anthropic-style Agent Skills (`SKILL.md` + scripts/references) improve agent task performance: 87 tasks across 8 domains, each with a deterministic verifier, run in with-skill vs. no-skill conditions. The runnable artifact is the **BenchFlow** harness (`pip`/`uv` package `benchflow`, CLI `bench`) plus the task packages in the `benchflow-ai/skillsbench` git repo. **A third party can run a fully private skill + task set locally with no upstreaming** — `--tasks-dir` takes any local path and skills are injected at runtime via `--skills-dir`.

## Identity and provenance

- Benchmark by the **benchflow-ai** org; 87 tasks / 8 professional domains, each paired with curated skills and deterministic verifiers. ([skillsbench.ai](https://www.skillsbench.ai/), [arXiv:2602.12670](https://arxiv.org/abs/2602.12670))
- Paper: *"SkillsBench: Benchmarking How Well Agent Skills Work Across Diverse Tasks"*, arXiv:2602.12670 (v1 2026-02-13, v4 2026-06-14, ~77 authors, CC BY 4.0). Headline: curated skills raise average pass rate 33.9% → 50.5% (+16.6 pp); small focused skills (≤3 modules) beat exhaustive bundles.
- Two repos: tasks/benchmark at [github.com/benchflow-ai/skillsbench](https://github.com/benchflow-ai/skillsbench); the general-purpose runner at [github.com/benchflow-ai/benchflow](https://github.com/benchflow-ai/benchflow), published on PyPI as [`benchflow`](https://pypi.org/project/benchflow/).

## Marketing claims vs. what is runnable today

**Site claims** ([skillsbench.ai](https://www.skillsbench.ai/)): leaderboard of 25 agent–model configurations (GPT-5.5 + OpenHands leads at 67.3% with skills), normalized skill gains 4.9–38.7%, "+2.2 points/month" capability trend, ~47k skills screened during collection. The landing page itself has no run instructions.

**Actually downloadable/runnable — yes, fully** ([getting-started docs](https://www.skillsbench.ai/docs/getting-started)):

```bash
uv tool install benchflow          # PyPI benchflow 0.6.5 (2026-07-11), Python >=3.12
git clone https://github.com/benchflow-ai/skillsbench.git && cd skillsbench && uv sync --locked
bench tasks check tasks/<task-id>
bench eval run --tasks-dir tasks/<task-id> --agent oracle --sandbox docker
```

Prereqs: Docker, Python 3.12+, `uv`, model API keys as env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`; some tasks want ElevenLabs/GitHub/HuggingFace/Modal tokens). Credential-gated extra tasks live under `tasks-extra/` (`--no-default-excludes`).

## License

- **skillsbench repo (code + task data): Apache-2.0.** No separate data license found — one Apache-2.0 covers the repo including task packages and bundled skills. ([repo](https://github.com/benchflow-ai/skillsbench))
- **benchflow runner: Apache-2.0** ([repo](https://github.com/benchflow-ai/benchflow), [PyPI](https://pypi.org/project/benchflow/)). Paper: CC BY 4.0.
- *Not audited:* whether individual third-party-contributed skills inside task packages carry their own upstream licenses.

## Supported models and agent harnesses

- **Benchmarked harnesses** (leaderboard): OpenHands, Claude Code, Codex, Gemini CLI. Models: OpenAI GPT-5.x, Anthropic Opus 4.7/4.8 / Sonnet 4.6 / Haiku 4.5, Gemini 3.x, GLM 5.1, Kimi K2.6, DeepSeek V4, Grok 4.3, others. ([skillsbench.ai](https://www.skillsbench.ai/))
- **Runner adapters** (what `bench` can invoke): Gemini CLI, Claude Code, Codex, OpenCode, OpenHands, Pi, plus any custom agent speaking **ACP (Agent Client Protocol)**. The documented SkillsBench workflow uses `--agent claude-agent-acp`; `--agent oracle` runs the reference solution. Sandboxes: Docker (local), Daytona, Modal. ([benchflow README](https://github.com/benchflow-ai/benchflow), [CONTRIBUTING.md](https://github.com/benchflow-ai/skillsbench/blob/main/CONTRIBUTING.md))

## Task/eval format

Per [task-authoring docs](https://docs.benchflow.ai/task-authoring-task-md.md) and CONTRIBUTING.md:

```
tasks/<task-id>/
  task.md                      # YAML frontmatter (schema_version 1.3, strict) + markdown prompt
  environment/                 # Dockerfile + bundled inputs + skills/<skill-name>/ (SKILL.md, references/, scripts/)
  oracle/solve.sh              # reference solution, must score 1.0
  verifier/test.sh, test_outputs.py
```

- Frontmatter: task identity, metadata (author, difficulty + explanation, 1 of 8 controlled categories, task_type/modality/interface/skill_type vocab, tags), `agent.timeout_sec`, verifier type/timeout, environment (docker_image, network_mode, cpus, memory_mb, storage_mb). Unknown keys rejected.
- **Verifier contract:** `cd /verifier && ./test.sh` writes a float 0.0–1.0 to `/logs/verifier/reward.txt`. Strategies: `script`, `llm-judge` (needs rubric), `reward-kit`, `agent-judge`, `ors-episode`. SkillsBench policy: deterministic, outcome-based ("test the result, not the process"); oracle/verifier must not require paid API keys.
- **Skill attachment:** skills live in `environment/skills/<skill-name>/` and are *not* baked into the image — BenchFlow injects them at runtime under `--skill-mode with-skill --skills-dir ...`, making with-skill/no-skill runs symmetric over the same task package.

## Extensibility — private skill + task sets: yes, no upstreaming needed

Concrete recipe for a third party (e.g. benchmarking the `crm` skill against private D365 tasks):

1. `bench tasks init <name>` scaffolds a task; write `task.md`, `environment/Dockerfile`, your skill under `environment/skills/<skill-name>/`, `oracle/solve.sh`, `verifier/test.sh` (+ `test_outputs.py`).
2. `bench tasks check <path>` validates (`--level schema` for frontmatter-only).
3. `bench eval run --tasks-dir <private-dir> --agent oracle --sandbox docker` — oracle must score 1.0.
4. Paired comparison:
   `bench eval run --tasks-dir <private-dir> --agent claude-agent-acp --model <model> --skill-mode with-skill --skills-dir <dir>/environment/skills/` vs. `--skill-mode no-skill`.

`--tasks-dir` accepts any local path; BenchFlow also supports `--source-repo <org>/<repo> --source-path <dir>` and YAML config files (`bench eval run --config <yaml>`). The PR/CONTRIBUTING checklist only applies if you want a task on the public leaderboard. ([task-authoring docs](https://docs.benchflow.ai/task-authoring-task-md.md), [CONTRIBUTING.md](https://github.com/benchflow-ai/skillsbench/blob/main/CONTRIBUTING.md), [benchflow README](https://github.com/benchflow-ai/benchflow))

## Maintenance activity (as of 2026-07-17)

- **skillsbench**: last commit 2026-07-16 (daily activity, maintainer `bingran-you`); 452 commits, ~1.5k stars, 335 forks, 42 open issues, 56 open PRs; latest release v1.1 (2026-06-14), matching paper v4 and the [SkillsBench 1.1 blog post](https://www.skillsbench.ai/blogs/skillsbench-1-1).
- **benchflow**: v0.6.5 (2026-07-11); 1,428 commits, 289 stars, 7 open issues, 14 open PRs; PyPI maintainers `bingran-you`, `xdotli`.

## Not verified / caveats

- No PyPI/npm package named `skillsbench` exists or is claimed; the runnable artifact is `benchflow` (PyPI) + the git repo for tasks.
- `registry.json` in the skillsbench repo is linked from the site but its role is undocumented in the README (likely the leaderboard/task registry).
- Per-task skill licensing and exact ACP adapter mechanics (Claude Code adapter vs `claude-agent-acp`) were not inspected at code level.
- GitHub stats are page-render snapshots from 2026-07-17; commands were quoted from raw files (README, CONTRIBUTING) where possible.
