"""Usage of a component: `used`, `unused` or `unknown` (spec #1091).

A `Usage` carries no entity-specific field, so later slices (plug-in steps,
workflows) share it. The one rule behind it: **`unknown` is never `unused`.**
Only positive evidence (`used`) or proof of non-use (a confirmed empty entity)
changes a row; a read that failed or a source with no signal stays `unknown`.

The entity probes are read-only and run with bounded concurrency. A v9 zero
from `RetrieveTotalRecordCount` is a snapshot (under 24 hours old, and empty on
a young org), so it is never trusted: an existence read confirms it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Collection, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any, Literal

from crm.core.legacy import LegacyClient
from crm.core.legacy_reads import connection_wide, list_entities_with_primary_ids
from crm.core.workflow import (
    CATEGORY_ACTION,
    CATEGORY_BPF,
    CATEGORY_BUSINESS_RULE,
    CATEGORY_DIALOG,
    CATEGORY_MODERN_FLOW,
    MODE_REALTIME,
    STATE_DRAFT,
    TYPE_ACTIVATION,
    TYPE_DEFINITION,
)
from crm.utils.d365_backend import D365Backend, D365Error, as_dict

UsageState = Literal["used", "unused", "unknown"]
Progress = Callable[[int, int], None]

SIGNAL = "record_presence"
COUNT_CHUNK = 50  # RetrieveTotalRecordCount names per request
WORKERS = 4
LEGACY_AGGREGATE_LIMIT = 50_000  # CRM 2011's AggregateQueryRecordLimit


@dataclass(frozen=True)
class Usage:
    """One component's usage.

    *signal* names what was read, *value* is a count (None when only existence
    is known), *as_of* the ISO time it was read, *reason* why the state is
    `unknown` (or a caveat on a count).
    """

    state: UsageState
    signal: str
    value: int | None
    as_of: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EntityTarget:
    """An entity to probe; *skip* is the reason it cannot be (it reads `unknown`)."""

    name: str
    set_name: str
    primary_id: str
    skip: str | None = None


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def attach(
    rows: Sequence[dict[str, Any]],
    usages: dict[str, Usage],
    *,
    key: Callable[[dict[str, Any]], str],
    exclude_unknown: bool,
) -> list[dict[str, Any]]:
    """Give each row its `usage`, dropping `unused` rows (and `unknown` ones if strict)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        u = usages[key(row)]
        if u.state == "unused" or (exclude_unknown and u.state == "unknown"):
            continue
        out.append({**row, "usage": u.to_dict()})
    return out


