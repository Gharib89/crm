"""The `report` inventory kind (#1205): extraction, scope, diff and the v4 upgrade, offline.

Runs over the synthetic CRM 2011 org of test_legacy_inventory.py, to which it adds report
rows. The read is shared by every source (`_READS`), so one fake org covers both.
"""

# pyright: basic
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from crm.core import inventory, inventory_diff
from crm.core.inventory_assess import assess
from crm.tests.test_legacy_inventory import (
    _ORG,
    FakeClient,
    _extract,
    _id,
    _keys,
    _manifest,
    _ref,
    reads,  # noqa: F401  (a fixture, used through pytestmark)
)

pytestmark = pytest.mark.usefixtures("reads")

_FETCH = (
    '<Report xmlns="http://schemas.microsoft.com/sqlserver/reporting/2008/01/reportdefinition">'
    "<DataSources><DataSource Name='CRM'><ConnectionProperties>"
    "<DataProvider>MSCRMFETCH</DataProvider></ConnectionProperties></DataSource></DataSources>"
    "</Report>"
)
_SQL = _FETCH.replace("MSCRMFETCH", "SQL")
_SHARED = (
    "<Report><DataSources><DataSource Name='CRM'>"
    "<DataSourceReference>Shared</DataSourceReference></DataSource></DataSources></Report>"
)
_ENDPOINT = "the CRM 2011 SOAP endpoint"


def _report(n: int, name: str, body: str | None, **kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": _id(n), "name": name, "reporttypecode": 1, "ispersonal": False,
        "iscustomreport": True, "languagecode": 1033, "ismanaged": False,
        "solutionid": _ref("solution", 2), "bodytext": body,
    }  # fmt: skip
    return {**row, **kw}


def _org(*pages: list[dict[str, Any]]) -> dict[str, Any]:
    org = copy.deepcopy(_ORG)
    org["report"] = [list(page) for page in pages]
    return org


def _records(out: Path) -> dict[str, dict[str, Any]]:
    return {r["key"]: r for r in _manifest(out)["records"] if r["kind"] == "report"}


def test_each_report_is_a_record_keyed_by_name_with_its_provider_and_rdl_artifact(tmp_path):
    org = _org(
        [
            _report(300, "Fetch report", _FETCH),
            _report(301, "Sql report", _SQL, ispersonal=True, iscustomreport=False),
        ],
        [_report(302, "Shared report", _SHARED)],
    )
    summary = _extract(FakeClient(org=org), tmp_path / "inv")
    assert summary.complete
    data = _manifest(tmp_path / "inv")
    records = _records(tmp_path / "inv")
    assert sorted(records) == ["Fetch report", "Shared report", "Sql report"]
    fetch = records["Fetch report"]
    assert fetch["fields"] == {
        "name": "Fetch report", "report_type": 1, "personal": False, "custom": True,
        "language": 1033, "data_provider": "fetch",
    }  # fmt: skip
    assert (fetch["id"], fetch["managed"], fetch["origin"]) == (_id(300), False, "custom")
    (artifact,) = fetch["artifacts"]
    assert artifact["content"] == "text"
    assert (tmp_path / "inv" / artifact["path"]).read_text(encoding="utf-8") == _FETCH
    sql = records["Sql report"]["fields"]
    assert (sql["data_provider"], sql["personal"], sql["custom"]) == ("sql", True, False)
    shared = records["Shared report"]["fields"]
    assert shared["data_provider"] == "unknown"
    assert "Shared" in shared["data_provider_reason"]
    assert data["coverage"]["report"] == {
        "status": "complete", "pages": 2, "records": 3, "truncated": False, "errors": [],
    }  # fmt: skip
    assert not [g for g in data["gaps"] if g["kind"] == "report"]


def test_a_reporting_services_report_with_no_body_is_unknown_and_named_by_one_gap(tmp_path):
    org = _org([_report(300, "Fetch report", _FETCH), _report(301, "Bodyless", None)])
    _extract(FakeClient(org=org), tmp_path / "inv")
    data = _manifest(tmp_path / "inv")
    fields = _records(tmp_path / "inv")["Bodyless"]["fields"]
    assert fields["data_provider"] == "unknown"
    assert fields["data_provider_reason"] == f"RDL body not returned by {_ENDPOINT}"
    assert _records(tmp_path / "inv")["Bodyless"]["artifacts"] == []
    (gap,) = [g for g in data["gaps"] if g["kind"] == "report"]
    assert gap["key"] == "Bodyless"
    assert gap["reason"] == f"RDL body not returned by {_ENDPOINT}"
    # The read of the records was whole, so the kind stays complete and the diff compares
    # every other report.
    assert data["coverage"]["report"]["status"] == "complete"


