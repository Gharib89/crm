"""The tracer runner: one task → isolated agent → programmatic score → cleanup.

End-to-end for a single task:

1. parse the task spec;
2. provision an isolated agent context and **verify isolation** (the priority — a
   leak invalidates the measurement, so this happens even on a dry run);
3. seed the live target into the throwaway ``CRM_HOME``;
4. feed the verbatim prompt to the agent and capture the transcript;
5. evaluate the deterministic end-state predicate (pass/fail);
6. run cleanup unconditionally, then tear down the sandbox.

``--dry-run`` stops after step 2 — it parses tasks and proves isolation without
invoking an agent or touching a live org. That is the path the offline smoke test
(and CI, which never runs an agent) exercises.

On-demand invocation:

    D365_E2E_PROFILE=agent-cloud D365_E2E_ALLOW_HOST=<host> \\
        CRM_EVAL_AGENT_CMD='claude -p' \\
        python -m evals.skill.runner evals/skill/tasks/records-create-verify.md
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from evals.skill import isolation, target
from evals.skill.taskspec import TaskSpec, evaluate_expect, parse_task_file


class RunError(RuntimeError):
    """Raised when a step the runner controls fails (not a task-scoring failure)."""


@dataclasses.dataclass
class RunResult:
    """Outcome of a single task run. ``passed`` is None on a dry run (not scored)."""

    task_id: str
    dry_run: bool
    isolation_checks: dict[str, str]
    passed: bool | None = None
    reason: str = ""
    transcript: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _resolve_agent_cmd(agent_cmd: str | None) -> list[str]:
    raw = agent_cmd or os.environ.get("CRM_EVAL_AGENT_CMD", "").strip()
    if not raw:
        raise RunError(
            "no agent command configured: pass --agent-cmd or set CRM_EVAL_AGENT_CMD "
            "(recommended: 'claude -p' for headless Claude Code). The agent reads the "
            "task prompt on stdin."
        )
    return shlex.split(raw)


def _crm_json(args: list[str], env: dict[str, str], crm_bin: str) -> Any:
    """Run ``crm --json <args>`` in the isolated env and return the ``data`` payload."""
    proc = subprocess.run(
        [crm_bin, "--json", *args], capture_output=True, text=True, env=env
    )
    if proc.returncode != 0:
        raise RunError(f"crm {' '.join(args)} failed (exit {proc.returncode}): {proc.stderr.strip()}")
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RunError(f"crm {' '.join(args)} returned non-JSON: {proc.stdout[:200]!r}") from exc
    return envelope.get("data")


def _run_agent(prompt: str, agent_cmd: list[str], iso: isolation.Isolation) -> str:
    """Feed the verbatim prompt to the agent in the isolated context; return transcript.

    The agent's exit code is recorded in the transcript header (the deterministic
    end-state predicate, not the exit code, is the pass/fail gate) so a crashed or
    misconfigured agent is diagnosable rather than scored as a silent failure.
    """
    proc = subprocess.run(
        agent_cmd,
        input=prompt,
        capture_output=True,
        text=True,
        cwd=str(iso.work),
        env=iso.env,
    )
    header = f"[agent exit {proc.returncode}]\n"
    return header + proc.stdout + (f"\n[stderr]\n{proc.stderr}" if proc.stderr else "")


def _cleanup_org(spec: TaskSpec, env: dict[str, str], profile: str, crm_bin: str) -> None:
    """Delete every record each cleanup step matches. Idempotent and best-effort:
    a per-step or per-record failure is logged and skipped so one failure can't
    strand the rest of the teardown (the org must be left as clean as possible)."""
    for step in spec.cleanup:
        try:
            rows = _crm_json(
                ["--profile", profile, "query", "odata", step.entity,
                 "--filter", step.filter, "--select", step.id_field],
                env, crm_bin,
            ) or []
        except RunError as exc:
            print(f"[cleanup] listing {step.entity} failed, skipping: {exc}", file=sys.stderr)
            continue
        for row in rows:
            rec_id = row.get(step.id_field)
            if not rec_id:
                continue
            try:
                _crm_json(
                    ["--profile", profile, "entity", "delete", step.entity, rec_id, "--yes"],
                    env, crm_bin,
                )
            except RunError as exc:
                print(f"[cleanup] deleting {step.entity} {rec_id} failed: {exc}", file=sys.stderr)


def run_task(
    task_file: str | Path,
    *,
    dry_run: bool = False,
    agent_cmd: str | None = None,
    crm_bin: str | None = None,
) -> RunResult:
    """Run one task end-to-end (or up to isolation, when ``dry_run``)."""
    spec = parse_task_file(task_file)
    resolved_bin = crm_bin or shutil.which("crm")
    if not resolved_bin:
        raise RunError("crm binary not on PATH")

    iso = isolation.provision_isolation(resolved_bin)
    try:
        checks = isolation.verify_isolation(iso)
        if dry_run:
            return RunResult(task_id=spec.id, dry_run=True, isolation_checks=checks)

        agent = _resolve_agent_cmd(agent_cmd)
        profile = target.seed_target(iso.crm_home, spec.target)
        transcript = _run_agent(spec.prompt, agent, iso)
        try:
            data = _crm_json(["--profile", profile, *spec.query], iso.env, resolved_bin)
            passed, reason = evaluate_expect(data, spec.expect)
        finally:
            _cleanup_org(spec, iso.env, profile, resolved_bin)
        return RunResult(
            task_id=spec.id, dry_run=False, isolation_checks=checks,
            passed=passed, reason=reason, transcript=transcript,
        )
    finally:
        iso.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one skill-eval task (tracer).")
    parser.add_argument("task_file", help="path to a tasks/*.md task spec")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse + prove isolation only; no agent, no live org")
    parser.add_argument("--agent-cmd", default=None,
                        help="agent command (default: $CRM_EVAL_AGENT_CMD)")
    args = parser.parse_args(argv)

    result = run_task(args.task_file, dry_run=args.dry_run, agent_cmd=args.agent_cmd)
    print(json.dumps(result.to_dict(), indent=2))
    # Exit non-zero only on a real scored failure; a dry run is informational.
    return 1 if result.passed is False else 0


if __name__ == "__main__":
    sys.exit(main())
