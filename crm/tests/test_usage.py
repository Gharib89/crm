"""Usage model and entity record-presence probes (spec #1091, slice #1092)."""

# pyright: basic
from __future__ import annotations

import re
from urllib.parse import unquote

import pytest

from crm.core import usage
from crm.core.usage import EntityTarget, Usage
from crm.utils.d365_backend import D365Error

AS_OF = "2026-11-05T00:00:00Z"


def _target(name: str, **kw) -> EntityTarget:
    return EntityTarget(name=name, set_name=f"{name}s", primary_id=f"{name}id", **kw)


def _counts(path: str) -> list[str]:
    return re.findall(r"'([^']+)'", unquote(path))


def _v9_org(
    counts: dict[str, int],
    rows: set[str],
    bad: frozenset[str] | set[str] = frozenset(),
    unreadable=(),
    status=400,
):
    """A `get` answer: batch counts from *counts*, existence reads from *rows*."""

    def respond(path):
        if path.startswith("RetrieveTotalRecordCount"):
            names = _counts(path)
            if bad & set(names):
                raise D365Error("not valid for read", status=status)
            return {
                "EntityRecordCountCollection": {
                    "Keys": names,
                    "Values": [counts.get(n, 0) for n in names],
                }
            }
        entity = path.removesuffix("s")
        if entity in unreadable:
            raise D365Error("forbidden", status=403)
        return {"value": [{f"{entity}id": "x"}] if entity in rows else []}

    return respond


def test_v9_zero_count_is_confirmed_by_an_existence_read(make_fake_backend):
    backend = make_fake_backend(
        responses={"get": _v9_org({"big": 7}, rows={"stale"}, unreadable=("locked",))}
    )
    targets = [_target(n) for n in ("big", "stale", "empty", "locked")]
    got = usage.probe_v9_entities(backend, targets, clock=lambda: AS_OF)
    assert got["big"] == Usage("used", "record_presence", 7, AS_OF)
    # the snapshot said 0, the existence read found a row: used, count unknown
    assert got["stale"] == Usage("used", "record_presence", None, AS_OF)
    assert got["empty"].state == "unused" and got["empty"].value == 0
    assert got["locked"].state == "unknown" and "forbidden" in (got["locked"].reason or "")


# 400: invalid or virtual name; 403: an entity the caller has no read privilege on
@pytest.mark.parametrize("status", [400, 403])
def test_v9_bad_name_does_not_lose_the_rest_of_the_chunk(make_fake_backend, status):
    names = [f"e{i}" for i in range(50)] + ["bad"]
    org = _v9_org({n: 1 for n in names}, set(), {"bad"}, status=status)
    backend = make_fake_backend(responses={"get": org})
    got = usage.probe_v9_entities(backend, [_target(n) for n in names], clock=lambda: AS_OF)
    assert got["bad"].state == "unknown" and "not valid for read" in (got["bad"].reason or "")
    assert [n for n in names if got[n].state == "used"] == names[:50]
    sizes = [len(_counts(p)) for v, p, _ in backend.calls if p.startswith("RetrieveTotal")]
    assert max(sizes) <= 50


def test_v9_zero_without_an_entity_set_is_unknown_and_unread(make_fake_backend):
    backend = make_fake_backend(responses={"get": _v9_org({}, rows=set())})
    target = EntityTarget(name="odd", set_name="", primary_id="oddid")
    got = usage.probe_v9_entities(backend, [target], clock=lambda: AS_OF)
    assert got["odd"].state == "unknown"
    # one count request only: an empty set name would GET the service root
    assert [p for _, p, _ in backend.calls if not p.startswith("RetrieveTotal")] == []


def test_v9_virtual_and_private_are_not_probed(make_fake_backend):
    backend = make_fake_backend(forbid=("get",))
    targets = [_target("v", skip="virtual table"), _target("p", skip="private entity")]
    got = usage.probe_v9_entities(backend, targets, clock=lambda: AS_OF)
    assert [(u.state, u.reason) for u in got.values()] == [
        ("unknown", "virtual table"),
        ("unknown", "private entity"),
    ]


def test_v9_transport_failure_stops_the_probe(make_fake_backend):
    err = D365Error("Request failed: boom")  # no status: not a per-entity answer
    backend = make_fake_backend(errors={"get": err})
    with pytest.raises(D365Error):
        usage.probe_v9_entities(backend, [_target("a")], clock=lambda: AS_OF)