def test_a_link_report_has_no_body_to_expect_and_no_gap(tmp_path):
    org = _org([_report(300, "Link", None, reporttypecode=3, bodyurl="https://x")])
    _extract(FakeClient(org=org), tmp_path / "inv")
    data = _manifest(tmp_path / "inv")
    fields = _records(tmp_path / "inv")["Link"]["fields"]
    assert fields["data_provider"] == "unknown" and fields["data_provider_reason"]
    assert not [g for g in data["gaps"] if g["kind"] == "report"]


def test_two_reports_sharing_a_name_are_ambiguous_and_keep_their_own_artifact(tmp_path):
    org = _org([_report(300, "Sales", _FETCH), _report(301, "Sales", _SQL, ispersonal=True)])
    summary = _extract(FakeClient(org=org), tmp_path / "inv")
    assert summary.complete
    inv = inventory.read_inventory(tmp_path / "inv")
    pair = inv.ambiguous()[("report", "Sales")]
    paths = {r.artifacts[0].path for r in pair}
    assert len(paths) == 2
    assert {(tmp_path / "inv" / p).read_text(encoding="utf-8") for p in paths} == {_FETCH, _SQL}


class _Spy(FakeClient):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.fetches: list[str] = []

    def pages(self, fetch_xml, **kw):
        self.fetches.append(fetch_xml)
        return super().pages(fetch_xml, **kw)


def test_the_default_scope_leaves_out_stock_reports_and_never_fetches_their_bodies(tmp_path):
    org = _org(
        [
            _report(300, "Custom", _FETCH),
            _report(301, "Stock", _SQL, ismanaged=True, solutionid=_ref("solution", 1)),
        ]
    )
    client = _Spy(org=org)
    _extract(client, tmp_path / "inv", include_system=False)
    assert sorted(_records(tmp_path / "inv")) == ["Custom"]
    # The rows come without the body column; the body is fetched by id for kept rows only.
    plain = [f for f in client.fetches if 'entity name="report"' in f and "<value>" not in f]
    assert plain and not any("bodytext" in f for f in plain)
    by_id = [f for f in client.fetches if 'entity name="report"' in f and "<value>" in f]
    assert by_id and all(_id(300) in f and _id(301) not in f for f in by_id)
    assert not [g for g in _manifest(tmp_path / "inv")["gaps"] if g["kind"] == "report"]


def test_a_stock_report_sharing_a_kept_reports_name_raises_no_gap_for_it(tmp_path):
    # The stock row is dropped unread, so its missing body must not name the custom report.
    org = _org(
        [
            _report(300, "Overview", _FETCH),
            _report(301, "Overview", None, ismanaged=True, solutionid=_ref("solution", 1)),
        ]
    )
    _extract(FakeClient(org=org), tmp_path / "inv", include_system=False)
    assert sorted(_records(tmp_path / "inv")) == ["Overview"]
    assert not [g for g in _manifest(tmp_path / "inv")["gaps"] if g["kind"] == "report"]


def test_include_system_keeps_stock_reports_with_their_bodies(tmp_path):
    org = _org([_report(301, "Stock", _SQL, ismanaged=True, solutionid=_ref("solution", 1))])
    _extract(FakeClient(org=org), tmp_path / "inv", include_system=True)
    stock = _records(tmp_path / "inv")["Stock"]
    assert (stock["managed"], stock["origin"]) == (True, "system")
    assert stock["fields"]["data_provider"] == "sql" and stock["artifacts"]


def test_a_solution_run_finds_reports_through_membership_and_keeps_stock(tmp_path):
    org = _org(
        [
            _report(300, "Member", _FETCH),
            _report(301, "Other", _SQL),
            _report(302, "Stock member", _SQL, ismanaged=True, solutionid=_ref("solution", 1)),
        ]
    )
    for n in (300, 302):
        org["solutioncomponent"][0].append(
            {"id": _id(900 + n), "solutionid": _ref("solution", 2), "componenttype": 31,
             "objectid": _id(n)}
        )  # fmt: skip
    _extract(FakeClient(org=org), tmp_path / "inv", solutions=("contoso",))
    assert sorted(_records(tmp_path / "inv")) == ["Member", "Stock member"]
    assert "contoso|report|Member" in _keys(_manifest(tmp_path / "inv"), "solution_membership")