class UsageProgress:
    """Write probe progress as `[crm] usage stage=<stage> ...` lines, at most one a second.

    The first line (done 0) and the last (done == total) always print, in the
    style of extract's progress lines: one line per tick, never a redrawn bar.
    """

    def __init__(
        self,
        write: Callable[[str], object],
        stage: str,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._write, self._stage, self._clock = write, stage, clock
        self._last: float | None = None

    def __call__(self, done: int, total: int) -> None:
        now = self._clock()
        edge = done == 0 or done >= total
        if not edge and self._last is not None and now - self._last < 1.0:
            return
        self._last = now
        tail = f"total={total}" if done == 0 else f"done={done}/{total}"
        self._write(f"[crm] usage stage={self._stage} {tail}\n")


class _Tick:
    """A thread-safe done counter that reports to *progress*."""

    def __init__(self, total: int, progress: Progress | None):
        self._total, self._progress, self._done = total, progress, 0
        self._lock = threading.Lock()
        if progress:
            progress(0, total)

    def add(self, n: int = 1) -> None:
        with self._lock:
            self._done += n
            if self._progress:
                self._progress(self._done, self._total)


def _map(fn: Callable[[Any], Any], items: Sequence[Any]) -> Iterator[Any]:
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        yield from pool.map(fn, items)


# ── v9 ───────────────────────────────────────────────────────────────────


def entity_targets(backend: D365Backend) -> dict[str, EntityTarget]:
    """Every entity as a probe target, virtual, elastic and private ones marked skipped.

    `TableType` is absent before Dataverse (on-prem 9.0 answers 400), so the
    read is repeated without it and nothing is marked virtual then.
    """
    base = "LogicalName,EntitySetName,PrimaryIdAttribute,IsPrivate"
    try:
        rows = backend.get_collection("EntityDefinitions", params={"$select": base + ",TableType"})
    except D365Error as exc:
        if exc.status != 400:
            raise
        rows = backend.get_collection("EntityDefinitions", params={"$select": base})
    targets: dict[str, EntityTarget] = {}
    for r in rows:
        kind = r.get("TableType")
        skip = (
            "private entity"
            if r.get("IsPrivate")
            else f"{kind.lower()} table"
            if kind in ("Virtual", "Elastic")
            else None
        )
        name = r.get("LogicalName") or ""
        targets[name] = EntityTarget(
            name, r.get("EntitySetName") or "", r.get("PrimaryIdAttribute") or "", skip
        )
    return targets


def _batch_count(backend: D365Backend, names: Sequence[str]) -> dict[str, int]:
    quoted = ",".join(f"'{n}'" for n in names)
    result = as_dict(backend.get(f"RetrieveTotalRecordCount(EntityNames=[{quoted}])"))
    coll: dict[str, Any] = result.get("EntityRecordCountCollection") or {}
    counts = dict(zip(coll.get("Keys") or [], coll.get("Values") or [], strict=False))
    return {n: int(counts.get(n, 0)) for n in names}


def _count_names(backend: D365Backend, names: Sequence[str]) -> dict[str, int | str]:
    """Counts for *names*; one the server refuses maps to its reason.

    One invalid name (400) or one the caller may not read (403) fails the whole
    chunk, so a failed chunk is bisected and the refused name recorded without
    losing the rest.
    """
    try:
        return dict(_batch_count(backend, names))
    except D365Error as exc:
        if exc.status not in (400, 403) or connection_wide(exc):
            raise
        if len(names) == 1:
            return {names[0]: str(exc)}
        mid = len(names) // 2
        return {**_count_names(backend, names[:mid]), **_count_names(backend, names[mid:])}


def _exists_v9(backend: D365Backend, t: EntityTarget) -> bool:
    rows = as_dict(backend.get(t.set_name, params={"$select": t.primary_id, "$top": "1"}))
    return bool(rows.get("value"))


def probe_v9_entities(
    backend: D365Backend,
    targets: Sequence[EntityTarget],
    *,
    progress: Progress | None = None,
    clock: Callable[[], str] = _now,
) -> dict[str, Usage]:
    """Record presence of each entity on v9: batched counts, every zero confirmed by a read."""
    as_of = clock()
    tick = _Tick(len(targets), progress)
    out: dict[str, Usage] = {}

    def unknown(reason: str) -> Usage:
        return Usage("unknown", SIGNAL, None, as_of, reason)

    live = [t for t in targets if not t.skip]
    for t in targets:
        if t.skip:
            out[t.name] = unknown(t.skip)
    tick.add(len(targets) - len(live))

    by_name = {t.name: t for t in live}
    chunks = [live[i : i + COUNT_CHUNK] for i in range(0, len(live), COUNT_CHUNK)]
    counts: dict[str, int | str] = {}
    for part in _map(lambda c: _count_names(backend, [t.name for t in c]), chunks):
        counts.update(part)

    def settle(name: str) -> tuple[str, Usage]:
        n = counts[name]
        if isinstance(n, str):
            u = unknown(n)
        elif n > 0:
            u = Usage("used", SIGNAL, n, as_of)
        elif not by_name[name].primary_id or not by_name[name].set_name:
            u = unknown("no entity set or primary id attribute to confirm a zero count")
        else:
            try:
                present = _exists_v9(backend, by_name[name])
            except D365Error as exc:
                if connection_wide(exc):
                    raise
                u = unknown(str(exc))
            else:
                u = (
                    Usage("used", SIGNAL, None, as_of)
                    if present
                    else Usage("unused", SIGNAL, 0, as_of)
                )
        tick.add()
        return name, u

    out.update(_map(settle, list(by_name)))
    return out


# ── CRM 2011 ─────────────────────────────────────────────────────────────


def _first_page(client: LegacyClient, fetch_xml: str) -> list[dict[str, object]]:
    return next(iter(client.pages(fetch_xml, page_size=1))).records


def _count_legacy(
    client: LegacyClient, name: str, primary_id: str
) -> tuple[int | None, str | None]:
    """The exact count, or 50,000 with an "at least" reason when the aggregate limit answers."""
    fetch = (
        f'<fetch aggregate="true"><entity name="{name}">'
        f'<attribute name="{primary_id}" alias="n" aggregate="count"/></entity></fetch>'
    )
    try:
        (record,) = _first_page(client, fetch)
    except D365Error as exc:
        if connection_wide(exc):
            raise
        if "AggregateQueryRecordLimit" in str(exc):
            return LEGACY_AGGREGATE_LIMIT, "at least 50,000"
        return None, f"count unavailable: {exc}"
    return int(record["n"]), None  # pyright: ignore[reportArgumentType]


def probe_legacy_entities(
    client: LegacyClient,
    primary_ids: dict[str, str],
    *,
    with_counts: bool = False,
    progress: Progress | None = None,
    clock: Callable[[], str] = _now,
) -> dict[str, Usage]:
    """Record presence of each entity on CRM 2011: a page-size-1 read of its primary id.

    *primary_ids* maps entity name to the primary id attribute from its
    metadata (never `<name>id`: `systemform` has none). An entity whose read
    fails (`RetrieveMultiple` refused) is `unknown` with the reason. With
    *with_counts*, a used entity also gets its exact FetchXML aggregate count.
    """
    as_of = clock()
    tick = _Tick(len(primary_ids), progress)

    def probe(name: str) -> tuple[str, Usage]:
        if not primary_ids[name]:
            tick.add()
            return name, Usage("unknown", SIGNAL, None, as_of, "no primary id attribute to read")
        try:
            present = bool(
                _first_page(
                    client,
                    f'<fetch><entity name="{name}"><attribute name="{primary_ids[name]}"/>'
                    "</entity></fetch>",
                )
            )
        except D365Error as exc:
            if connection_wide(exc):
                raise
            u = Usage("unknown", SIGNAL, None, as_of, str(exc))
        else:
            if not present:
                u = Usage("unused", SIGNAL, 0, as_of)
            elif with_counts:
                value, reason = _count_legacy(client, name, primary_ids[name])
                u = Usage("used", SIGNAL, value, as_of, reason)
            else:
                u = Usage("used", SIGNAL, None, as_of)
        tick.add()
        return name, u

    return dict(_map(probe, list(primary_ids)))


# ── Plug-in steps, types and assemblies (slice #1093) ────────────────────

STEP_SIGNAL = "plugin_step_runs"
JOB_TIME_CAP = 120.0  # seconds a CRM 2011 async job read may run before it stops
JOB_ROW_CAP = 100_000  # rows it may read before it stops


@dataclass(frozen=True)
class Step:
    """A processing step with what decides its usage."""

    id: str
    type_id: str
    enabled: bool
    sync: bool
    autodelete: bool


@dataclass(frozen=True)
class PluginType:
    id: str
    assembly_id: str
    typename: str
    outside: str = ""  # why the type can run with no step (a workflow activity, a custom API)


@dataclass(frozen=True)
class Evidence:
    """Run records read for the steps: step ids with a job row, and with a trace log row.

    *jobs_gap* says why the job read is incomplete or missing; *trace_gap* why
    there is no trace signal (tracing off, no such entity). Both only explain an
    `unknown`: a missing record never makes a row `unused`.
    """

    jobs: frozenset[str] = frozenset()
    traces: frozenset[str] = frozenset()
    jobs_gap: str | None = None
    trace_gap: str | None = None


def _step_usage(step: Step, ev: Evidence, as_of: str) -> Usage:
    if not step.enabled:
        return Usage("unused", STEP_SIGNAL, None, as_of, "disabled")
    if step.id in ev.jobs:
        return Usage("used", "async_job", None, as_of)
    if step.id in ev.traces:
        return Usage("used", "plugin_trace", None, as_of)
    if step.sync:
        why = ["synchronous step: no run record exists"]
    elif step.autodelete:
        why = ["successful async jobs of this step are deleted"]
    else:
        why = [ev.jobs_gap or "no async job found (job history may have been purged)"]
    if ev.trace_gap:
        why.append(ev.trace_gap)
    return Usage("unknown", STEP_SIGNAL, None, as_of, "; ".join(why))


def roll_up(children: Sequence[Usage], as_of: str, *, orphan: str = "") -> Usage:
    """A parent's usage from its children: `used` if any is, `unused` only if all are.

    No children, or only disabled ones, means nothing can fire through a step:
    `unused`, unless *orphan* names why the parent can still run without one
    (it then reads `unknown`).
    """
    if any(c.state == "used" for c in children):
        return Usage("used", STEP_SIGNAL, None, as_of)
    if orphan and all(c.state == "unused" for c in children):
        return Usage("unknown", STEP_SIGNAL, None, as_of, orphan)
    if all(c.state == "unused" for c in children):
        return Usage("unused", STEP_SIGNAL, None, as_of, "every step is disabled or none exists")
    why = next((c.reason for c in children if c.state == "unknown" and c.reason), None)
    return Usage(
        "unknown", STEP_SIGNAL, None, as_of, "no run record" + (f", e.g. {why}" if why else "")
    )


def plugin_usage(
    steps: Sequence[Step],
    types: Sequence[PluginType],
    ev: Evidence,
    *,
    assembly_ids: Sequence[str] = (),
    clock: Callable[[], str] = _now,
) -> tuple[dict[str, Usage], dict[str, Usage], dict[str, Usage]]:
    """Usage of every step, type and assembly, each keyed by id (rules in spec #1093).

    *assembly_ids* adds assemblies that own no type, which read `unused`.
    """
    as_of = clock()
    by_step = {s.id: _step_usage(s, ev, as_of) for s in steps}
    step_states: dict[str, list[Usage]] = {t.id: [] for t in types}
    for s in steps:
        if s.type_id in step_states:
            step_states[s.type_id].append(by_step[s.id])
    by_type = {
        t.id: roll_up(
            step_states[t.id],
            as_of,
            orphan=t.outside,
        )
        for t in types
    }
    by_assembly = {
        a: roll_up([by_type[t.id] for t in types if t.assembly_id == a], as_of)
        for a in {t.assembly_id for t in types} | set(assembly_ids)
    }
    return by_step, by_type, by_assembly


def _job_fetch(group_by: str = "owningextensionid", operation_type: int = 1) -> str:
    return (
        '<fetch aggregate="true"><entity name="asyncoperation">'
        f'<attribute name="{group_by}" alias="step" groupby="true"/>'
        '<attribute name="asyncoperationid" alias="n" aggregate="count"/>'
        f'<filter><condition attribute="operationtype" operator="eq" value="{operation_type}"/>'
        "</filter></entity></fetch>"
    )


def _trace_fetch() -> str:
    return (
        '<fetch aggregate="true"><entity name="plugintracelog">'
        '<attribute name="pluginstepid" alias="step" groupby="true"/>'
        '<attribute name="plugintracelogid" alias="n" aggregate="count"/>'
        "</entity></fetch>"
    )


def read_v9_plugin_evidence(backend: D365Backend, *, trace_on: bool) -> Evidence:
    """Async job rows grouped by step (one aggregate) and, when tracing is on, trace hits.

    Tracing off or unreadable is recorded as no signal; so is a job aggregate
    the server refuses (e.g. its record limit), which keeps those steps `unknown`.
    """
    jobs: frozenset[str] = frozenset()
    jobs_gap = None
    try:
        got = as_dict(backend.get("asyncoperations", params={"fetchXml": _job_fetch()}))
        jobs = frozenset(str(r["step"]) for r in got.get("value", []) if r.get("step"))
    except D365Error as exc:
        if connection_wide(exc):
            raise
        jobs_gap = f"async job read failed: {exc}"
    traces: frozenset[str] = frozenset()
    trace_gap = "plug-in trace log is off" if not trace_on else None
    if trace_on:
        try:
            got = as_dict(backend.get("plugintracelogs", params={"fetchXml": _trace_fetch()}))
            traces = frozenset(str(r["step"]) for r in got.get("value", []) if r.get("step"))
        except D365Error as exc:
            if connection_wide(exc):
                raise
            trace_gap = f"plug-in trace log unreadable: {exc}"
    return Evidence(jobs, traces, jobs_gap, trace_gap)


WORKFLOW = "workflow activity: invoked by workflows, not by steps"
CUSTOM_API = "custom API implementation: invoked by the API, not by steps"


def _custom_api_types(backend: D365Backend) -> set[str]:
    """Plug-in types that implement a custom API (none where the table does not exist)."""
    try:
        rows = backend.get_collection("customapis", params={"$select": "_plugintypeid_value"})
    except D365Error as exc:
        if exc.status not in (400, 404):
            raise
        return set()
    return {r["_plugintypeid_value"] for r in rows if r.get("_plugintypeid_value")}


def probe_v9_plugin_types(
    backend: D365Backend,
    types: Sequence[PluginType],
    *,
    clock: Callable[[], str] = _now,
) -> dict[str, Usage]:
    """Usage of each v9 plug-in type, rolled up from its steps: three bulk reads, none per step."""
    return _v9_plugin_states(backend, types, (), clock=clock)[1]


def _v9_plugin_states(
    backend: D365Backend,
    types: Sequence[PluginType],
    assembly_ids: Sequence[str],
    *,
    clock: Callable[[], str] = _now,
) -> tuple[dict[str, Usage], dict[str, Usage], dict[str, Usage]]:
    """Usage of every step, type and assembly on v9, each keyed by id."""
    try:  # the column is absent before 9.0.x on some on-prem builds: no trace signal then
        org = as_dict(
            backend.get("organizations", params={"$select": "plugintracelogsetting", "$top": "1"})
        )
    except D365Error as exc:
        if exc.status != 400:
            raise
        org = {}
    org_rows: list[dict[str, Any]] = org.get("value") or [{}]
    trace_on = bool(org_rows[0].get("plugintracelogsetting"))
    select = "sdkmessageprocessingstepid,statecode,mode,asyncautodelete,_eventhandler_value"
    api_types = _custom_api_types(backend)
    types = [
        replace(t, outside=CUSTOM_API) if t.id in api_types and not t.outside else t for t in types
    ]
    steps = [
        Step(
            r["sdkmessageprocessingstepid"],
            r["_eventhandler_value"],
            r.get("statecode") != 1,  # 1 is disabled; an unread state is never a proof
            r.get("mode") == 0,
            bool(r.get("asyncautodelete")),
        )
        for r in backend.get_collection("sdkmessageprocessingsteps", params={"$select": select})
        if r.get("_eventhandler_value")
    ]
    ev = read_v9_plugin_evidence(backend, trace_on=trace_on)
    return plugin_usage(steps, types, ev, assembly_ids=assembly_ids, clock=clock)


def _ref(value: object) -> str:
    return str(value.get("id") or "") if isinstance(value, dict) else ""  # pyright: ignore


def _fetch_all(
    client: LegacyClient, entity: str, columns: Sequence[str], where: str = ""
) -> list[dict[str, Any]]:
    attrs = "".join(f'<attribute name="{c}"/>' for c in columns)
    flt = f"<filter>{where}</filter>" if where else ""
    xml = f'<fetch><entity name="{entity}">{attrs}{flt}</entity></fetch>'
    return [r for page in client.retrieve_multiple(xml) for r in page]


def _read_ids(
    client: LegacyClient,
    fetch: str,
    column: str,
    what: str,
    *,
    time_cap: float,
    monotonic: Callable[[], float],
) -> tuple[set[str], str | None]:
    """The distinct lookup ids of *column* over *fetch*, read page by page; plus why it is partial.

    Stops at *time_cap* seconds or `JOB_ROW_CAP` rows, or on a failed read: the
    ids already seen are kept and the gap names *what* stopped and why.
    """
    deadline = monotonic() + time_cap
    ids: set[str] = set()
    gap = None
    rows = 0
    try:
        for page in client.pages(fetch):
            ids.update(_ref(r.get(column)) for r in page.records)
            rows += len(page.records)
            if page.more and monotonic() >= deadline:
                gap = f"{what} stopped at the {time_cap:g} s time cap"
                break
            if page.more and rows >= JOB_ROW_CAP:
                gap = f"{what} stopped at the {JOB_ROW_CAP} row cap"
                break
    except D365Error as exc:
        if connection_wide(exc):
            raise
        gap = f"{what} failed: {exc}"
    return ids - {""}, gap


def read_legacy_plugin_evidence(
    client: LegacyClient,
    *,
    time_cap: float = JOB_TIME_CAP,
    monotonic: Callable[[], float] = time.monotonic,
) -> Evidence:
    """Step ids that own an async job row, read page by page until *time_cap* seconds pass.

    CRM 2011 refuses `asyncoperation` aggregates (the 50,000 limit) and filtered
    scans of it can run for minutes, so the read carries no `not-null` filter (rows
    with no owning step are dropped here) and stops at *time_cap* seconds or
    `JOB_ROW_CAP` rows: steps not seen when it stops stay `unknown`.
    There is no `plugintracelog` entity.
    """
    fetch = (
        '<fetch><entity name="asyncoperation"><attribute name="owningextensionid"/>'
        '<filter><condition attribute="operationtype" operator="eq" value="1"/></filter>'
        "</entity></fetch>"
    )
    jobs, gap = _read_ids(
        client,
        fetch,
        "owningextensionid",
        "async job read",
        time_cap=time_cap,
        monotonic=monotonic,
    )
    return Evidence(frozenset(jobs), frozenset(), gap, "no plug-in trace log on CRM 2011")


def probe_legacy_plugins(
    client: LegacyClient,
    *,
    time_cap: float = JOB_TIME_CAP,
    clock: Callable[[], str] = _now,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Usage], dict[str, Usage], dict[str, Usage]]:
    """Usage of every CRM 2011 plug-in step, type and assembly, each keyed by row id."""
    types = [
        PluginType(
            str(r["id"]),
            _ref(r.get("pluginassemblyid")),
            str(r.get("typename") or ""),
            WORKFLOW if r.get("isworkflowactivity") else "",
        )
        for r in _fetch_all(
            client, "plugintype", ("typename", "pluginassemblyid", "isworkflowactivity")
        )
    ]
    steps = [
        Step(
            str(r["id"]),
            _ref(r.get("plugintypeid")),
            r.get("statecode") != 1,  # 1 is disabled; an unread state is never a proof
            r.get("mode") == 0,
            bool(r.get("asyncautodelete")),
        )
        for r in _fetch_all(
            client,
            "sdkmessageprocessingstep",
            ("plugintypeid", "statecode", "mode", "asyncautodelete"),
        )
    ]
    ev = read_legacy_plugin_evidence(client, time_cap=time_cap, monotonic=monotonic)
    assembly_ids = [str(r["id"]) for r in _fetch_all(client, "pluginassembly", ("name",))]
    return plugin_usage(steps, types, ev, assembly_ids=assembly_ids, clock=clock)


