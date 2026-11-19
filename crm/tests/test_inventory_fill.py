"""`crm inventory extract --with-fill-counts`: column fill usage on custom `attribute` rows (#1341).

Runs the real fill probe over the synthetic CRM 2011 org of test_legacy_inventory.py, whose
client answers each not-null existence read and count from a per-column table.
"""

# pyright: basic, reportArgumentType=false
from __future__ import annotations

from xml.etree.ElementTree import fromstring

import pytest

from crm.core import inventory_extract, legacy_reads, usage
from crm.core.inventory import Record
from crm.core.inventory_assess import AssessResult
from crm.core.inventory_usage_filter import filter_findings
from crm.core.legacy import Page
from crm.tests.test_inventory_usage import UNUSED, USED, UsageSource, _answers, _extract, _run
from crm.tests.test_legacy_inventory import (
    _ENTITIES,
    FakeClient,
    _detail,
    _manifest,
    reads,  # noqa: F401  (a fixture, used through pytestmark)
)
from crm.utils.d365_backend import D365Error

pytestmark = pytest.mark.usefixtures("reads", "columns")

LIMIT = D365Error("AggregateQueryRecordLimit exceeded. Cannot perform this operation.")
CUSTOM = ("new_budget", "new_code", "new_notes", "new_photo")


def _with_columns(name: str) -> dict:
    detail = _detail(name)
    for col in CUSTOM:
        detail["attributes"].append(
            {"logical_name": col, "metadata_id": None, "type": "String", "custom": True,
             "managed": False, "required_level": "None", "option_set": None}
        )  # fmt: skip
    return detail


@pytest.fixture
def columns(monkeypatch):
    """Give every synthetic entity the custom columns in `CUSTOM`, and the primary ids."""
    monkeypatch.setattr(legacy_reads, "entity_detail", lambda c, n: _with_columns(n))
    monkeypatch.setattr(
        legacy_reads,
        "list_entities_with_primary_ids",
        lambda c: (_ENTITIES, {e["logical_name"]: f"{e['logical_name']}id" for e in _ENTITIES}),
    )


class FillClient(FakeClient):
    """Answers a not-null read of column *c* from ``fill[c]``.

    An int is the filled row count (0: no row); ``"over"`` is a filled column whose count
    the aggregate limit refuses; an exception is raised on the existence read.
    """

    def __init__(self, fill: dict, **kw):
        super().__init__(**kw)
        self.fill = fill
        self.fill_reads: list[tuple[str, str, str]] = []

    def pages(self, fetch_xml, *, page_size=5000, start=None):
        root = fromstring(fetch_xml)
        cond = root.find(".//condition[@operator='not-null']")
        if cond is None:
            yield from super().pages(fetch_xml, page_size=page_size, start=start)
            return
        entity, col = root[0].get("name"), cond.get("attribute")
        counting = root.get("aggregate") == "true"
        self.fill_reads.append((entity, col, "count" if counting else "exists"))
        answer = self.fill.get(col, 0)
        if isinstance(answer, Exception):
            raise answer
        if counting:
            if answer == "over":
                raise LIMIT
            yield _page(entity, [{"n": answer}])
        else:
            yield _page(entity, [{f"{entity}id": "x"}] if answer else [])


def _page(entity, rows):
    return Page(entity, 1, rows, False, None)


def _fill_of(out, entity="new_project") -> dict:
    data = _manifest(out)
    return {
        r["fields"]["logical_name"]: r.get("usage")
        for r in data["records"]
        if r["kind"] == "attribute" and r["fields"]["entity"] == entity
    }


def test_without_the_flag_extract_issues_no_fill_read(tmp_path):
    client = FillClient({})
    _extract(inventory_extract.LegacySource(client), tmp_path / "inv")
    assert client.fill_reads == []
    assert all(u is None for u in _fill_of(tmp_path / "inv").values())


