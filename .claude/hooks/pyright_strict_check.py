#!/usr/bin/env python3
"""Claude Code PostToolUse check: run pyright on edited strict-mode files.

After an Edit/Write to a pyright-strict source file, run pyright on JUST that
file and, if it reports errors, exit 2 so the diagnostics are fed back to the
agent to fix immediately (PostToolUse exit-2 contract). The edit has already
happened -- this surfaces type regressions at write time instead of at CI.

Strict surface (CLAUDE.md): `crm/core/*` and `crm/utils/d365_backend.py`. The
rest of the tree is basic mode, so it is skipped to stay fast and quiet.

Invocation mirrors the documented local lint: `--pythonpath .venv/bin/python`
(else ~56 false import errors) and `--pythonversion 3.9` (else 3.10+ symbols
mask real runtime ImportErrors). Missing venv/pyright -> pass through (exit 0):
a guardrail must never wedge editing when the toolchain is absent.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

BLOCK = 2


def _in_strict_scope(rel: str) -> bool:
    rel = rel.replace(os.sep, "/")
    if not rel.endswith(".py"):
        return False
    return rel.startswith("crm/core/") or rel == "crm/utils/d365_backend.py"


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (ValueError, TypeError):
        return 0
    if not isinstance(payload, dict):
        return 0

    file_path = (payload.get("tool_input") or {}).get("file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        return 0

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    abs_path = os.path.abspath(file_path)
    try:
        rel = os.path.relpath(abs_path, project_dir)
    except ValueError:
        return 0
    if not _in_strict_scope(rel):
        return 0

    pyright = os.path.join(project_dir, ".venv", "bin", "pyright")
    python = os.path.join(project_dir, ".venv", "bin", "python")
    if not (os.path.exists(pyright) and os.path.exists(python)):
        return 0  # toolchain absent -> never block editing

    try:
        proc = subprocess.run(
            [pyright, "--pythonpath", python, "--pythonversion", "3.9", rel],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0  # pyright unavailable / hung -> don't wedge the edit

    if proc.returncode == 0:
        return 0

    out = (proc.stdout or "") + (proc.stderr or "")
    sys.stderr.write(
        f"pyright (strict) reported errors in {rel} after your edit — fix before "
        f"continuing:\n{out.strip()}\n"
    )
    return BLOCK


if __name__ == "__main__":
    sys.exit(main())
