"""Advisory blind L2 LLM judge — qualitative signal L1 can't reach (#894, ADR 0028).

The paired eval's L1 grader is deterministic and binary (org-state read / answer-key
match) and is the *sole* input to pass-rate, Hake gain, and regression verdicts. This
module adds the L2 judge the ADR keeps strictly **advisory**: it scores dimensions a
predicate can't — the **clarification quality** of the agent's questions and the
**elegance** of its solution — and its verdict is recorded on the trial record *alongside*
L1, never mixed into it.

Two invariants make it safe to keep in the loop:

- **Blind to condition.** The judge is handed only the task prompt and the run transcript,
  never which leg (with-skill / bare) produced it, and the prompt template names no
  condition — so a systematic pro-skill bias can't leak in. (The transcript itself may
  still betray a ``Skill`` tool call; blindness here is "not told, not asked to guess" —
  the bar the ADR sets — not transcript sanitisation.)
- **Isolated from L1.** The verdict lands in ``TrialRecord.judge`` — a field the
  aggregation, Hake-gain, reportability, and regression code never read — so lift stats
  and regression verdicts are provably unaffected (see ``test_judge`` / ``test_paired``).

The judge model and a pinned ``RUBRIC_VERSION`` are recorded with every verdict so a score
is reproducible and a rubric change is visible in the record. Like the agent-under-test and
the reviewer, the live judge is a ``claude -p`` subprocess resolved from ``--judge-cmd`` >
``$CRM_EVAL_JUDGE_CMD`` > a default; the pure prompt/parse functions are unit-tested with a
fake invoker (the one seam), so the whole path is exercised offline with no live model.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Callable
from typing import Any

#: Bumped whenever the rubric/prompt changes, so an old score is never silently compared to
#: a new rubric. Recorded on every verdict.
RUBRIC_VERSION = "1"

#: Default judge command. Opus — scoring clarification/elegance is a judgment task, not the
#: cheap tier the agent-under-test runs on.
DEFAULT_JUDGE_CMD = "claude -p --model opus"

#: The advisory dimensions L1 can't reach (ADR 0028). Each scored 1–5 with a one-line note.
DIMENSIONS = ("clarification_quality", "elegance")

_SCORE_MIN, _SCORE_MAX = 1, 5

#: Wall-clock cap (seconds) on one judge subprocess. The judge is advisory, so a stalled
#: judge command must never hang the whole eval run — on overrun it fails closed (recorded
#: as an error verdict), never blocks the trial.
JUDGE_TIMEOUT_S = 300

#: A raw-text invoker: takes a composed prompt, returns the judge's raw text. The
#: offline-testable seam — tests pass a fake, the default is a ``claude -p`` subprocess.
JudgeCmd = Callable[[str], str]

#: A blind judge: ``(task_prompt, transcript) -> verdict dict``. Never receives the leg.
Judge = Callable[[str, str], dict[str, Any]]


class JudgeError(RuntimeError):
    """Raised when the judge cannot run or its output cannot be parsed."""


def resolve_judge_cmd(judge_cmd: str | None = None) -> list[str]:
    """Resolve the judge command: explicit arg > ``$CRM_EVAL_JUDGE_CMD`` > default."""
    raw = (judge_cmd or os.environ.get("CRM_EVAL_JUDGE_CMD", "")).strip() or DEFAULT_JUDGE_CMD
    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        # Malformed command (e.g. unbalanced quotes) — rethrow as JudgeError so command
        # resolution follows the module's error contract instead of leaking a bare ValueError.
        raise JudgeError(f"malformed judge command {raw!r}: {exc}") from exc
    if not parts:
        raise JudgeError(f"empty judge command: {raw!r}")
    return parts


def judge_model(judge_cmd: list[str]) -> str:
    """The model id recorded on a verdict: the token after ``--model``, else argv[0].

    A judge command bakes the model into a ``--model <id>`` flag (like the agent/reviewer);
    surfacing it as its own field keeps a score honest about *which* model produced it,
    rather than only the full command string.
    """
    for i, tok in enumerate(judge_cmd):
        if tok == "--model" and i + 1 < len(judge_cmd):
            return judge_cmd[i + 1]
    return judge_cmd[0]


def build_judge_prompt(prompt: str, transcript: str) -> str:
    """Compose the judge prompt — blind to condition by construction.

    Carries only the task prompt and the run transcript; it never states, and never asks
    the judge to guess, whether the crm skill was installed. Requests a strict-JSON verdict
    scoring each dimension 1–5 with a one-line note.
    """
    return (
        "You are grading how well an AI agent handled a Microsoft Dynamics 365 task, from "
        "its transcript. This is an ADVISORY qualitative read — you are NOT deciding "
        "pass/fail (a separate deterministic check owns that), so judge only the quality "
        "below.\n\n"
        "Score two dimensions a pass/fail check can't capture, each 1 (poor) to 5 "
        "(excellent):\n"
        "- clarification_quality: when the ask was ambiguous or risky, did the agent ask "
        "the right clarifying question (or sensibly proceed on a stated assumption) rather "
        "than guess blindly?\n"
        "- elegance: was the solution direct and well-formed, without trial-and-error, "
        "flailing, or needless steps?\n\n"
        f"## Task prompt\n{prompt}\n\n"
        f"## Agent transcript\n{transcript}\n\n"
        "Reply with ONLY this JSON, no prose:\n"
        "```json\n"
        "{\n"
        '  "clarification_quality": {"score": 1-5, "note": "<one line>"},\n'
        '  "elegance": {"score": 1-5, "note": "<one line>"}\n'
        "}\n"
        "```\n"
    )


def _extract_json(text: str) -> Any:
    """The JSON object spanning the first ``{`` to the last ``}`` (handles a ```json fence
    or surrounding prose), or ``None`` if that span doesn't parse.
    """
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def parse_judgment(text: str) -> dict[str, Any]:
    """Parse + validate the judge's structured output; raise on a malformed verdict.

    Each dimension must carry an integer ``score`` within 1–5. A judge that drifts off-shape
    raises :class:`JudgeError` so the verdict is recorded as an error rather than stored as
    a score that isn't one. Returns just the per-dimension scores; the caller stamps the
    rubric version and model.
    """
    obj = _extract_json(text)
    if not isinstance(obj, dict):
        raise JudgeError("no JSON object found in judge output")
    scores: dict[str, Any] = {}
    for dim in DIMENSIONS:
        d = obj.get(dim)
        if not isinstance(d, dict):
            raise JudgeError(f"judgment missing dimension {dim!r}")
        score = d.get("score")
        # bool is an int subclass — reject it so `True` can't masquerade as score 1.
        if (
            isinstance(score, bool)
            or not isinstance(score, int)
            or not _SCORE_MIN <= score <= _SCORE_MAX
        ):
            raise JudgeError(f"dimension {dim!r} needs an int score in {_SCORE_MIN}–{_SCORE_MAX}")
        scores[dim] = {"score": score, "note": str(d.get("note", ""))}
    return scores


def run_judge(prompt: str, judge_cmd: list[str]) -> str:  # pragma: no cover - live model call
    """Feed the prompt to the judge command on stdin; return its text.

    Like the reviewer (and unlike the agent under test), the judge is the *evaluator*, so it
    runs in the operator's own env (real HOME/credentials to reach Claude). A missing binary,
    a non-zero exit, or a :data:`JUDGE_TIMEOUT_S` overrun raises :class:`JudgeError` — which
    :func:`make_judge` records as an advisory error verdict, so a stalled judge never hangs
    the run.
    """
    try:
        # encoding/errors pinned (not bare text=True): a model transcript can carry a stray
        # non-UTF-8 byte, and the locale default would crash decoding it (coding-standards
        # §Encoding). The legacy review.run_reviewer shares this gap; fixed here for new code.
        # timeout bounds a stalled judge — advisory means it must fail closed, never block.
        proc = subprocess.run(
            judge_cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=JUDGE_TIMEOUT_S,
        )
    except FileNotFoundError as exc:
        raise JudgeError(
            f"judge command not found: {judge_cmd!r} — set CRM_EVAL_JUDGE_CMD or pass "
            f"--judge-cmd ({exc})"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise JudgeError(f"judge {judge_cmd!r} timed out after {JUDGE_TIMEOUT_S}s") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise JudgeError(f"judge {judge_cmd!r} exited {proc.returncode}: {detail[:500]}")
    return proc.stdout


def make_judge(judge_cmd: str | None = None, *, run_cmd: JudgeCmd | None = None) -> Judge:
    """Build a blind judge closure: ``(prompt, transcript) -> verdict dict``.

    ``run_cmd`` is the raw-text invoker (default: a :func:`run_judge` ``claude -p``
    subprocess); injectable so tests drive the whole build/parse/stamp path with a fake and
    no live model. The returned verdict is what lands on the trial record: the pinned
    ``rubric_version``, the resolved judge ``model``, and the per-dimension ``scores``. A
    judge that errors or returns unparseable output yields a ``{..., "error": ...}`` verdict
    (scores omitted) rather than raising — the judge is advisory, so it must never fail a
    trial.
    """
    cmd = resolve_judge_cmd(judge_cmd)
    invoke = run_cmd if run_cmd is not None else (lambda p: run_judge(p, cmd))

    def _judge(prompt: str, transcript: str) -> dict[str, Any]:
        verdict: dict[str, Any] = {"rubric_version": RUBRIC_VERSION, "model": judge_model(cmd)}
        try:
            verdict["scores"] = parse_judgment(invoke(build_judge_prompt(prompt, transcript)))
        except Exception as exc:  # noqa: BLE001 — advisory: ANY invoker/parse failure is contained
            # Deliberately broad (Exception, not BaseException): the judge must never fail a
            # trial, so an unexpected error from a custom invoker is recorded, not propagated.
            verdict["error"] = str(exc)
        return verdict

    return _judge