@pytest.mark.parametrize(
    ("answer", "state", "value", "reason"),
    [
        pytest.param(0, "unused", 0, None, id="unused"),
        pytest.param(12, "used", 12, None, id="used-with-count"),
        pytest.param("over", "used", None, "AggregateQueryRecordLimit", id="used-over-limit"),
        pytest.param(
            D365Error("attribute cannot be filtered", status=400),
            "unknown",
            None,
            "attribute cannot be filtered",
            id="unknown-failed-read",
        ),
    ],
)
def test_each_fill_state(tmp_path, answer, state, value, reason):
    client = FillClient({"new_budget": answer})
    summary = _extract(
        inventory_extract.LegacySource(client), tmp_path / "inv", with_fill_counts=True
    )
    got = _fill_of(tmp_path / "inv")["new_budget"]
    assert summary.complete
    assert (got["state"], got["signal"], got["value"]) == (state, usage.FILL_SIGNAL, value)
    assert (got["reason"] is None) if reason is None else (reason in got["reason"])


def test_a_count_follows_only_a_successful_existence_read_that_found_a_row(tmp_path):
    client = FillClient({"new_budget": 3, "new_code": 0, "new_notes": D365Error("bad")})
    _extract(inventory_extract.LegacySource(client), tmp_path / "inv", with_fill_counts=True)
    reads_of = {(c, k) for e, c, k in client.fill_reads if e == "new_project"}
    assert ("new_budget", "count") in reads_of
    assert ("new_code", "count") not in reads_of
    assert ("new_notes", "count") not in reads_of


def test_columns_of_an_unused_entity_are_unused_without_a_read(tmp_path):
    client = FillClient({})
    answers = _answers()  # new_project reads unused, account used
    source = UsageSource(client, answers)
    out = tmp_path / "inv"
    data_summary = _extract(source, out, with_usage=True, with_fill_counts=True)
    assert data_summary.complete
    read_entities = {e for e, _, _ in client.fill_reads}
    assert "new_project" not in read_entities and "account" in read_entities
    assert {c for _, c, _ in client.fill_reads} <= set(CUSTOM)  # no system column
    # A column of an empty table is unused without a read: no row can fill it.
    fill = _fill_of(out)
    assert all((fill[c]["state"], fill[c]["value"]) == ("unused", 0) for c in CUSTOM)
    assert all(u is None for c, u in fill.items() if c not in CUSTOM)  # no system column
    assert _manifest(out)["scope"]["usage_read"] is True


def test_the_fill_flag_alone_marks_usage_read(tmp_path):
    _extract(
        inventory_extract.LegacySource(FillClient({})), tmp_path / "inv", with_fill_counts=True
    )
    assert _manifest(tmp_path / "inv")["scope"]["usage_read"] is True


def test_a_connection_wide_failure_stops_the_run(tmp_path):
    client = FillClient({"new_budget": D365Error("unauthorized", status=401)})
    with pytest.raises(D365Error):
        _extract(inventory_extract.LegacySource(client), tmp_path / "inv", with_fill_counts=True)


def test_a_resume_reuses_the_entities_already_read(tmp_path, monkeypatch):
    out = tmp_path / "inv"
    first = FillClient({"new_budget": 5})
    calls = {"n": 0}
    real = inventory_extract.LegacySource.probe_fill

    def flaky(self, entity, columns, *, progress):
        calls["n"] += 1
        if entity == "new_project_account" and calls["n"] < 4:
            raise D365Error("the metadata read was refused")
        return real(self, entity, columns, progress=progress)

    monkeypatch.setattr(inventory_extract.LegacySource, "probe_fill", flaky)
    summary = _extract(inventory_extract.LegacySource(first), out, with_fill_counts=True)
    assert not summary.complete and any("fill" in e for e in summary.errors)
    failed = _fill_of(out, "new_project_account")["new_budget"]
    assert failed["state"] == "unknown" and "refused" in failed["reason"]
    (gap,) = [g for g in _manifest(out)["gaps"] if "fill" in (g.get("description") or "")]
    assert gap["kind"] == "attribute" and "new_project_account" in gap["description"]
    assert gap["reason"].startswith("fill probe failed:") and gap["key"] is None

    second = FillClient({"new_budget": 5})
    summary = _extract(inventory_extract.LegacySource(second), out, with_fill_counts=True)
    assert summary.complete
    assert {e for e, _, _ in second.fill_reads} == {"new_project_account"}
    assert _fill_of(out, "new_project_account")["new_budget"]["value"] == 5


