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

``--analyze`` (off by default, #572) adds an analysis pass after scoring: it routes
``{task, transcript, final org state, programmatic verdict}`` to Claude for a
qualitative read of *why* a task passed/stumbled. It is also the only way to score
a **diagnostic** task — one whose spec declares no end-state predicate.

On-demand invocation:

    D365_E2E_PROFILE=agent-cloud D365_E2E_ALLOW_HOST=<host> \\
        CRM_EVAL_AGENT_CMD='claude -p --dangerously-skip-permissions' \\
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

from evals.skill import analyze, isolation, target
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
    #: Claude's qualitative read, when the ``--analyze`` pass ran (else None). For a
    #: diagnostic task it carries the only score; for a predicate task it explains
    #: *why* the deterministic verdict came out as it did.
    analysis: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _resolve_agent_cmd(agent_cmd: str | None) -> list[str]:
    raw = agent_cmd or os.environ.get("CRM_EVAL_AGENT_CMD", "").strip()
    if not raw:
        raise RunError(
            "no agent command configured: pass --agent-cmd or set CRM_EVAL_AGENT_CMD "
            "(recommended: 'claude -p --dangerously-skip-permissions' for headless Claude "
            "Code — the flag lets the agent execute tools without an interactive approval; "
            "it bypasses all approval, so run it only against a throwaway target). The agent "
            "reads the task prompt on stdin."
        )
    return shlex.split(raw)


def _crm_json(args: list[str], env: dict[str, str], crm_bin: str, cwd: str) -> Any:
    """Run ``crm --json <args>`` in the isolated env+cwd and return the ``data`` payload.

    ``cwd`` is the sandbox working dir so the CLI never runs from the repo root and
    can't pick up repo-relative effects — matching the isolation intent.
    """
    proc = subprocess.run(
        [crm_bin, "--json", *args], capture_output=True, text=True, env=env, cwd=cwd
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


def _cleanup_org(spec: TaskSpec, env: dict[str, str], profile: str, crm_bin: str, cwd: str) -> None:
    """Delete every record each cleanup step matches. Idempotent and best-effort:
    a per-step or per-record failure is logged and skipped so one failure can't
    strand the rest of the teardown (the org must be left as clean as possible)."""
    for step in spec.cleanup:
        try:
            rows = _crm_json(
                ["--profile", profile, "query", "odata", step.entity,
                 "--filter", step.filter, "--select", step.id_field],
                env, crm_bin, cwd,
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
                    env, crm_bin, cwd,
                )
            except RunError as exc:
                print(f"[cleanup] deleting {step.entity} {rec_id} failed: {exc}", file=sys.stderr)


def run_task(
    task_file: str | Path,
    *,
    dry_run: bool = False,
    agent_cmd: str | None = None,
    crm_bin: str | None = None,
    analyze_pass: bool = False,
    analyze_cmd: str | None = None,
) -> RunResult:
    """Run one task end-to-end (or up to isolation, when ``dry_run``).

    ``analyze_pass`` enables the optional Claude analysis pass (#572): after scoring
    it routes ``{task, transcript, org state, verdict}`` to the analyzer. It is the
    *only* score for a diagnostic task (no ``expect``), so such a task without the
    pass is refused up front rather than running an agent it cannot score.
    """
    spec = parse_task_file(task_file)
    if not dry_run and spec.is_diagnostic and not analyze_pass:
        raise RunError(
            f"task {spec.id!r} is diagnostic (no end-state predicate); pass --analyze "
            f"to score it via the Claude analysis pass"
        )
    resolved_bin = crm_bin or shutil.which("crm")
    if not resolved_bin:
        raise RunError("crm binary not on PATH")

    iso = isolation.provision_isolation(resolved_bin)
    try:
        checks = isolation.verify_isolation(iso)
        if dry_run:
            return RunResult(task_id=spec.id, dry_run=True, isolation_checks=checks)

        agent = _resolve_agent_cmd(agent_cmd)
        resolved_analyze = analyze.resolve_analyze_cmd(analyze_cmd) if analyze_pass else None
        profile = target.seed_target(iso.crm_home, spec.target)
        transcript = _run_agent(spec.prompt, agent, iso)
        work = str(iso.work)
        try:
            # Fetch the final org state (the scoring payload, and what the analyzer
            # reads). A diagnostic task may still declare a query for state alone.
            data = (
                _crm_json(["--profile", profile, *spec.query], iso.env, resolved_bin, work)
                if spec.query
                else None
            )
            if spec.expect:
                passed, reason = evaluate_expect(data, spec.expect)
            else:
                passed, reason = None, "diagnostic task: no programmatic predicate, scored by --analyze"
            analysis: str | None = None
            if resolved_analyze is not None:
                prompt = analyze.build_analysis_prompt(
                    task_prompt=spec.prompt,
                    transcript=transcript,
                    org_state=data,
                    verdict={"passed": passed, "reason": reason},
                )
                analysis = analyze.run_analysis(prompt, resolved_analyze)
                if spec.is_diagnostic:
                    # The analysis pass is this task's only score: turn the analyzer's
                    # PASS/FAIL verdict into the programmatic result. No parseable
                    # verdict leaves passed=None (unscored) — surfaced by the exit code.
                    passed = analyze.parse_verdict(analysis)
                    reason = (
                        f"diagnostic scored by --analyze: {'PASS' if passed else 'FAIL'}"
                        if passed is not None
                        else "diagnostic: analyzer returned no PASS/FAIL verdict"
                    )
        finally:
            _cleanup_org(spec, iso.env, profile, resolved_bin, work)
        return RunResult(
            task_id=spec.id, dry_run=False, isolation_checks=checks,
            passed=passed, reason=reason, transcript=transcript, analysis=analysis,
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
    parser.add_argument("--analyze", action="store_true",
                        help="route task+transcript+org-state+verdict to Claude for a "
                             "qualitative read (off by default); required to score a "
                             "diagnostic task that has no end-state predicate")
    parser.add_argument("--analyze-cmd", default=None,
                        help="analyzer command (default: $CRM_EVAL_ANALYZE_CMD, else 'claude -p')")
    args = parser.parse_args(argv)

    result = run_task(
        args.task_file, dry_run=args.dry_run, agent_cmd=args.agent_cmd,
        analyze_pass=args.analyze, analyze_cmd=args.analyze_cmd,
    )
    print(json.dumps(result.to_dict(), indent=2))
    # Exit non-zero on a scored failure. A diagnostic task scored via --analyze that
    # the analyzer failed or returned no verdict for (passed is None) is also a
    # non-success; only a dry run (unscored by design) is always exit 0.
    return 0 if result.dry_run or result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
