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
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from crm.core.legacy import LegacyClient
from crm.core.legacy_reads import connection_wide
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


@dataclass(frozen=True)
class Step:
    """A processing step with what decides its usage; *message* is its SDK message name."""

    id: str
    type_id: str
    enabled: bool
    sync: bool
    autodelete: bool
    message: str = ""
    type_name: str = ""


@dataclass(frozen=True)
class PluginType:
    id: str
    assembly_id: str
    typename: str
    workflow: bool = False


@dataclass(frozen=True)
class Evidence:
    """Run records read for the steps: job rows by step id, trace hits by (type, message).

    *jobs_gap* says why the job read is incomplete or missing; *trace_gap* why
    there is no trace signal (tracing off, no such entity). Both only explain an
    `unknown`: a missing record never makes a row `unused`.
    """

    jobs: frozenset[str] = frozenset()
    traces: frozenset[tuple[str, str]] = frozenset()
    jobs_gap: str | None = None
    trace_gap: str | None = None


def _step_usage(step: Step, ev: Evidence, as_of: str) -> Usage:
    if not step.enabled:
        return Usage("unused", STEP_SIGNAL, None, as_of, "disabled")
    if step.id in ev.jobs:
        return Usage("used", "async_job", None, as_of)
    if (step.type_name, step.message.lower()) in ev.traces:
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


def roll_up(children: Sequence[Usage], as_of: str, *, orphan: str | None = None) -> Usage:
    """A parent's usage from its children: `used` if any is, `unused` only if all are.

    No children means nothing can fire: `unused`, unless *orphan* names why the
    parent can still run (it then reads `unknown`).
    """
    if any(c.state == "used" for c in children):
        return Usage("used", STEP_SIGNAL, None, as_of)
    if not children and orphan:
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
            orphan="workflow activity: invoked by workflows, not steps" if t.workflow else None,
        )
        for t in types
    }
    by_assembly = {
        a: roll_up([by_type[t.id] for t in types if t.assembly_id == a], as_of)
        for a in {t.assembly_id for t in types} | set(assembly_ids)
    }
    return by_step, by_type, by_assembly


def _job_fetch(extra: str = "") -> str:
    return (
        '<fetch aggregate="true"><entity name="asyncoperation">'
        '<attribute name="owningextensionid" alias="step" groupby="true"/>'
        '<attribute name="asyncoperationid" alias="n" aggregate="count"/>'
        f'<filter><condition attribute="operationtype" operator="eq" value="1"/>{extra}</filter>'
        "</entity></fetch>"
    )


def _trace_fetch() -> str:
    return (
        '<fetch aggregate="true"><entity name="plugintracelog">'
        '<attribute name="typename" alias="type" groupby="true"/>'
        '<attribute name="messagename" alias="message" groupby="true"/>'
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
    traces: frozenset[tuple[str, str]] = frozenset()
    trace_gap = "plug-in trace log is off" if not trace_on else None
    if trace_on:
        try:
            got = as_dict(backend.get("plugintracelogs", params={"fetchXml": _trace_fetch()}))
            traces = frozenset(
                (str(r["type"]), str(r["message"]).lower()) for r in got.get("value", [])
            )
        except D365Error as exc:
            if connection_wide(exc):
                raise
            trace_gap = f"plug-in trace log unreadable: {exc}"
    return Evidence(jobs, traces, jobs_gap, trace_gap)


def _message_name(step_row: dict[str, Any]) -> str:
    message: dict[str, Any] = step_row.get("sdkmessageid") or {}
    return str(message.get("name") or "").lower()


def probe_v9_plugin_types(
    backend: D365Backend,
    types: Sequence[PluginType],
    *,
    clock: Callable[[], str] = _now,
) -> dict[str, Usage]:
    """Usage of each v9 plug-in type, rolled up from its steps: three bulk reads, none per step."""
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
    params = {"$select": select}
    if trace_on:
        params["$expand"] = "sdkmessageid($select=name)"
    names = {t.id: t.typename for t in types}
    steps = [
        Step(
            r["sdkmessageprocessingstepid"],
            r["_eventhandler_value"],
            r.get("statecode") == 0,
            r.get("mode") == 0,
            bool(r.get("asyncautodelete")),
            _message_name(r),
            names.get(r["_eventhandler_value"], ""),
        )
        for r in backend.get_collection("sdkmessageprocessingsteps", params=params)
        if r.get("_eventhandler_value")
    ]
    ev = read_v9_plugin_evidence(backend, trace_on=trace_on)
    return plugin_usage(steps, types, ev, clock=clock)[1]


def _ref(value: object) -> str:
    return str(value.get("id") or "") if isinstance(value, dict) else ""  # pyright: ignore


def _fetch_all(client: LegacyClient, entity: str, columns: Sequence[str]) -> list[dict[str, Any]]:
    attrs = "".join(f'<attribute name="{c}"/>' for c in columns)
    xml = f'<fetch><entity name="{entity}">{attrs}</entity></fetch>'
    return [r for page in client.retrieve_multiple(xml) for r in page]


def read_legacy_plugin_evidence(
    client: LegacyClient,
    *,
    time_cap: float = JOB_TIME_CAP,
    monotonic: Callable[[], float] = time.monotonic,
) -> Evidence:
    """Step ids that own an async job row, read page by page until *time_cap* seconds pass.

    CRM 2011 refuses `asyncoperation` aggregates (the 50,000 limit) and filtered
    scans of it can run for minutes, so the read is plain and bounded: steps not
    seen when it stops stay `unknown`. There is no `plugintracelog` entity.
    """
    gap = "no plug-in trace log on CRM 2011"
    fetch = (
        '<fetch><entity name="asyncoperation"><attribute name="owningextensionid"/>'
        '<filter><condition attribute="operationtype" operator="eq" value="1"/>'
        '<condition attribute="owningextensionid" operator="not-null"/></filter></entity></fetch>'
    )
    deadline = monotonic() + time_cap
    jobs: set[str] = set()
    jobs_gap = None
    try:
        for page in client.pages(fetch):
            jobs.update(_ref(r.get("owningextensionid")) for r in page.records)
            if page.more and monotonic() >= deadline:
                jobs_gap = f"async job read stopped at the {time_cap:g} s time cap"
                break
    except D365Error as exc:
        if connection_wide(exc):
            raise
        jobs_gap = f"async job read failed: {exc}"
    return Evidence(frozenset(jobs - {""}), frozenset(), jobs_gap, gap)


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
            bool(r.get("isworkflowactivity")),
        )
        for r in _fetch_all(
            client, "plugintype", ("typename", "pluginassemblyid", "isworkflowactivity")
        )
    ]
    steps = [
        Step(
            str(r["id"]),
            _ref(r.get("plugintypeid")),
            r.get("statecode") == 0,
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
