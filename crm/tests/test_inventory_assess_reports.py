"""`crm inventory assess`: the report_data_provider detection, `report-sql-datasource` (#1206)."""

# pyright: basic

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import jsonschema
import pytest

from crm.core import inventory as inv
from crm.core import inventory_assess as assess
from crm.core.inventory_assess import InventoryError
from crm.tests.test_inventory_assess import SCHEMAS, _record
from crm.tests.test_inventory_assess_xaml import _inventory

RULE = "report-sql-datasource"
UNKNOWN_REASON = "shared data source 'Corp' is defined only in Reporting Services"


def _report(
    name: str,
    provider: str,
    *,
    custom: bool = False,
    origin: str = "custom",
    report_type: int = 1,
    reason: str | None = None,
) -> inv.Record:
    fields = {
        "name": name,
        "report_type": report_type,
        "custom": custom,
        "data_provider": provider,
        **({"data_provider_reason": reason} if reason else {}),
    }
    return dataclasses.replace(_record("report", fields, Path(".")), origin=origin)


def _records() -> list[inv.Record]:
    return [
        _report("Custom SQL", "sql"),
        _report("Custom Fetch", "fetch"),
        _report("Custom unknown", "unknown", reason=UNKNOWN_REASON),
        _report("Stock SQL", "sql", origin="system"),
        _report(
            "Link report", "unknown", report_type=3, reason="the report has no RDL body to classify"
        ),
        _report(
            "Excel report",
            "unknown",
            report_type=2,
            reason="the report has no RDL body to classify",
        ),
    ]


def _found(tmp_path: Path, target: str) -> dict[str, list]:
    result = assess.assess(_inventory(tmp_path, _records()), target)
    out: dict[str, list] = {}
    for f in result.findings:
        out.setdefault(f.key, []).append(f)
    return out


def _rule():
    (rule,) = [r for r in assess.load_rules().rules if r.id == RULE]
    return rule


def test_a_custom_sql_report_is_a_blocker_on_online(tmp_path):
    (hit,) = _found(tmp_path, "online")["Custom SQL"]
    assert (hit.cls, hit.rule, hit.reason) == ("blocker", RULE, None)
    assert isinstance(hit.evidence, assess.FieldEvidence)
    assert (hit.evidence.field, hit.evidence.value) == ("data_provider", "sql")


def test_a_custom_fetch_report_is_clear(tmp_path):
    assert [(f.cls, f.rule) for f in _found(tmp_path, "online")["Custom Fetch"]] == [
        ("clear", None)
    ]


def test_an_unknown_provider_carries_the_recorded_reason(tmp_path):
    (hit,) = _found(tmp_path, "online")["Custom unknown"]
    assert (hit.cls, hit.rule, hit.reason) == ("unknown", RULE, UNKNOWN_REASON)


def test_the_rule_judges_reporting_services_reports_of_a_non_system_origin_only(tmp_path):
    found = _found(tmp_path, "online")
    for name in ("Stock SQL", "Link report", "Excel report"):
        assert [(f.cls, f.rule) for f in found[name]] == [("clear", None)], name


@pytest.mark.parametrize("custom", [False, True, None])
def test_the_iscustomreport_flag_does_not_decide_the_rule(tmp_path, custom):
    # #1272: a 2011 org's SQL reports carry custom=false yet record origin custom.
    folder = _inventory(tmp_path, [_report("Flagged", "sql", custom=custom)])
    (hit,) = [f for f in assess.assess(folder, "online").findings if f.key == "Flagged"]
    assert (hit.cls, hit.rule) == ("blocker", RULE)


def test_a_system_origin_sql_report_stays_clear_whatever_its_custom_flag(tmp_path):
    folder = _inventory(tmp_path, [_report("Stock", "sql", custom=True, origin="system")])
    (hit,) = [f for f in assess.assess(folder, "online").findings if f.key == "Stock"]
    assert (hit.cls, hit.rule) == ("clear", None)


def test_an_unknown_provider_is_unknown_whatever_the_custom_flag(tmp_path):
    folder = _inventory(tmp_path, [_report("Odd", "unknown", reason=UNKNOWN_REASON)])
    (hit,) = [f for f in assess.assess(folder, "online").findings if f.key == "Odd"]
    assert (hit.cls, hit.rule, hit.reason) == ("unknown", RULE, UNKNOWN_REASON)