PLUGIN_KINDS = ("plugin-assemblies", "plugin-types", "plugin-steps")  # `legacy components` kinds
USAGE_KINDS = (*PLUGIN_KINDS, "processes")  # the kinds `--in-use` applies to


def annotate_v9_types(
    backend: D365Backend, items: list[dict[str, Any]], *, exclude_unknown: bool
) -> list[dict[str, Any]]:
    """Give each `plugin list-types` row a `usage`, dropping the unused rows."""
    types = [
        PluginType(
            it["plugintypeid"],
            it.get("_pluginassemblyid_value") or "",
            it.get("typename") or "",
            WORKFLOW if it.get("isworkflowactivity") else "",
        )
        for it in items
    ]
    try:
        states = probe_v9_plugin_types(backend, types)
    except D365Error as exc:
        if connection_wide(exc):
            raise
        # a failed probe read is no evidence: keep the listed rows, labelled unknown
        gone = Usage("unknown", STEP_SIGNAL, None, _now(), f"usage probe failed: {exc}")
        states = {t.id: gone for t in types}
    rows = [
        {k: v for k, v in it.items() if k not in ("_pluginassemblyid_value", "isworkflowactivity")}
        for it in items
    ]
    return attach(rows, states, key=lambda r: r["plugintypeid"], exclude_unknown=exclude_unknown)


