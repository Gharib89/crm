"""Prove a comment cleanup changed no code: each modified .py file parses to the
same tree as at the merge base with origin/main, once comments and docstrings
are set aside.

    python .claude/skills/delete-comment/scripts/same_code.py

Prints each file whose code differs and exits 1, else prints `code unchanged`.
"""

import ast
import subprocess
import sys


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


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
    files = _git("diff", "--name-only", "--diff-filter=M", base, "--", "*.py").split()
    changed = [
        f
        for f in files
        if code_of(open(f, encoding="utf-8").read()) != code_of(_git("show", f"{base}:{f}"))
    ]
    print("\n".join(changed) or "code unchanged")
    return 1 if changed else 0


if __name__ == "__main__":
    sys.exit(main())