def test_the_ssrs_gap_names_the_shared_data_sources_the_endpoint_does_not_reach(tmp_path):
    _extract(FakeClient(org=_org()), tmp_path / "inv")
    (gap,) = [g for g in _manifest(tmp_path / "inv")["gaps"] if "SSRS" in (g["description"] or "")]
    assert gap["description"] == "shared data sources of SSRS reports"
    assert gap["reason"] == (
        f"a report's shared data source is defined in SSRS, which {_ENDPOINT} does not reach"
    )
    assert gap["kind"] is None and gap["key"] is None


def _diff(tmp_path: Path, a: list[dict[str, Any]], b: list[dict[str, Any]]):
    _extract(FakeClient(org=_org(a)), tmp_path / "a")
    _extract(FakeClient(org=_org(b)), tmp_path / "b")
    diff = inventory_diff.diff_inventories(
        *inventory_diff.read_pair(tmp_path / "a", tmp_path / "b")
    )
    return {e.key: e for e in diff.entries if e.kind == "report"}


def test_diff_lists_added_removed_and_changed_reports(tmp_path):
    entries = _diff(
        tmp_path,
        [_report(300, "Gone", _FETCH), _report(301, "Same", _FETCH), _report(302, "Edit", _FETCH)],
        [_report(301, "Same", _FETCH), _report(302, "Edit", _SQL), _report(303, "New", _FETCH)],
    )
    verdicts = {k: e.verdict for k, e in entries.items()}
    assert verdicts == {"Gone": "removed", "Same": "unchanged", "Edit": "changed", "New": "added"}
    names = {c.name for c in entries["Edit"].fields}
    assert "fields.data_provider" in names
    assert entries["Edit"].artifacts


def test_a_report_the_gap_names_is_not_comparable_and_the_others_still_diff(tmp_path):
    entries = _diff(
        tmp_path,
        [_report(300, "Bodyless", None), _report(301, "Edit", _FETCH)],
        [_report(300, "Bodyless", _FETCH), _report(301, "Edit", _SQL)],
    )
    assert entries["Bodyless"].verdict == "not_comparable"
    assert "extraction gap on A" in (entries["Bodyless"].reason or "")
    assert entries["Edit"].verdict == "changed"


def test_two_same_name_reports_diff_as_ambiguous(tmp_path):
    entries = _diff(
        tmp_path,
        [_report(300, "Sales", _FETCH), _report(301, "Sales", _SQL)],
        [_report(300, "Sales", _FETCH)],
    )
    assert entries["Sales"].verdict == "ambiguous"


def _v3(tmp_path: Path, name: str) -> Path:
    """The extraction of the synthetic org rewritten as a schema_version 3 manifest."""
    folder = tmp_path / name
    _extract(FakeClient(org=_org([_report(300, "Sales", _FETCH)])), folder)
    data = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    data["schema_version"] = 3
    data["records"] = [r for r in data["records"] if r["kind"] != "report"]
    del data["coverage"]["report"]
    data["gaps"] = [g for g in data["gaps"] if g["kind"] != "report"]
    (folder / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


def test_a_pre_v4_side_reads_the_report_kind_as_not_comparable_never_as_no_reports(tmp_path):
    older = _v3(tmp_path, "older")
    _extract(FakeClient(org=_org([_report(300, "Sales", _FETCH)])), tmp_path / "newer")
    inv_a, inv_b = inventory_diff.read_pair(older, tmp_path / "newer")
    assert inv_a.coverage["report"].status == "unsupported"
    assert inv_a.coverage["report"].errors == (
        "not read: a schema_version 3 extraction predates the report kind",
    )
    diff = inventory_diff.diff_inventories(inv_a, inv_b)
    (entry,) = [e for e in diff.entries if e.kind == "report"]
    assert entry.verdict == "not_comparable"
    assert diff.incomplete()["report"] == {"a": "unsupported", "b": "complete"}


def test_assess_judges_a_report_on_online_and_rolls_it_up_with_no_rule_on_prem(tmp_path):
    org = _org([_report(300, "Sales", _FETCH)])
    _extract(FakeClient(org=org), tmp_path / "inv")
    online = assess(tmp_path / "inv", "online")
    mine = [f for f in online.findings if f.kind == "report"]
    assert [(f.key, f.cls, f.reason) for f in mine] == [("Sales", "clear", None)]
    assert "report" not in online.uncovered
    onprem = assess(tmp_path / "inv", "onprem-9.1")
    assert not [f for f in onprem.findings if f.kind == "report"]
    assert onprem.uncovered["report"] == ("Sales",)