def annotate_legacy_components(
    client: LegacyClient, data: dict[str, list[dict[str, Any]]], *, exclude_unknown: bool
) -> dict[str, list[dict[str, Any]]]:
    """Give each plug-in or process row read by `legacy components` its `usage`.

    The plug-in and process probes are independent: a failed one labels only its
    own kinds `unknown` and the other still runs.
    """
    by_kind: dict[str, dict[str, Usage]] = {}
    probes: list[tuple[tuple[str, ...], str, Callable[[], Sequence[dict[str, Usage]]]]] = [
        (PLUGIN_KINDS, STEP_SIGNAL, lambda: _plugin_states(client)),
        (("processes",), PROCESS_SIGNAL, lambda: [probe_legacy_processes(client)]),
    ]
    for kinds, signal, probe in probes:
        present = [k for k in kinds if k in data]
        if not present:
            continue
        try:
            by_kind.update(zip(kinds, probe(), strict=True))
        except D365Error as exc:
            if connection_wide(exc):
                raise
            # a failed probe read is no evidence: keep the rows already read, labelled unknown
            gone = Usage("unknown", signal, None, _now(), f"usage probe failed: {exc}")
            by_kind.update({k: {str(r["id"]): gone for r in data[k]} for k in present})
    unread = Usage("unknown", PROCESS_SIGNAL, None, _now(), "created after the usage read")
    return {
        kind: attach(
            rows,
            {str(r["id"]): unread for r in rows} | by_kind[kind],  # a row born mid-read is unknown
            key=lambda r: str(r["id"]),
            exclude_unknown=exclude_unknown,
        )
        for kind, rows in data.items()
    }


