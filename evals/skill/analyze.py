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
import re
import shlex
import subprocess
from typing import Any

#: Default analyzer command when neither ``--analyze-cmd`` nor the env var is set.
#: ``claude -p`` runs headless Claude Code, reading the prompt on stdin.
DEFAULT_ANALYZE_CMD = "claude -p"

#: A diagnostic task's analysis must contain a line of exactly this form so the
#: runner can turn the qualitative read into a pass/fail score (the analysis pass is
#: that task's only score). Anchored to a whole line so prose mentioning the word
#: "verdict" can't be mistaken for the verdict itself.
_VERDICT_RE = re.compile(r"^\s*VERDICT:\s*(PASS|FAIL)\s*$", re.IGNORECASE | re.MULTILINE)


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
        "task with no programmatic predicate — judge it yourself.\n\n"
        "## Task prompt given to the agent\n"
        f"{task_prompt}\n\n"
        "## Agent transcript\n"
        f"{transcript}\n\n"
        "## Final org state (scoring query payload)\n"
        f"{org_state_text}\n\n"
        "## Programmatic verdict\n"
        f"{json.dumps(verdict, default=str)}\n\n"
        "## Your verdict\n"
        "After your reasoning, finish with a final line that is exactly one of\n"
        "    VERDICT: PASS\n"
        "    VERDICT: FAIL\n"
        "and nothing after it, so the run can be scored.\n"
    )


def run_analysis(prompt: str, analyze_cmd: list[str]) -> str:
    """Feed the composed prompt to the analyzer on stdin; return its analysis text.

    A non-zero analyzer exit is raised as :class:`AnalyzeError`, not returned — for a
    diagnostic task the analysis pass is the *only* score, so a silently failed
    analyzer must not look like a successful (and unscoreable) read. A missing
    analyzer binary raises likewise, naming the offending command.

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
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise AnalyzeError(f"analyzer {analyze_cmd!r} exited {proc.returncode}: {detail[:500]}")
    return proc.stdout


def parse_verdict(analysis: str) -> bool | None:
    """Extract the analyzer's PASS/FAIL verdict from its analysis text.

    Returns True for PASS, False for FAIL, or ``None`` when the analyzer emitted no
    parseable ``VERDICT:`` line (the run then has no score). The last verdict line
    wins, so a stray earlier mention can't pre-empt the analyzer's final call.
    """
    matches = _VERDICT_RE.findall(analysis)
    if not matches:
        return None
    return matches[-1].upper() == "PASS"
