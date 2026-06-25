"""Task-spec parsing and the deterministic end-state predicate.

A task is a markdown file with a YAML frontmatter block (structured fields) and a
body (the verbatim prompt fed to the isolated agent). The frontmatter declares the
end-state predicate and the cleanup steps; the body is the prompt, untouched.

Predicate evaluation is kept pure here — `evaluate_expect` scores an already-fetched
`data` payload against the declared `expect` — so it is unit-testable without a live
org. The runner owns actually running the `crm` query that produces `data`.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml

#: Allowed values for a task's ``target`` gate.
TARGETS = ("cloud", "onprem", "either")


@dataclasses.dataclass(frozen=True)
class CleanupStep:
    """Delete every ``entity`` row whose ``id_field`` matches ``filter``.

    Idempotent by construction: when the filter matches nothing the runner deletes
    nothing. Cleanup runs after scoring, pass or fail, so a live org is never left
    polluted across runs.
    """

    entity: str
    id_field: str
    filter: str


@dataclasses.dataclass(frozen=True)
class TaskSpec:
    """One behavioral-eval task parsed from a ``tasks/*.md`` file."""

    id: str
    domain: str
    target: str
    prompt: str
    #: argv passed after ``crm --json`` to fetch the scoring payload (a list verb,
    #: so the result's ``data`` is a bare array of rows).
    query: list[str]
    #: declared expectations over that ``data`` array (``count`` and/or ``row``).
    expect: dict[str, Any]
    cleanup: list[CleanupStep]


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return ``(frontmatter_yaml, body)`` for a ``---``-delimited markdown file.

    The body has only the whitespace introduced by the frontmatter delimiter
    stripped (the blank line after the closing ``---`` and trailing newline); the
    authored prompt content between is preserved.
    """
    if not text.startswith("---"):
        raise ValueError("task file must open with a '---' YAML frontmatter block")
    # Split into: '', frontmatter, body — on the first two '---' fences.
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("task file frontmatter is not closed with a second '---'")
    # strip("\n") drops only the delimiter-introduced newlines (the blank line after
    # the closing '---' and the trailing newline); spaces/indentation in the authored
    # prompt are preserved, so the body stays verbatim.
    return parts[1], parts[2].strip("\n")


def parse_task_file(path: str | Path) -> TaskSpec:
    """Parse a ``tasks/*.md`` task file into a :class:`TaskSpec`.

    Raises ``ValueError`` with a path-prefixed message on any malformed field, so a
    bad task file fails the smoke test loudly rather than at run time against a live
    org.
    """
    path = Path(path)
    front, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    meta = yaml.safe_load(front) or {}
    if not isinstance(meta, dict):
        raise ValueError(f"{path}: frontmatter must be a YAML mapping, got {type(meta).__name__}")

    def require(key: str) -> Any:
        if key not in meta:
            raise ValueError(f"{path}: missing required field {key!r}")
        return meta[key]

    target = require("target")
    if target not in TARGETS:
        raise ValueError(f"{path}: target {target!r} not one of {TARGETS}")

    end_state = require("end_state")
    if not isinstance(end_state, dict):
        raise ValueError(f"{path}: end_state must be a mapping")
    query = end_state.get("query")
    if not isinstance(query, list) or not all(isinstance(a, str) for a in query):
        raise ValueError(f"{path}: end_state.query must be a list of strings")
    expect = end_state.get("expect")
    if not isinstance(expect, dict) or not expect:
        raise ValueError(f"{path}: end_state.expect must be a non-empty mapping")
    if "count" in expect and not isinstance(expect["count"], int):
        raise ValueError(f"{path}: end_state.expect.count must be an integer")
    if "row" in expect and not isinstance(expect["row"], dict):
        raise ValueError(f"{path}: end_state.expect.row must be a mapping")

    raw_cleanup = require("cleanup") or []
    if not isinstance(raw_cleanup, list):
        raise ValueError(f"{path}: cleanup must be a list of steps")
    cleanup: list[CleanupStep] = []
    for step in raw_cleanup:
        if not isinstance(step, dict) or not {"entity", "id_field", "filter"} <= step.keys():
            raise ValueError(
                f"{path}: each cleanup step needs entity/id_field/filter, got {step!r}"
            )
        cleanup.append(
            CleanupStep(entity=step["entity"], id_field=step["id_field"], filter=step["filter"])
        )

    if not body.strip():
        raise ValueError(f"{path}: empty prompt body")

    return TaskSpec(
        id=require("id"),
        domain=require("domain"),
        target=target,
        prompt=body,
        query=query,
        expect=expect,
        cleanup=cleanup,
    )


def evaluate_expect(data: Any, expect: dict[str, Any]) -> tuple[bool, str]:
    """Score a query's ``data`` payload against a declared ``expect`` mapping.

    Tracer-scope matchers (extended by #571 as the task set grows):

    - ``count``: the ``data`` array has exactly this many rows;
    - ``row``: at least one row carries every ``field: value`` pair (string compare,
      so an absent key never matches).

    Returns ``(passed, reason)``; ``reason`` explains the first failing matcher so a
    failed run is self-describing.
    """
    if not isinstance(data, list):
        return False, f"expected a list of rows in data, got {type(data).__name__}"

    if "count" in expect:
        want = expect["count"]
        if len(data) != want:
            return False, f"count: expected {want} row(s), got {len(data)}"

    if "row" in expect:
        want_row: dict[str, Any] = expect["row"]
        if not any(
            all(str(row.get(k)) == str(v) for k, v in want_row.items()) for row in data
        ):
            return False, f"row: no row matched {want_row!r}"

    return True, "all expectations met"