def _plugin_states(client: LegacyClient) -> list[dict[str, Usage]]:
    steps, types, assemblies = probe_legacy_plugins(client)
    return [assemblies, types, steps]  # in PLUGIN_KINDS order


# ── workflows and processes (slice #1094) ────────────────────────────────

PROCESS_SIGNAL = "process_runs"
_NO_JOB = {  # kinds whose runs write no async job, so an absent job proves nothing
    CATEGORY_DIALOG: "dialog",
    CATEGORY_BUSINESS_RULE: "business rule",
    CATEGORY_ACTION: "action",
    CATEGORY_BPF: "business process flow",
}


@dataclass(frozen=True)
class Process:
    """A process definition with what decides its usage; `draft` is statecode 0 and nothing else."""

    id: str
    category: int
    draft: bool
    sync: bool
    autodelete: bool


@dataclass(frozen=True)
class ProcessEvidence:
    """Definition ids that own an async job row or a process session row.

    *gap* says why a read is incomplete or missing; it only explains an `unknown`.
    """

    jobs: frozenset[str] = frozenset()
    sessions: frozenset[str] = frozenset()
    gap: str | None = None


def _process_usage(p: Process, ev: ProcessEvidence, as_of: str) -> Usage:
    if p.draft:
        return Usage("unused", PROCESS_SIGNAL, None, as_of, "not activated")
    if p.id in ev.jobs:
        return Usage("used", "async_job", None, as_of)
    if p.id in ev.sessions:
        return Usage("used", "process_session", None, as_of)
    if p.category == CATEGORY_MODERN_FLOW:
        why = "cloud flow: run history is kept outside Dataverse"
    elif p.category in _NO_JOB:
        why = f"{_NO_JOB[p.category]}: runs write no async job"
    elif p.sync:
        why = "real-time workflow: runs write no async job"
    elif p.autodelete:
        why = "successful async jobs of this workflow are deleted"
    else:
        why = "no async job found (job history may have been purged)"
    if ev.gap:
        why += f"; {ev.gap}"
    return Usage("unknown", PROCESS_SIGNAL, None, as_of, why)


