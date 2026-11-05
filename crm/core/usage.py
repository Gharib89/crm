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
