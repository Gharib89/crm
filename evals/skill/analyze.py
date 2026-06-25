"""Optional Claude analysis pass (issue #572, ADR 0015).

The deterministic end-state predicate (``taskspec.evaluate_expect``) stays *the*
gate. This pass is **off by default** and additive: when enabled it routes
``{task, transcript, final org state, programmatic verdict}`` to Claude for a
qualitative read — *why* a task failed or stumbled — and, for **diagnostic** tasks
that have no clean end-state predicate, it is the only available score.

Kept as a seam separate from the runner so the prompt assembly and command
resolution are unit-testable offline without invoking Claude or touching a live
org (mirroring how ``isolation``/``target`` isolate their concerns).

The analyzer is reached the same way as the agent under test: a configurable
command (``$CRM_EVAL_ANALYZE_CMD``, default ``claude -p`` for headless Claude
Code) that reads the composed prompt on **stdin**. Routing through a command — not
an in-process SDK — keeps the harness free of an API-client dependency and lets a
maintainer point the pass at any Claude entry point they already have wired.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Any

#: Default analyzer command when neither ``--analyze-cmd`` nor the env var is set.
#: ``claude -p`` runs headless Claude Code, reading the prompt on stdin.
DEFAULT_ANALYZE_CMD = "claude -p"


class AnalyzeError(RuntimeError):
    """Raised when the analysis pass cannot be run (bad command, analyzer failure)."""


def resolve_analyze_cmd(analyze_cmd: str | None = None) -> list[str]:
    """Resolve the analyzer command: explicit arg > ``$CRM_EVAL_ANALYZE_CMD`` > default.

    Unlike the agent command (which has no default — the eval is meaningless without
    a real agent), the analyzer defaults to ``claude -p`` because issue #572 routes
    specifically to Claude; a maintainer can still override it.
    """
    raw = (analyze_cmd or os.environ.get("CRM_EVAL_ANALYZE_CMD", "")).strip() or DEFAULT_ANALYZE_CMD
    parts = shlex.split(raw)
    if not parts:
        raise AnalyzeError(f"empty analyzer command: {raw!r}")
    return parts


def build_analysis_prompt(
    *,
    task_prompt: str,
    transcript: str,
    org_state: Any,
    verdict: dict[str, Any],
) -> str:
    """Compose the analysis prompt routed to Claude.

    Bundles the four inputs #572 calls for — the task the agent was given, the
    captured transcript, the final org state (the scoring query's payload, or
    ``None`` for a task that fetched none), and the programmatic verdict — and asks
    for a qualitative read plus, for diagnostic tasks, a PASS/FAIL judgment.
    """
    org_state_text = (
        json.dumps(org_state, indent=2, default=str) if org_state is not None else "(none captured)"
    )
    return (
        "You are scoring a behavioral eval of the `crm` CLI agent skill. Read the "
        "task the agent was given, the transcript of what it did, the final state of "
        "the org, and the deterministic verdict (if any). Explain *why* the task "
        "passed, failed, or stumbled. If the verdict is null this is a diagnostic "
        "task with no programmatic predicate — judge it yourself and state a clear "
        "PASS or FAIL with your reasoning.\n\n"
        "## Task prompt given to the agent\n"
        f"{task_prompt}\n\n"
        "## Agent transcript\n"
        f"{transcript}\n\n"
        "## Final org state (scoring query payload)\n"
        f"{org_state_text}\n\n"
        "## Programmatic verdict\n"
        f"{json.dumps(verdict, default=str)}\n"
    )


def run_analysis(prompt: str, analyze_cmd: list[str]) -> str:
    """Feed the composed prompt to the analyzer on stdin; return its analysis text.

    The analyzer's exit code is recorded in a header (mirroring the agent run) so a
    crashed or misconfigured analyzer is diagnosable rather than silently empty. A
    missing analyzer binary raises :class:`AnalyzeError` with the offending command.

    Unlike the agent under test (which runs in the scrubbed, fresh-HOME isolation
    sandbox), the analyzer runs in the operator's own environment: it is the
    *evaluator*, not the subject, and needs the operator's real HOME/credentials to
    reach Claude — so no ``env``/``cwd`` override is passed here, by design.
    """
    try:
        proc = subprocess.run(
            analyze_cmd, input=prompt, capture_output=True, text=True
        )
    except FileNotFoundError as exc:
        raise AnalyzeError(
            f"analyzer command not found: {analyze_cmd!r} — set CRM_EVAL_ANALYZE_CMD "
            f"or pass --analyze-cmd ({exc})"
        ) from exc
    header = f"[analyzer exit {proc.returncode}]\n"
    return header + proc.stdout + (f"\n[stderr]\n{proc.stderr}" if proc.stderr else "")
