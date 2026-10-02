#!/usr/bin/env python3
"""Claude Code PostToolUse check: run pyright on edited strict-mode files.

After an Edit/Write to a pyright-strict source file, run pyright on JUST that
file and, if it reports errors, exit 2 so the diagnostics are fed back to the
agent to fix immediately (PostToolUse exit-2 contract). The edit has already
happened -- this surfaces type regressions at write time instead of at CI.

Strict surface (CLAUDE.md): `crm/core/*` and `crm/utils/d365_backend.py`. The
rest of the tree is basic mode, so it is skipped to stay fast and quiet.

Scope is relative to the git toplevel of the edited file's own directory, not
CLAUDE_PROJECT_DIR: a session started in the main checkout edits sibling
worktrees, whose paths sit outside the project dir.

Invocation mirrors the documented local lint: `--pythonpath .venv/bin/python`
(else ~56 false import errors) and `--pythonversion 3.13`, the python_requires
floor (else newer symbols mask real runtime ImportErrors). The venv is the
root's own, else the main checkout's (the parent of the git common dir), the
same fallback scripts/check.sh and scripts/local-gate.sh use. Missing venv/npx
-> pass through (exit 0): a guardrail must never wedge editing when the
toolchain is absent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

BLOCK = 2


def _in_strict_scope(rel: str) -> bool:
    rel = rel.replace(os.sep, "/")
    if not rel.endswith(".py"):
        return False
    return rel.startswith("crm/core/") or rel == "crm/utils/d365_backend.py"


def _git(cwd: str, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


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

    abs_path = os.path.abspath(file_path)
    root = _git(os.path.dirname(abs_path), "rev-parse", "--show-toplevel")
    if root is None:
        return 0
    try:
        rel = os.path.relpath(abs_path, root)
    except ValueError:
        return 0
    if not _in_strict_scope(rel):
        return 0

    # Microsoft's npm pyright at the repo's one pinned version (setup.py [dev] comment).
    npx = shutil.which("npx")
    main_checkout = os.path.dirname(
        os.path.join(root, _git(root, "rev-parse", "--git-common-dir") or ".git")
    )
    candidates = (os.path.join(d, ".venv", "bin", "python") for d in (root, main_checkout))
    python = next((p for p in candidates if os.path.exists(p)), None)
    if not (npx and python):
        return 0  # toolchain absent -> never block editing

    try:
        proc = subprocess.run(
            [
                npx,
                "--yes",
                "--package=pyright@1.1.414",
                "pyright",
                "--pythonpath",
                python,
                "--pythonversion",
                "3.13",
                rel,
            ],
            cwd=root,
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
