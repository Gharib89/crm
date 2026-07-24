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

#: Allowed values for a task's ``kind`` — the second half of the ADR-0028 double tag.
#: ``do`` mutates the org and is graded on org state; ``feasibility`` mutates nothing
#: and is graded field-by-field against an evidenced answer key (#891).
KINDS = ("do", "feasibility")

#: Allowed ``source.type`` values — the channel a corpus task was harvested from
#: (ADR 0028 real-demand sourcing). ``firsthand`` is a traceable secondary (this
#: repo's issues / DISCOVERED_BUGS) and may carry a null ``url``.
SOURCE_TYPES = ("so", "forum", "reddit", "repo", "firsthand")


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
    #: ``"do"`` (default) or ``"feasibility"`` (#891). A feasibility task grades the
    #: agent's structured output against ``answer_key`` instead of fetching org state.
    kind: str = "do"
    #: For a ``feasibility`` task: the evidenced answer key the agent's structured output
    #: is graded against (see :func:`evaluate_feasibility`). Empty for a ``do``-task.
    answer_key: dict[str, Any] = dataclasses.field(default_factory=dict)
    #: For a ``feasibility`` task: the authored provenance for each answer-key claim
    #: (a docs ref, a live-org read, a forum thread) — captured at authoring time so a
    #: wrong key is auditable, per ADR 0028's verifier-quality leg. Empty for a ``do``-task.
    evidence: list[str] = dataclasses.field(default_factory=list)
    #: When true, the run step always measures this task's skill **lift** by also
    #: running a skill-absent (counterfactual) leg (#588) — the per-task "always
    #: measure this one" knob, equivalent to passing ``run --counterfactual``.
    counterfactual: bool = False
    #: Curation metadata (ADR 0028, #895), never read by the runner. ``tier`` is the
    #: discrimination weight — 1 single-command, 2 workflow, 3 trap — used at authoring
    #: time for demand-weighted slot allocation (the corpus skews to 2/3). ``None`` when
    #: the task predates the tag.
    tier: int | None = None
    #: Provenance of the real-demand ask this task encodes: ``{"type": <SOURCE_TYPES>,
    #: "url": <str|None>}``. Recorded so the corpus is auditable against its sources and
    #: can't silently drift into teaching-to-the-test. Empty when the task predates the tag.
    source: dict[str, Any] = dataclasses.field(default_factory=dict)

    @property
    def is_feasibility(self) -> bool:
        """True when the task grades structured output against an answer key (#891)."""
        return self.kind == "feasibility"

    @property
    def is_diagnostic(self) -> bool:
        """True when the task has no programmatic predicate (a ``do``-task with no ``expect``).

        A diagnostic task can only be scored by the ``--analyze`` pass; the runner
        refuses to run one without it. A ``feasibility`` task is never diagnostic — its
        answer-key match *is* its programmatic predicate.
        """
        return not self.is_feasibility and not self.expect


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

    kind = meta.get("kind", "do")
    if kind not in KINDS:
        raise ValueError(f"{path}: kind {kind!r} not one of {KINDS}")

    # Curation metadata (#895): optional, but validated when present so a malformed tag
    # fails the smoke test rather than silently sitting in the corpus. The runner ignores
    # both — they drive authoring-time slot allocation and source auditing only.
    tier = meta.get("tier")
    # Require a real ``int`` in (1, 2, 3): ``not isinstance(tier, int)`` rejects ``tier: 2.0``,
    # and the explicit ``bool`` guard rejects ``tier: true`` — Python's ``bool ⊆ int`` makes
    # both ``True in (1, 2, 3)`` and ``2.0 in (1, 2, 3)`` true, so without these a non-int
    # would silently pass (the same trap evaluate_feasibility guards for scalar answer keys).
    if tier is not None and (
        not isinstance(tier, int) or isinstance(tier, bool) or tier not in (1, 2, 3)
    ):
        raise ValueError(f"{path}: tier {tier!r} must be the integer 1, 2, or 3 (or omitted)")

    raw_source = meta.get("source")
    source: dict[str, Any] = {}
    if raw_source is not None:
        if not isinstance(raw_source, dict):
            raise ValueError(f"{path}: source must be a mapping of 'type' and 'url'")
        stype = raw_source.get("type")
        if stype not in SOURCE_TYPES:
            raise ValueError(f"{path}: source.type {stype!r} not one of {SOURCE_TYPES}")
        if "url" not in raw_source:
            raise ValueError(f"{path}: source must carry a 'url' key (null for firsthand)")
        # A non-``firsthand`` source must cite a non-empty URL string; ``firsthand`` (a
        # traceable secondary — this repo's issues) may leave it null. Validated so a
        # curation tag can't carry a meaningless ``url: 123`` / ``url: null`` provenance.
        url = raw_source["url"]
        if stype == "firsthand":
            if url is not None and not (isinstance(url, str) and url.strip()):
                raise ValueError(
                    f"{path}: source.url for firsthand must be null or a non-empty string"
                )
        elif not (isinstance(url, str) and url.strip()):
            raise ValueError(
                f"{path}: source.url must be a non-empty string (null only for firsthand)"
            )
        source = raw_source

    query: list[str] = []
    expect: dict[str, Any] = {}
    answer_key: dict[str, Any] = {}
    evidence: list[str] = []

    if kind == "feasibility":
        # A feasibility task (#891) grades structured output, not org state: it must not
        # declare ``end_state`` (there is nothing to mutate or read back) and instead
        # carries an ``answer_key`` (the graded fields) plus ``evidence`` (their provenance).
        if "end_state" in meta:
            raise ValueError(
                f"{path}: a feasibility task grades the agent's structured output, not org "
                f"state — remove end_state (declare answer_key instead)"
            )
        raw_key = require("answer_key")
        if not isinstance(raw_key, dict) or not raw_key:
            raise ValueError(
                f"{path}: answer_key must be a non-empty mapping for a feasibility task"
            )
        for name, want in raw_key.items():
            values = want if isinstance(want, list) else [want]
            if not all(isinstance(v, str | int | float | bool) for v in values):
                raise ValueError(
                    f"{path}: answer_key.{name} must be a scalar or a list of scalars "
                    f"(scalar → exact match, list → recall)"
                )
        answer_key = raw_key
        raw_evidence = require("evidence")
        if (
            not isinstance(raw_evidence, list)
            or not raw_evidence
            or not all(isinstance(e, str) and e.strip() for e in raw_evidence)
        ):
            raise ValueError(
                f"{path}: evidence must be a non-empty list of non-empty strings "
                f"(the answer key's provenance, captured at authoring time)"
            )
        evidence = raw_evidence
    else:
        # ``end_state`` is optional: a diagnostic task (#572) omits the programmatic
        # predicate and is scored by the ``--analyze`` pass instead. When present, a
        # non-empty ``query`` is required (it fetches the org state — used for scoring
        # and/or fed to the analyzer); ``expect`` is optional, and its absence marks the
        # task diagnostic (org state still flows to the analyzer, just nothing asserted).
        # A diagnostic task that needs no org-state query omits ``end_state`` entirely —
        # an empty query is rejected so it can't silently degrade scoring to NoneType.
        end_state = meta.get("end_state")
        if end_state is not None:
            if not isinstance(end_state, dict):
                raise ValueError(f"{path}: end_state must be a mapping")
            query = end_state.get("query")
            if (
                not isinstance(query, list)
                or not query
                or not all(isinstance(a, str) for a in query)
            ):
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
        kind=kind,
        answer_key=answer_key,
        evidence=evidence,
        counterfactual=bool(meta.get("counterfactual", False)),
        tier=tier,
        source=source,
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
        if not any(all(str(row.get(k)) == str(v) for k, v in want_row.items()) for row in data):
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


