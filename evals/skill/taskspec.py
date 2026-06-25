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
    #: argv passed after ``crm --json`` to fetch the scoring/state payload (a list
    #: verb, so the result's ``data`` is a bare array of rows). Empty when the task
    #: declares no ``end_state`` at all.
    query: list[str]
    #: declared expectations over that ``data`` array (``count``, ``row``, and/or
    #: ``row_suffix``). Empty for a **diagnostic** task — one with no clean
    #: programmatic predicate, scored instead by the optional ``--analyze`` pass (#572).
    expect: dict[str, Any]
    cleanup: list[CleanupStep]

    @property
    def is_diagnostic(self) -> bool:
        """True when the task has no programmatic predicate (no ``expect``).

        A diagnostic task can only be scored by the ``--analyze`` pass; the runner
        refuses to run one without it.
        """
        return not self.expect


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

    # ``end_state`` is optional: a diagnostic task (#572) omits the programmatic
    # predicate and is scored by the ``--analyze`` pass instead. When present, a
    # non-empty ``query`` is required (it fetches the org state — used for scoring
    # and/or fed to the analyzer); ``expect`` is optional, and its absence marks the
    # task diagnostic (org state still flows to the analyzer, just nothing asserted).
    # A diagnostic task that needs no org-state query omits ``end_state`` entirely —
    # an empty query is rejected so it can't silently degrade scoring to NoneType.
    query: list[str] = []
    expect: dict[str, Any] = {}
    end_state = meta.get("end_state")
    if end_state is not None:
        if not isinstance(end_state, dict):
            raise ValueError(f"{path}: end_state must be a mapping")
        query = end_state.get("query")
        if not isinstance(query, list) or not query or not all(isinstance(a, str) for a in query):
            raise ValueError(
                f"{path}: end_state.query must be a non-empty list of strings "
                f"(omit end_state entirely for a diagnostic task that needs no org-state query)"
            )
        expect = end_state.get("expect") or {}
        if not isinstance(expect, dict):
            raise ValueError(f"{path}: end_state.expect must be a mapping")
        if "count" in expect and not isinstance(expect["count"], int):
            raise ValueError(f"{path}: end_state.expect.count must be an integer")
        if "row" in expect and not isinstance(expect["row"], dict):
            raise ValueError(f"{path}: end_state.expect.row must be a mapping")
        if "row_suffix" in expect and not isinstance(expect["row_suffix"], dict):
            raise ValueError(f"{path}: end_state.expect.row_suffix must be a mapping")

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

    ``count`` covers exact-cardinality end states (including the 50-row bulk load),
    ``row`` covers named-artifact end states, and ``row_suffix`` (added in #584)
    covers named-artifact end states whose logical name carries an org-varying
    publisher prefix:

    - ``count``: the ``data`` array has exactly this many rows;
    - ``row``: at least one row carries every ``field: value`` pair (string compare,
      so an absent key never matches);
    - ``row_suffix``: at least one row whose every ``field`` *ends with* the given
      string (string compare; an absent key never matches, even an empty suffix).
      Publisher-prefix-agnostic — a global option set named
      ``ag_maintenancepriority`` matches suffix ``maintenancepriority`` whatever the
      org's default publisher prefix is, so a correctly-created artifact isn't a false
      fail just because the prefix differs from the stock ``new_``.

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

    if "row_suffix" in expect:
        want_suffix: dict[str, Any] = expect["row_suffix"]
        # Require the key to be present (so an absent field never matches, even against
        # an empty suffix) and skip non-mapping rows so a stray scalar can't crash the
        # matcher — the endswith semantics are otherwise the suffix analogue of ``row``.
        if not any(
            isinstance(row, dict)
            and all(k in row and str(row[k]).endswith(str(v)) for k, v in want_suffix.items())
            for row in data
        ):
            return False, f"row_suffix: no row matched {want_suffix!r}"

    return True, "all expectations met"
