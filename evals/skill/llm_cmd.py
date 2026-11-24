"""Shared stdin-prompt subprocess call for the evaluator commands (judge and reviewer).

Both evaluators feed a composed prompt to an operator-configured command (``claude -p`` by
default) and read its stdout. They share one failure contract, so it lives here once.
"""

from __future__ import annotations

import subprocess


def run_llm_cmd[E: Exception](
    cmd: list[str],
    prompt: str,
    *,
    role: str,
    timeout_s: int,
    error: type[E],
    hint: str,
) -> str:
    """Feed ``prompt`` to ``cmd`` on stdin and return its stdout.

    A missing binary, a non-zero exit or a ``timeout_s`` overrun raises ``error`` naming
    ``role`` (``"judge"``, ``"reviewer"``) and the command; ``hint`` says how to point at a
    different command. ``encoding``/``errors`` are pinned rather than bare ``text=True``: a
    model transcript can carry a stray non-UTF-8 byte, and the locale codec (cp1252 on
    Windows) would crash decoding it (coding-standards §Encoding). The ``timeout`` bounds a
    stalled command so it can never hang an eval run.
    """
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise error(f"{role} command not found: {cmd!r} — {hint} ({exc})") from exc
    except subprocess.TimeoutExpired as exc:
        raise error(f"{role} {cmd!r} timed out after {timeout_s}s") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise error(f"{role} {cmd!r} exited {proc.returncode}: {detail[:500]}")
    return proc.stdout