def process_usage(
    processes: Sequence[Process], ev: ProcessEvidence, *, clock: Callable[[], str] = _now
) -> dict[str, Usage]:
    """Usage of every process definition, keyed by id (rules in spec #1094)."""
    as_of = clock()
    return {p.id: _process_usage(p, ev, as_of) for p in processes}


def _definition_ids(ids: set[str] | frozenset[str], parents: dict[str, str]) -> frozenset[str]:
    """Map activation ids to their definition: a job names the activation, not the definition."""
    return frozenset(parents.get(i, i) for i in ids)


def _v9_ids(backend: D365Backend, entity_set: str, fetch: str) -> tuple[set[str], bool]:
    """Group ids of one aggregate read, and whether the server held back more groups."""
    got = as_dict(backend.get(entity_set, params={"fetchXml": fetch}))
    ids = {str(r["step"]) for r in got.get("value", []) if r.get("step")}
    return ids, bool(got.get("@Microsoft.Dynamics.CRM.morerecords"))


def read_v9_process_evidence(backend: D365Backend, parents: dict[str, str]) -> ProcessEvidence:
    """Async job and process session rows grouped by process (one aggregate each).

    A refused aggregate (e.g. its record limit) is recorded as a gap: the
    definitions it would have proven stay `unknown`.
    """
    jobs: set[str] = set()
    sessions: set[str] = set()
    gaps: list[str] = []
    reads = (
        ("asyncoperations", _job_fetch("workflowactivationid", 10), jobs, "async job read"),
        (
            "processsessions",
            '<fetch aggregate="true"><entity name="processsession">'
            '<attribute name="processid" alias="step" groupby="true"/>'
            '<attribute name="processsessionid" alias="n" aggregate="count"/>'
            "</entity></fetch>",
            sessions,
            "process session read",
        ),
    )
    for entity_set, fetch, into, what in reads:
        try:
            ids, more = _v9_ids(backend, entity_set, fetch)
            into.update(ids)
            if more:  # only the first page of groups is read: the rest stay unknown
                gaps.append(f"{what} truncated at the first page of groups")
        except D365Error as exc:
            if connection_wide(exc):
                raise
            gaps.append(f"{what} failed: {exc}")
    return ProcessEvidence(
        _definition_ids(jobs, parents), _definition_ids(sessions, parents), "; ".join(gaps) or None
    )