def test_a_sql_report_of_unknown_origin_is_unknown_never_clear(tmp_path):
    folder = _inventory(tmp_path, [_report("Who", "sql", origin="unknown")])
    (hit,) = [f for f in assess.assess(folder, "online").findings if f.key == "Who"]
    assert (hit.cls, hit.rule) == ("unknown", RULE)
    assert hit.reason and "origin" in hit.reason


def test_a_fetch_report_of_unknown_origin_is_clear(tmp_path):
    folder = _inventory(tmp_path, [_report("Fetchy", "fetch", origin="unknown")])
    (hit,) = [f for f in assess.assess(folder, "online").findings if f.key == "Fetchy"]
    assert (hit.cls, hit.rule) == ("clear", None)


def test_no_finding_of_the_rule_on_the_onprem_target(tmp_path):
    found = _found(tmp_path, "onprem-9.1")
    assert not [f for fs in found.values() for f in fs if f.rule == RULE]


def test_a_partial_report_coverage_keeps_its_unknown_beside_the_rule_finding(tmp_path):
    folder = _inventory(tmp_path, _records())
    path = folder / inv.MANIFEST_NAME
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["coverage"]["report"]["status"] = "partial"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    hits = [f for f in assess.assess(folder, "online").findings if f.key == "Custom SQL"]
    assert {(f.cls, f.rule, f.reason) for f in hits} == {
        ("blocker", RULE, None),
        ("unknown", None, "kind coverage partial"),
    }


def test_a_custom_report_with_no_recorded_provider_is_unknown(tmp_path):
    record = _record("report", {"name": "Bare", "report_type": 1}, Path("."))
    folder = _inventory(tmp_path, [record])
    (hit,) = [f for f in assess.assess(folder, "online").findings if f.key == "Bare"]
    assert (hit.cls, hit.rule, hit.reason) == ("unknown", RULE, "field data_provider not recorded")


def test_a_report_with_an_unrecorded_report_type_is_unknown_never_clear(tmp_path):
    fields = {"name": "Odd", "report_type": None, "data_provider": "sql"}
    folder = _inventory(tmp_path, [_record("report", fields, Path("."))])
    (hit,) = [f for f in assess.assess(folder, "online").findings if f.key == "Odd"]
    assert (hit.cls, hit.rule, hit.reason) == ("unknown", RULE, "field report_type not recorded")


def test_a_non_rdl_report_is_clear(tmp_path):
    fields = {"name": "Link", "report_type": 3, "data_provider": "unknown"}
    folder = _inventory(tmp_path, [_record("report", fields, Path("."))])
    (hit,) = [f for f in assess.assess(folder, "online").findings if f.key == "Link"]
    assert (hit.cls, hit.rule) == ("clear", None)


def test_the_rule_says_online_runs_fetch_reports_and_the_migration_removes_the_rest():
    rule = _rule()
    assert rule.cls == "blocker" and rule.targets == ("online",) and rule.kind == "report"
    text = f"{rule.title} {rule.fix}".lower()
    assert "fetch" in text and "removes" in text and "fetchxml" in text
    assert "sandbox" in text
    assert "sql2fetchxml" not in text and "does not stop" not in text
    assert any("writing-reports" in s for s in rule.sources)
    assert any("dn920273" in s for s in rule.sources)
    assert any("rdl-sandboxing" in s for s in rule.sources)


def test_the_rules_file_still_validates_against_its_schema():
    schema = json.loads((SCHEMAS / "compatibility-rules-v1.schema.json").read_text("utf-8"))
    data = json.loads(assess.RULES_PATH.read_text("utf-8"))
    jsonschema.validate(data, schema)
    assert data["version"] >= 8


def test_report_data_provider_applies_to_report_only():
    data = json.loads(assess.RULES_PATH.read_text("utf-8"))
    rule = next(r for r in data["rules"] if r["id"] == RULE)
    rule = {**rule, "kind": "form"}
    with pytest.raises(InventoryError, match="report_data_provider applies to report only"):
        assess.parse_rules({**data, "rules": [rule]})


_OLD_CLAIM = re.compile(r"only\s+accepts\s+(?:an\s+)?RDLs?\s+(?:whose|using)", re.I)


def test_the_unsourced_fetch_only_claim_is_gone_from_the_docs_and_the_module():
    root = Path(__file__).resolve().parents[2]
    files = [
        root / "crm" / "core" / "report.py",
        root / "docs" / "how-to" / "report.md",
        root / "crm" / "skills" / "reference" / "themes-reports.md",
    ]
    for path in files:
        text = re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))
        assert not _OLD_CLAIM.search(text), path
