#!/usr/bin/env python3
"""Bump-guard: fail a PR whose Conventional-Commit title implies a version bump
larger than patch unless the matching opt-in label is present.

Release tooling (python-semantic-release) reads the squash-merge subject — the PR
title — to pick the bump: ``feat:`` -> minor, ``!``/``BREAKING CHANGE`` -> major,
everything else -> patch. To keep the minor digit reserved for real features
(see ADR 0011, issue #398), this gate requires an explicit maintainer-applied
label to opt into a non-patch bump:

  * ``feat:`` title      -> requires the ``minor`` label (``major`` also accepted)
  * breaking title/body  -> requires the ``major`` label

Patch-level titles (fix/perf/docs/chore/refactor/test/build/ci/style/revert)
need no label. A title that is not a valid Conventional Commit fails outright.

Inputs come from the environment (set by the workflow):
  PR_TITLE   - the pull request title
  PR_BODY    - the pull request body (scanned for a BREAKING CHANGE footer)
  PR_LABELS  - the PR's labels, comma / whitespace / newline separated
"""
import os
import re
import sys
from typing import List, Optional, Tuple

# A Conventional-Commit subject: type, optional (scope), optional !, ": ", text.
_TITLE_RE = re.compile(r"^([a-z]+)(\([^)]+\))?(!)?:\s+\S")
# A BREAKING CHANGE footer (PSR treats either spelling as a major bump).
_BREAKING_RE = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)


def required_label(title: str, body: str = "") -> Optional[str]:
    """The label this PR must carry: ``"minor"`` for a feat, ``"major"`` for a
    breaking change, or ``None`` when it is patch-level. Raises ``ValueError``
    if the title is not a valid Conventional Commit."""
    m = _TITLE_RE.match(title.strip())
    if not m:
        raise ValueError(f"title {title!r} is not a valid Conventional Commit")
    type_, bang = m.group(1), m.group(3)
    if bang or _BREAKING_RE.search(body or ""):
        return "major"
    if type_ == "feat":
        return "minor"
    return None


def check(title: str, body: str, labels: List[str]) -> Tuple[int, str]:
    """Return ``(exit_code, message)``: 0 if the PR is allowed, 1 otherwise."""
    try:
        needed = required_label(title, body)
    except ValueError as exc:
        return 1, (
            f"bump-guard: {exc}. PR titles must be Conventional Commits "
            "(e.g. 'fix: ...', 'feat: ...') because the squash subject drives "
            "the release version bump."
        )
    have = {label.strip().lower() for label in labels if label.strip()}
    if needed is None:
        return 0, "bump-guard: patch-level title, no bump label required."
    if needed == "minor" and ("minor" in have or "major" in have):
        return 0, "bump-guard: feat title carries the 'minor' label."
    if needed == "major" and "major" in have:
        return 0, "bump-guard: breaking title carries the 'major' label."
    kind = "breaking change" if needed == "major" else "feat"
    return 1, (
        f"bump-guard: this PR's title is a {kind}, which bumps the {needed} "
        f"version. Add the '{needed}' label to confirm the {needed} bump is "
        "intended, or retitle it as a patch-level change (fix:/perf:/...)."
    )


def _split_labels(raw: str) -> List[str]:
    return [part for part in re.split(r"[,\n]+", raw) if part.strip()]


def main() -> int:
    title = os.environ.get("PR_TITLE", "")
    body = os.environ.get("PR_BODY", "")
    labels = _split_labels(os.environ.get("PR_LABELS", ""))
    code, message = check(title, body, labels)
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