def probe_v9_processes(
    backend: D365Backend, *, clock: Callable[[], str] = _now
) -> dict[str, Usage]:
    """Usage of every v9 process definition: four bulk reads, none per definition."""
    select = "workflowid,category,statecode,mode,asyncautodelete"
    defs = backend.get_collection(
        "workflows", params={"$select": select, "$filter": f"type eq {TYPE_DEFINITION}"}
    )
    acts = backend.get_collection(
        "workflows",
        params={
            "$select": "workflowid,_parentworkflowid_value",
            "$filter": f"type eq {TYPE_ACTIVATION}",
        },
    )
    parents = {
        a["workflowid"]: a["_parentworkflowid_value"]
        for a in acts
        if a.get("_parentworkflowid_value")
    }
    processes = [
        Process(
            r["workflowid"],
            int(r.get("category") or 0),
            r.get("statecode") == STATE_DRAFT[0],  # an unread state is never proof of a draft
            r.get("mode") == MODE_REALTIME,
            bool(r.get("asyncautodelete")),
        )
        for r in defs
    ]
    return process_usage(processes, read_v9_process_evidence(backend, parents), clock=clock)


def probe_legacy_processes(
    client: LegacyClient,
    *,
    time_cap: float = JOB_TIME_CAP,
    clock: Callable[[], str] = _now,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Usage]:
    """Usage of every CRM 2011 process definition, keyed by row id.

    Same capped, unfiltered page reads as the plug-in jobs: the `asyncoperation`
    read carries only the operation type filter, and a session or job read that
    stops early leaves the definitions it did not reach `unknown`.
    """
    defs = _fetch_all(
        client,
        "workflow",
        ("category", "statecode", "asyncautodelete"),
        f'<condition attribute="type" operator="eq" value="{TYPE_DEFINITION}"/>',
    )
    acts = _fetch_all(
        client,
        "workflow",
        ("parentworkflowid",),
        f'<condition attribute="type" operator="eq" value="{TYPE_ACTIVATION}"/>',
    )
    parents = {str(a["id"]): _ref(a.get("parentworkflowid")) for a in acts}
    job_fetch = (
        '<fetch><entity name="asyncoperation"><attribute name="workflowactivationid"/>'
        '<filter><condition attribute="operationtype" operator="eq" value="10"/></filter>'
        "</entity></fetch>"
    )
    session_fetch = (
        '<fetch><entity name="processsession"><attribute name="processid"/></entity></fetch>'
    )
    jobs, jobs_gap = _read_ids(
        client,
        job_fetch,
        "workflowactivationid",
        "async job read",
        time_cap=time_cap,
        monotonic=monotonic,
    )
    sessions, sessions_gap = _read_ids(
        client,
        session_fetch,
        "processid",
        "process session read",
        time_cap=time_cap,
        monotonic=monotonic,
    )
    gap = "; ".join(g for g in (jobs_gap, sessions_gap) if g) or None
    processes = [
        Process(
            str(r["id"]),
            int(r.get("category") or 0),
            draft=r.get("statecode") == STATE_DRAFT[0],  # an unset state is never proof of a draft
            sync=False,  # 2011 workflows carry no mode column
            autodelete=bool(r.get("asyncautodelete")),
        )
        for r in defs
    ]
    ev = ProcessEvidence(_definition_ids(jobs, parents), _definition_ids(sessions, parents), gap)
    return process_usage(processes, ev, clock=clock)