class _FakeLegacy:
    """The legacy client's `pages`: one record for entities in *rows*, a fault for *fail*."""

    def __init__(self, rows=(), fail=(), over_limit=()):
        self.rows, self.fail, self.over_limit = set(rows), dict(fail), set(over_limit)
        self.fetches: list[str] = []

    def pages(self, fetch_xml, *, page_size=5000, start=None):
        self.fetches.append(fetch_xml)
        entity = re.findall(r'<entity name="([^"]+)"', fetch_xml)[0]
        if 'aggregate="true"' in fetch_xml and entity in self.over_limit:
            raise D365Error("AggregateQueryRecordLimit exceeded", status=500)
        if entity in self.fail:
            raise D365Error(self.fail[entity], status=500)
        if 'aggregate="true"' in fetch_xml:
            return iter([_Page([{"n": 228}])])
        return iter([_Page([{"id": "x"}] if entity in self.rows else [])])


class _Page:
    def __init__(self, records):
        self.records = records


def test_legacy_existence_read_per_entity_with_unreadable_unknown():
    client = _FakeLegacy(rows={"account"}, fail={"postrole": "RetrieveMultiple not supported"})
    ids = {"account": "accountid", "empty": "emptyid", "postrole": "postroleid"}
    got = usage.probe_legacy_entities(client, ids, clock=lambda: AS_OF)  # pyright: ignore[reportArgumentType]
    assert got["account"] == Usage("used", "record_presence", None, AS_OF)
    assert got["empty"].state == "unused"
    assert got["postrole"].state == "unknown"
    assert "not supported" in (got["postrole"].reason or "")
    # the primary id comes from metadata, and the read is page size 1 (no fetch top=)
    assert all("<attribute name=" in f and "top=" not in f for f in client.fetches)


def test_legacy_entity_without_a_primary_id_is_unknown_and_unread():
    client = _FakeLegacy()
    got = usage.probe_legacy_entities(client, {"intersect": ""}, clock=lambda: AS_OF)  # pyright: ignore[reportArgumentType]
    assert got["intersect"].state == "unknown" and client.fetches == []


def test_legacy_aggregate_limit_is_at_least_50000_never_unused():
    client = _FakeLegacy(rows={"account", "asyncoperation"}, over_limit={"asyncoperation"})
    ids = {"account": "accountid", "asyncoperation": "asyncoperationid"}
    got = usage.probe_legacy_entities(client, ids, with_counts=True, clock=lambda: AS_OF)  # pyright: ignore[reportArgumentType]
    assert got["account"].value == 228
    assert got["asyncoperation"].state == "used"
    assert got["asyncoperation"].value == 50000
    assert got["asyncoperation"].reason == "at least 50,000"


def _rows():
    return [{"n": "a"}, {"n": "b"}, {"n": "c"}]


def _states():
    return {
        "a": Usage("used", "record_presence", 1, AS_OF),
        "b": Usage("unused", "record_presence", 0, AS_OF),
        "c": Usage("unknown", "record_presence", None, AS_OF, "why"),
    }


def test_attach_in_use_drops_unused_only():
    out = usage.attach(_rows(), _states(), key=lambda r: r["n"], exclude_unknown=False)
    assert [r["n"] for r in out] == ["a", "c"]
    assert out[1]["usage"] == {
        "state": "unknown",
        "signal": "record_presence",
        "value": None,
        "as_of": AS_OF,
        "reason": "why",
    }


def test_attach_exclude_unknown_keeps_proven_used_only():
    out = usage.attach(_rows(), _states(), key=lambda r: r["n"], exclude_unknown=True)
    assert [r["n"] for r in out] == ["a"]


def test_progress_lines_throttle_and_always_print_the_last():
    lines: list[str] = []
    now = [0.0]
    p = usage.UsageProgress(lines.append, "entities", clock=lambda: now[0])
    p(0, 3)
    p(1, 3)  # inside the same second: dropped
    now[0] = 2.0
    p(2, 3)
    p(3, 3)
    assert lines == [
        "[crm] usage stage=entities total=3\n",
        "[crm] usage stage=entities done=2/3\n",
        "[crm] usage stage=entities done=3/3\n",
    ]