def evaluate_feasibility(data: Any, answer_key: dict[str, Any]) -> tuple[bool, str]:
    """Score a feasibility task's structured output ``data`` against its ``answer_key`` (#891).

    ``data`` is the JSON object the agent emitted (no org state — a feasibility task
    mutates nothing). It is graded **field-by-field** against the answer key, keeping the
    per-trial verdict a clean binary (ADR 0028):

    - the output must be a JSON **object** — anything else (a list, a scalar, ``None`` from
      a missing/invalid answer file) is schema-invalid and fails;
    - every ``answer_key`` field must be **present** in the output — an absent graded field
      is a schema-invalidity, not a pass;
    - a **scalar** answer-key value (e.g. ``cli_achievable``) is an **exact match**;
    - a **list** answer-key value is scored by **recall** — every expected item must appear
      (case-insensitive substring of some emitted list entry, so ``"data import"`` matches
      the agent's ``"crm data import accounts x.jsonl"``). Missing a single item fails the
      trial; extra emitted items are not penalised.

    Returns ``(passed, reason)``; ``reason`` names the first failing field so a failed run
    is self-describing.
    """
    if not isinstance(data, dict):
        return False, f"expected a JSON object, got {type(data).__name__}"

    for key, want in answer_key.items():
        if key not in data:
            return False, f"{key}: missing from the agent's output"
        got = data[key]
        if isinstance(want, list):
            if not isinstance(got, list):
                return False, f"{key}: expected a list, got {type(got).__name__}"
            emitted = [str(g).lower() for g in got]
            for item in want:
                needle = str(item).lower()
                if not any(needle in entry for entry in emitted):
                    return False, f"{key}: missing required item {item!r} (recall)"
        # Exact match on scalars — and a strict one: Python's ``bool ⊆ int`` makes
        # ``True == 1``, so guard the type too, else an agent emitting ``"cli_achievable": 1``
        # would pass a field the design calls an exact match (the one the binary pivots on).
        elif isinstance(want, bool) != isinstance(got, bool) or got != want:
            return False, f"{key}: expected {want!r}, got {got!r}"

    return True, "all answer-key fields matched"
