"""Ban `#` comments in Python, with a per-file ratchet for the ones that predate the ban.

    python scripts/check_comments.py FILE...   check the files against the baseline
    python scripts/check_comments.py --update  lower the baseline to today's counts
    python scripts/check_comments.py --list FILE...  print each comment as path:line: text

A file may hold at most its baseline count of comments (0 when it has no entry).
A count below the baseline fails too, so a deletion is locked in by `--update`
and nobody spends the freed slack. `--update` never raises a count or adds a
file; raising one is a hand edit a reviewer sees. Tool directives (noqa, type:,
pyright:, pragma:, fmt:, nosemgrep, ...), a shebang and a coding cookie are not
comments. A non-obvious why goes in a docstring, or an ADR when it spans modules:
the `/delete-comment` skill says which.

Exit 0 when every file is within its baseline, 1 otherwise.
"""

import json
import re
import sys
import tokenize
from pathlib import Path

BASELINE = Path(__file__).resolve().parent.parent / "ci" / "comment-baseline.json"
UPDATE_HINT = "run `python scripts/check_comments.py --update`"
_DIRECTIVE = re.compile(
    r"#\s*(noqa|type:|pyright:|pragma:|fmt:|nosec|nosemgrep|ruff:|codespell:|isort:|mypy:|-\*-|!)"
)


def comments(path: Path) -> list[tuple[int, str]]:
    """Each comment in `path` that is not a tool directive, as (line number, text)."""
    with tokenize.open(path) as f:
        tokens = list(tokenize.generate_tokens(f.readline))
    return [
        (t.start[0], t.string)
        for t in tokens
        if t.type == tokenize.COMMENT and not _DIRECTIVE.match(t.string)
    ]


def comment_lines(path: Path) -> list[int]:
    """The line numbers of the comments in `path` that are not tool directives."""
    return [line for line, _ in comments(path)]


def check(files: list[Path], baseline: dict[str, int], *, root: Path) -> list[str]:
    """One error line per file whose comment count differs from what the baseline allows."""
    errors = [
        f"{name}: in the baseline but missing: {UPDATE_HINT}"
        for name in baseline
        if not (root / name).exists()
    ]
    for path in files:
        name = path.resolve().relative_to(root.resolve()).as_posix()
        lines = comment_lines(path)
        allowed = baseline.get(name, 0)
        if len(lines) > allowed:
            where = f" (lines {', '.join(map(str, lines))})" if allowed == 0 else ""
            errors.append(f"{name}: {len(lines)} comment(s), baseline allows {allowed}{where}")
        elif len(lines) < allowed:
            errors.append(
                f"{name}: {len(lines)} comment(s), baseline allows {allowed}: {UPDATE_HINT}"
            )
    return errors


def updated(baseline: dict[str, int], *, root: Path) -> dict[str, int]:
    """The baseline lowered to each file's current count, without files that are gone or clean."""
    counts = {
        name: min(n, len(comment_lines(root / name)))
        for name, n in baseline.items()
        if (root / name).exists()
    }
    return {name: n for name, n in sorted(counts.items()) if n}


def main(argv: list[str]) -> int:
    root = Path.cwd()
    baseline: dict[str, int] = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    if argv[:1] == ["--list"]:
        for name in argv[1:]:
            for line, text in comments(Path(name)):
                print(f"{name}:{line}: {text}")
        return 0
    if argv == ["--update"]:
        BASELINE.write_text(json.dumps(updated(baseline, root=root), indent=1) + "\n")
        return 0
    errors = check([Path(a) for a in argv if a.endswith(".py")], baseline, root=root)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