def annotate_v9_processes(
    backend: D365Backend, items: list[dict[str, Any]], *, exclude_unknown: bool
) -> list[dict[str, Any]]:
    """Give each `workflow list` row a `usage`, dropping the unused rows."""
    try:
        states = probe_v9_processes(backend)
    except D365Error as exc:
        if connection_wide(exc):
            raise
        # a failed probe read is no evidence: keep the listed rows, labelled unknown
        gone = Usage("unknown", PROCESS_SIGNAL, None, _now(), f"usage probe failed: {exc}")
        states = {it["workflowid"]: gone for it in items}
    unread = Usage("unknown", PROCESS_SIGNAL, None, _now(), "created after the usage read")
    by_id = {it["workflowid"]: states.get(it["workflowid"], unread) for it in items}
    return attach(items, by_id, key=lambda r: r["workflowid"], exclude_unknown=exclude_unknown)


# ── inventory extract ──────────────────────────────────────

#: `inventory extract --with-usage` probes by group: the manifest kinds each fills, and the
#: signal their rows read `unknown` under when the whole group's probe fails.
INVENTORY_GROUPS: dict[str, tuple[tuple[str, ...], str]] = {
    "entity": (("entity",), SIGNAL),
    "plugins": (("plugin_assembly", "plugin_type", "step"), STEP_SIGNAL),
    "process": (("process",), PROCESS_SIGNAL),
}
GroupUsage = dict[str, dict[str, Usage]]  # manifest kind -> key (entity) or id -> usage


def probe_v9_group(
    backend: D365Backend, group: str, names: Collection[str], *, progress: Progress | None = None
) -> GroupUsage:
    """Usage for one group of manifest kinds on v9, by the probes `--in-use` runs.

    *names* are the entity logical names to probe (the kept ones); the other groups read in bulk.
    """
    if group == "entity":
        targets = entity_targets(backend)
        gone = "not in the entity definitions read for the probe"
        wanted = [targets.get(n) or EntityTarget(n, "", "", gone) for n in sorted(names)]
        return {"entity": probe_v9_entities(backend, wanted, progress=progress)}
    if group == "process":
        return {"process": probe_v9_processes(backend)}
    types = [
        PluginType(
            r["plugintypeid"],
            r.get("_pluginassemblyid_value") or "",
            r.get("typename") or "",
            WORKFLOW if r.get("isworkflowactivity") else "",
        )
        for r in backend.get_collection(
            "plugintypes",
            params={"$select": "plugintypeid,_pluginassemblyid_value,typename,isworkflowactivity"},
        )
    ]
    assemblies = [
        r["pluginassemblyid"]
        for r in backend.get_collection("pluginassemblies", params={"$select": "pluginassemblyid"})
    ]
    steps, by_type, by_assembly = _v9_plugin_states(backend, types, assemblies)
    return {"plugin_assembly": by_assembly, "plugin_type": by_type, "step": steps}


def probe_legacy_group(
    client: LegacyClient,
    group: str,
    names: Collection[str],
    *,
    time_cap: float = JOB_TIME_CAP,
    progress: Progress | None = None,
) -> GroupUsage:
    """`probe_v9_group` on CRM 2011; *time_cap* bounds each capped async-job read."""
    if group == "entity":
        _, primary_ids = list_entities_with_primary_ids(client)
        kept = {n: primary_ids.get(n, "") for n in names}
        return {"entity": probe_legacy_entities(client, kept, progress=progress)}
    if group == "process":
        return {"process": probe_legacy_processes(client, time_cap=time_cap)}
    steps, types, assemblies = probe_legacy_plugins(client, time_cap=time_cap)
    return {"plugin_assembly": assemblies, "plugin_type": types, "step": steps}
