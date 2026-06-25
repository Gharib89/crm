# Skill effectiveness eval — Machine B (tracer)

Behavioral eval of the shipped `crm` skill (ADR 0015, governed by ADR 0009). It runs
a task through an agent that has **only the installed skill + the `crm` binary +
`gh`** — no repo, no `CLAUDE.md`, no memory — then scores the result with a
deterministic end-state predicate. Isolation is the validity keystone: if it leaks,
the eval measures the repo, not the skill.

This is the **tracer** (issue #570): the end-to-end skeleton on a single task. The
broader workflow-per-domain task set (#571), the optional Claude `--analyze` pass
(#572), and the both-targets baseline trend (#573) build on it.

This tree is **not shipped in the wheel** (excluded in `setup.py`) and is **not
collected by the default `pytest` suite** (`testpaths = crm/tests`), so it never
blocks CI. Run it on demand.

## Layout

- `tasks/*.md` — one task per file: YAML frontmatter (`id`, `domain`, `target`,
  `end_state`, `cleanup`) plus a markdown body that is the **verbatim prompt** fed to
  the agent.
- `taskspec.py` — task parsing and the pure end-state predicate (`evaluate_expect`).
- `isolation.py` — provisions and **verifies** the isolated agent context.
- `target.py` — live-target selection, reusing the e2e `D365_E2E_PROFILE` mechanism
  and the `D365_E2E_ALLOW_HOST` prod-host guard.
- `runner.py` — orchestrates isolate → verify → seed → agent → score → cleanup.
- `test_runner_smoke.py` — offline smoke tests (parse tasks + dry-run, no agent).

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
