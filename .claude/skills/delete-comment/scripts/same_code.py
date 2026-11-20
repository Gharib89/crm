"""Prove a comment cleanup changed no code: each .py file the branch touched
exists at the merge base with origin/main and parses to the same tree there,
once comments and docstrings are set aside. An added, deleted, renamed or
untracked .py file fails the proof.

    python .claude/skills/delete-comment/scripts/same_code.py

Prints each file whose code differs and exits 1, else prints `code unchanged`.
"""

import ast
import subprocess
import sys
from pathlib import Path


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, encoding="utf-8", errors="replace", check=True
    ).stdout


def _is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def code_of(source: str) -> str:
    """The AST dump of `source` without the bare strings in module, class and function bodies."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            node.body = [s for s in node.body if not _is_docstring(s)] or [ast.Pass()]
    return ast.dump(tree)


def main() -> int:
    base = _git("merge-base", "origin/main", "HEAD").strip()
    rows = [
        row.split("\t") for row in _git("diff", "--name-status", base, "--", "*.py").splitlines()
    ]
    rows += [
        ["A", f] for f in _git("ls-files", "--others", "--exclude-standard", "--", "*.py").split()
    ]
    changed = [
        row[-1]
        for row in rows
        if row[0] != "M"
        or code_of(Path(row[1]).read_text(encoding="utf-8"))
        != code_of(_git("show", f"{base}:{row[1]}"))
    ]
    print("\n".join(changed) or "code unchanged")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
