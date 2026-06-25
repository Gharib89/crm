"""Skill-coverage gate helpers: parse `crm` citations out of the shipped skill
tree and reconcile them against the live CLI command catalogue.

The gate (``test_skill_coverage_gate.py``) enforces two **group-level** invariants
so the shipped skill (``crm/skills/``) can't drift out of sync with the CLI:

1. **Dead-reference** — every cited ``group verb`` command still exists.
2. **Group completeness** — every real top-level group is cited by the skill or
   explicitly :data:`WAIVED` with a reason.

Granularity is deliberately group-level: per ADR 0009 the skill states only what
``crm describe`` / ``--help`` cannot, so new verbs/flags under an already-routed
group need no new citation. The catalogue is obtained by reusing the e2e
``walk_commands`` walker — no new walker, no subprocess, no live org.
"""
# pyright: basic
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from crm.tests.e2e.coverage import walk_commands

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
SKILL_MD = SKILLS_DIR / "SKILL.md"
REFERENCE_DIR = SKILLS_DIR / "reference"

# A command / verb token: lowercase, may contain digits and hyphens (e.g.
# ``delete-entity``, ``self-update``). Placeholders (``<logical>``, ``[group]``),
# option flags (``--json``), and quoted/JSON args fail this, which is how the
# parser separates a real command path from its arguments.
_TOKEN = re.compile(r"[a-z][a-z0-9-]*\Z")

# Inline code spans: `...` (single backtick pairs; no nesting in this tree).
_INLINE_CODE = re.compile(r"`([^`]+)`")
# A fenced-code delimiter line (``` or ~~~, optionally with an info string).
_FENCE = re.compile(r"^\s*(```+|~~~+)")


def skill_files() -> list[Path]:
    """Every shipped skill markdown file (router + references)."""
    return [SKILL_MD, *sorted(REFERENCE_DIR.glob("*.md"))]


def _command_spans(text: str) -> list[str]:
    """Yield the raw text of every code construct that *could* be a citation:
    each inline-code span, plus each line inside a fenced code block. Prose and
    fence-delimiter lines are excluded; non-`crm` spans are filtered later.
    """
    spans: list[str] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            spans.append(line.strip())
        else:
            spans.extend(_INLINE_CODE.findall(line))
    return spans


def _parse_span(
    span: str, groups: frozenset[str], leaf_commands: frozenset[str]
) -> tuple[str | None, tuple[str, str] | None]:
    """Parse one code span into ``(group, pair)``.

    Returns ``(None, None)`` when the span is not a `crm <group>` citation.
    Otherwise ``group`` is the cited top-level name; ``pair`` is the
    ``(group, verb)`` tuple when a verb token follows a real group, else ``None``
    (a bare-group citation, or a single top-level command whose trailing tokens
    are arguments rather than sub-verbs).
    """
    tokens = span.split()
    if not tokens or tokens[0] != "crm":
        return None, None
    rest = tokens[1:]
    # Skip leading global options (`--json`, `--dry-run`, `-h`, …) that may sit
    # between `crm` and the group, so `crm --json connection whoami` still
    # anchors on `connection`.
    while rest and rest[0].startswith("-"):
        rest = rest[1:]
    if not rest:
        return None, None
    group = rest[0]
    if group not in groups and group not in leaf_commands:
        return None, None  # unknown first token → not a citation we anchor on
    pair = None
    # A verb is only meaningful under a multi-verb group; under a single
    # top-level command (`describe`, `apply`, …) the next token is an argument.
    if group in groups and len(rest) >= 2 and _TOKEN.match(rest[1]):
        pair = (group, rest[1])
    return group, pair


@lru_cache(maxsize=1)
def catalogue() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Reduce ``walk_commands`` into the sets the gate reconciles against.

    Cached: the in-process CLI tree is static, and ``parse_citations`` /
    the gate call this repeatedly — one lazy-loader walk suffices per run.

    Returns ``(top_level, groups, leaves)``:

    - ``top_level`` — every top-level name (groups + single top-level commands).
    - ``groups`` — top-level names that own sub-verbs (multi-token leaf paths).
    - ``leaves`` — full leaf command paths (``"metadata add-attribute"``).
    """
    leaves = frozenset(walk_commands())
    top_level = frozenset(p.split()[0] for p in leaves)
    groups = frozenset(p.split()[0] for p in leaves if len(p.split()) > 1)
    return top_level, groups, leaves


def parse_citations(
    files: list[Path] | None = None,
) -> tuple[set[str], set[tuple[str, str]]]:
    """Parse the skill tree into ``(cited_groups, cited_pairs)``.

    ``cited_groups`` is the set of top-level names cited anywhere; ``cited_pairs``
    is the set of ``(group, verb)`` commands cited under a real group.
    """
    top_level, groups, _ = catalogue()
    leaf_commands = top_level - groups
    cited_groups: set[str] = set()
    cited_pairs: set[tuple[str, str]] = set()
    for path in files if files is not None else skill_files():
        text = path.read_text(encoding="utf-8")
        for span in _command_spans(text):
            group, pair = _parse_span(span, groups, leaf_commands)
            if group is not None:
                cited_groups.add(group)
            if pair is not None:
                cited_pairs.add(pair)
    return cited_groups, cited_pairs


def dead_references(
    cited_pairs: set[tuple[str, str]], leaves: frozenset[str]
) -> set[str]:
    """Cited ``group verb`` commands that are absent from the catalogue."""
    return {f"{g} {v}" for g, v in cited_pairs if f"{g} {v}" not in leaves}


def unrouted_groups(
    cited_groups: set[str],
    groups: frozenset[str],
    waived: dict[str, str] | None = None,
) -> set[str]:
    """Real top-level *groups* neither cited by the skill nor explicitly waived.

    Scoped to multi-verb groups (the spec's "top-level CLI group"); bare
    top-level commands (`crm describe`, `crm doctor`, …) are out of scope —
    citing them is not required.
    """
    waived = WAIVED if waived is None else waived
    return {g for g in groups if g not in cited_groups and g not in waived}


# Top-level groups that legitimately carry no `crm <group>` citation in the
# shipped skill, each with the reason it is exempt from the completeness gate.
# Local/meta groups are set up by the router's prose or are out of the skill's
# Web-API scope; a stale-waiver check keeps every key a real group.
WAIVED: dict[str, str] = {
    "completion": "shell tab-completion installer; meta/local, not a Web-API workflow",
    "skill": "manages the agent skill itself (`crm skill install`); meta, not a Web-API workflow",
}