def test_the_command_flag_runs_the_fill_stage(monkeypatch, tmp_path):
    seen: list = []

    def probe(self, entity, columns, *, progress):
        seen.append(entity)
        return {c: usage.Usage("used", usage.FILL_SIGNAL, 1, "t") for c in columns}

    monkeypatch.setattr(inventory_extract.LegacySource, "probe_fill", probe)
    result, out, _ = _run(monkeypatch, tmp_path, "--with-fill-counts", "--include-system")
    assert result.exit_code == 0, result.output
    assert seen and _fill_of(out)["new_budget"]["value"] == 1


def _attribute(key: str, state: str) -> Record:
    u = {"used": USED, "unused": UNUSED}.get(state) or usage.Usage(
        "unknown", usage.FILL_SIGNAL, None, "t", "read failed"
    )
    return Record("attribute", key, None, None, "custom", False, "published", {}, (), u.to_dict())


@pytest.mark.parametrize(
    ("in_use", "exclude_unknown", "kept"),
    [(False, False, {"a", "b", "c"}), (True, False, {"a", "c"}), (True, True, {"a"})],
)
def test_the_usage_filters_apply_to_attribute_records(in_use, exclude_unknown, kept):
    records = [_attribute("a", "used"), _attribute("b", "unused"), _attribute("c", "unknown")]
    result = AssessResult({}, "online", None, (), (), {}, uncovered={"attribute": ("a", "b", "c")})
    got = filter_findings(
        result, records, (), "t", in_use=in_use, exclude_unknown=exclude_unknown, sort="default"
    )
    assert set(got.uncovered.get("attribute", ())) == kept


class _V9:
    """A Web API `get` answering fill reads from ``fill`` the way `FillClient` does."""

    def __init__(self, fill: dict):
        self.fill = fill
        self.paths: list[str] = []

    def get(self, path, params=None):
        self.paths.append(path)
        root = fromstring((params or {})["fetchXml"])
        cond = root.find(".//condition[@operator='not-null']")
        assert cond is not None
        answer = self.fill[cond.get("attribute")]
        if root.get("aggregate") == "true":
            if answer == "over":
                raise LIMIT
            return {"value": [{"n": answer}]}
        return {"value": [{"x": 1}] if answer else []}


def test_v9_reads_fill_over_the_entity_set():
    backend = _V9({"new_a": 0, "new_b": 4, "new_c": "over"})
    target = usage.EntityTarget("new_project", "new_projects", "new_projectid")
    got = usage.probe_v9_fill(backend, target, ["new_a", "new_b", "new_c"], clock=lambda: "t")
    assert set(backend.paths) == {"new_projects"}
    assert got["new_a"] == usage.Usage("unused", usage.FILL_SIGNAL, 0, "t")
    assert got["new_b"] == usage.Usage("used", usage.FILL_SIGNAL, 4, "t")
    assert got["new_c"] == usage.Usage("used", usage.FILL_SIGNAL, None, "t", usage.FILL_OVER_LIMIT)


def test_v9_a_skipped_table_reads_unknown_without_a_read():
    backend = _V9({})
    target = usage.EntityTarget("msdyn_x", "msdyn_xs", "msdyn_xid", "virtual table")
    got = usage.probe_v9_fill(backend, target, ["new_a"], clock=lambda: "t")
    assert backend.paths == []
    assert got["new_a"] == usage.Usage("unknown", usage.FILL_SIGNAL, None, "t", "virtual table")
