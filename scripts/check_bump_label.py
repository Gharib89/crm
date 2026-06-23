#!/usr/bin/env python3
"""Bump-guard: fail a PR whose Conventional-Commit title implies a *major* version
bump unless the maintainer-applied ``major`` label is present.

Release tooling (python-semantic-release) reads the squash-merge subject — the PR
title — to pick the bump: ``feat:`` -> minor, ``!``/``BREAKING CHANGE`` -> major,
everything else -> patch. A major bump must be a deliberate maintainer action,
never an agent's auto-bump (see ADR 0011, issues #398/#500), so this gate requires
an explicit ``major`` label to opt into it:

  * breaking title/body  -> requires the ``major`` label
  * ``feat:`` title       -> minor bump, no label required
  * everything else       -> patch, no label required

The minor digit is no longer label-gated: ``feat:`` -> minor flows freely so AFK
agents' feat PRs are not stalled waiting on a label. A title that is not a valid
Conventional Commit fails outright.

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
    """The label this PR must carry: ``"major"`` for a breaking change, or
    ``None`` otherwise (``feat:`` minor and patch-level bumps are not gated).
    Raises ``ValueError`` if the title is not a valid Conventional Commit."""
    m = _TITLE_RE.match(title.strip())
    if not m:
        raise ValueError(f"title {title!r} is not a valid Conventional Commit")
    bang = m.group(3)
    if bang or _BREAKING_RE.search(body or ""):
        return "major"
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
    if needed is None:
        return 0, "bump-guard: no major bump implied, no bump label required."
    have = {label.strip().lower() for label in labels if label.strip()}
    if "major" in have:
        return 0, "bump-guard: breaking title carries the 'major' label."
    return 1, (
        "bump-guard: this PR's title is a breaking change, which bumps the major "
        "version. A major bump must be opted in by a maintainer: add the 'major' "
        "label to confirm, or retitle it as a non-breaking change (feat:/fix:/...)."
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
