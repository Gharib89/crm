"""`crm inventory readiness`: the per-gate checklist read from an `assess --target online` file."""

# pyright: basic

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory as inv
from crm.core import inventory_assess as assess
from crm.core import inventory_readiness as readiness
from crm.tests.test_inventory_assess import ORG, _assembly, _record

SSRS_GAP = inv.ExtractionGap(
    None,
    None,
    "shared data sources of SSRS reports",
    "a report's shared data source is defined in SSRS, which the SOAP endpoint does not reach",
)


def _step(name: str, entity: str, folder: Path) -> inv.Record:
    fields = {
        "message": "Create",
        "primary_entity": entity,
        "stage": 40,
        "mode": 0,
        "plugin_type": "Contoso.Plugins.Handler",
        "name": name,
    }
    return _record("step", fields, folder)


def _write_inventory(folder: Path, *, version: str = "9.1.0.1", gaps=(SSRS_GAP,), extra=()) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    records = [
        _assembly("Contoso.Open", folder, source_type=0, isolation_mode=1),
        _assembly("Contoso.Gac", folder, source_type=2, isolation_mode=2),
        _assembly("Contoso.Clean", folder, source_type=0, isolation_mode=2),
        _step("On user create", "systemuser", folder),
        _step("On account create", "account", folder),
        *extra,
    ]
    counts: dict[str, int] = {}
    for record in records:
        counts[record.kind] = counts.get(record.kind, 0) + 1
    manifest = inv.Inventory(
        schema_version=inv.SCHEMA_VERSION,
        source=inv.Source("crm2011", ORG, None, version),
        extracted_at="2026-11-01T10:00:00Z",
        cli_version="1.91.0",
        scope=inv.Scope("org", ()),
        publication="published",
        records=tuple(records),
        coverage={k: inv.KindCoverage("complete", 1, n, False, ()) for k, n in counts.items()},
        gaps=tuple(gaps),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")
    return folder


def _findings_file(tmp_path: Path, target="online", rules=None, **kw) -> Path:
    folder = _write_inventory(tmp_path / "inv", **kw)
    path = tmp_path / "findings.json"
    path.write_text(
        assess.dump_findings(assess.assess(folder, target, rules=rules)), encoding="utf-8"
    )
    return path


def _rows(path: Path, rules=None) -> dict[str, readiness.Row]:
    return {r.gate: r for r in readiness.readiness(path, rules).rows}


def test_each_gate_reads_the_findings_of_its_rule(tmp_path):
    rows = _rows(_findings_file(tmp_path))
    assert rows["assembly-storage-online"].status == "fail"
    assert rows["assembly-isolation-none"].status == "warn"
    assert "sandbox" in rows["assembly-isolation-none"].note
    assert rows["step-on-systemuser"].status == "fail"
    # Two of the three assemblies are flagged by each assembly rule; the clean one is not
    # counted under a failure, and the steps off systemuser are `clear`.
    assert rows["assembly-storage-online"].components == 1
    assert rows["step-on-systemuser"].components == 1
    # The clean assembly is counted: storage fails 1 of 3 assemblies, and says 2 pass it.
    assert "2 of 3 pass this gate" in rows["assembly-storage-online"].note
    assert rows["csproj-sdk-net462"].status == "unknown"  # needs --source-map and --repo


def test_a_gate_with_components_and_no_finding_passes_and_counts_them(tmp_path):
    findings = _findings_file(tmp_path)
    data = json.loads(findings.read_text(encoding="utf-8"))
    for f in data["findings"]:
        if f["rule"] == "step-on-systemuser":
            f.update({"class": "clear", "rule": None, "evidence": None})
    findings.write_text(json.dumps(data), encoding="utf-8")
    row = _rows(findings)["step-on-systemuser"]
    assert (row.status, row.components) == ("pass", 2)
    assert row.display_status == "pass"


def test_a_gate_with_no_components_reads_pass_with_zero(tmp_path):
    row = _rows(_findings_file(tmp_path))["web-resource-crmform"]
    assert (row.status, row.components, row.display_status) == ("pass", 0, "pass (0 components)")


def test_a_step_off_systemuser_is_clear_not_unknown(tmp_path):
    folder = _write_inventory(tmp_path / "inv")
    result = assess.assess(folder, "online")
    off = next(f for f in result.findings if f.key.startswith("Create|account|"))
    assert (off.cls, off.rule, off.reason) == ("clear", None, None)
    on = next(f for f in result.findings if f.key.startswith("Create|systemuser|"))
    assert (on.cls, on.rule) == ("blocker", "step-on-systemuser")


@pytest.mark.parametrize(
    ("version", "status"),
    [
        ("9", "pass"),
        ("9.0.0.1", "pass"),
        ("9.1.0.1", "pass"),
        ("8.2.2.112", "fail"),
        ("5.0.9690.4376", "fail"),
    ],
)
def test_the_source_version_row_decides_on_9_0(tmp_path, version, status):
    row = readiness.readiness(_findings_file(tmp_path, version=version)).rows[0]
    assert (row.gate, row.status) == ("source-version", status)
    assert version in row.note


def test_an_unreadable_source_version_is_unknown(tmp_path):
    row = readiness.readiness(_findings_file(tmp_path, version="unknown")).rows[0]
    assert (row.gate, row.status) == ("source-version", "unknown")


def test_the_ssrs_gap_makes_the_reports_row_unknown_with_its_reason(tmp_path):
    row = _rows(_findings_file(tmp_path))["report-sql-datasource"]
    assert row.status == "unknown"
    assert SSRS_GAP.reason in row.note
    without = _rows(_findings_file(tmp_path / "again", gaps=()))["report-sql-datasource"]
    assert without.status == "pass"


def test_a_sql_report_the_extract_flags_not_custom_still_fails_the_reports_row(tmp_path):
    # #1272: the gate follows the rule's findings, which key on origin, not `custom`.
    fields = {"name": "Own SQL", "report_type": 1, "custom": False, "data_provider": "sql"}
    report = _record("report", fields, tmp_path)
    row = _rows(_findings_file(tmp_path, gaps=(), extra=(report,)))["report-sql-datasource"]
    assert (row.status, row.components) == ("fail", 1)


def test_a_findings_file_for_another_target_is_refused(tmp_path):
    path = _findings_file(tmp_path, target="onprem-9.1")
    with pytest.raises(inv.InventoryError, match="--target online"):
        readiness.readiness(path)
    res = CliRunner().invoke(cli, ["inventory", "readiness", str(path)])
    assert res.exit_code == 2 and "--target online" in res.output


def test_every_row_has_a_basis_and_the_manual_gates_carry_no_status(tmp_path):
    result = readiness.readiness(_findings_file(tmp_path))
    assert all(r.basis for r in result.rows)
    assert {r.gate: r.basis for r in result.rows}["source-version"].startswith("OP2OL whitepaper")
    assert result.manual
    assert all(set(m) == {"gate", "basis"} for m in result.manual)
    assert any("collation" in m["gate"] for m in result.manual)


def test_a_rule_added_to_a_rules_file_adds_a_row_with_no_change_to_the_verb(tmp_path):
    data = copy.deepcopy(json.loads(assess.RULES_PATH.read_text(encoding="utf-8")))
    data["rules"].append(
        {
            "id": "step-on-contact",
            "kind": "step",
            "class": "blocker",
            "title": "A step registered on contact",
            "source": {"from": "5.0", "before": None},
            "targets": ["online"],
            "detection": {"type": "field_in", "field": "primary_entity", "values": ["contact"]},
            "fix": "Move it.",
            "sources": ["https://learn.microsoft.com/power-apps/"],
        }
    )
    rules = assess.parse_rules(data, "fixture-sha")
    rows = _rows(_findings_file(tmp_path, rules=rules), rules)
    assert rows["step-on-contact"].status == "pass"
    assert rows["step-on-contact"].basis == readiness.ONLINE_BASIS


def test_findings_made_with_other_rules_are_refused(tmp_path):
    other = assess.parse_rules(json.loads(assess.RULES_PATH.read_text("utf-8")), "other-sha")
    path = _findings_file(tmp_path, rules=other)
    with pytest.raises(inv.InventoryError, match="assess"):
        readiness.readiness(path)


def test_the_gate_table_names_only_rules_that_exist():
    ids = {r.id for r in assess.load_rules().rules}
    assert set(readiness.OP2OL_GATES) <= ids


def test_json_output_is_the_rows_in_the_envelope(tmp_path):
    path = _findings_file(tmp_path)
    res = CliRunner().invoke(cli, ["--json", "inventory", "readiness", str(path)])
    assert res.exit_code == 0, res.output
    envelope = json.loads(res.output)
    assert envelope["ok"] is True
    data = envelope["data"]
    by_gate = {r["gate"]: r for r in data["rows"]}
    assert by_gate["assembly-storage-online"]["status"] == "fail"
    assert set(by_gate["assembly-storage-online"]) >= {
        "gate",
        "status",
        "components",
        "rules",
        "basis",
        "note",
    }
    assert data["target"] == "online" and data["manual"]
    assert data["source_version"] == "9.1.0.1"


def test_human_output_lists_every_row_and_exits_0_on_a_fail(tmp_path):
    res = CliRunner().invoke(cli, ["inventory", "readiness", str(_findings_file(tmp_path))])
    assert res.exit_code == 0, res.output
    assert "assembly-storage-online" in res.output and "fail" in res.output
    assert "manual" in res.output


def test_a_usage_filtered_findings_file_is_refused(tmp_path):
    path = _findings_file(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["usage_filtered"] = 2
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(inv.InventoryError, match="--in-use"):
        readiness.readiness(path)
    res = CliRunner().invoke(cli, ["inventory", "readiness", str(path)])
    assert res.exit_code == 2 and "usage_filtered" in res.output


@pytest.mark.parametrize(
    ("field", "value"), [("incomplete", ["x"]), ("unattached_gaps", "ab"), ("source_hosts", "ab")]
)
def test_a_malformed_findings_field_is_a_usage_error_not_a_traceback(tmp_path, field, value):
    path = _findings_file(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data[field] = value
    path.write_text(json.dumps(data), encoding="utf-8")
    res = CliRunner().invoke(cli, ["inventory", "readiness", str(path)])
    assert res.exit_code == 2 and field in res.output
