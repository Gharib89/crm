"""`crm inventory assess --checker-report`: a Solution Checker report merged in (#1197).

The fixture report is synthetic (`fixtures/inventory/checker/report.sarif`): the SARIF layout
`pac solution check` writes, with invented names. Nothing here runs `pac` or calls the service.
"""

# pyright: basic

from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, cast

import jsonschema
import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory as inv
from crm.core import inventory_assess as assess
from crm.core.inventory_checker import load_overrides
from crm.core.inventory_packages import build_packages, write_packages
from crm.core.inventory_source_map import dump_source_map, map_source, read_source_map
from crm.tests.test_inventory_assess import SCHEMAS
from crm.tests.test_inventory_convert import ORG, _record

FIXTURE = Path(__file__).parent / "fixtures" / "inventory" / "checker" / "report.sarif"
LEGACY = "new_/scripts/legacy.js"
ONLY = "new_/scripts/only-checker.js"
CLEAN = "new_/scripts/clean.js"
#: Line 2 and line 3 are packaged hits (soap-2011, xrm-page); the checker flags both, and more.
LEGACY_JS = (
    "var a = 1;\n"
    'var s = "/XRMServices/2011/Organization.svc/web";\n'
    'Xrm.Page.getAttribute("x");\n'
    'eval("x");\n'
    "\n"
    "var b = 2;\n"
    "var c = 3;\n"
)


@pytest.fixture(scope="module")
def folder(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("checker")
    folder = root / "inv"
    folder.mkdir()
    records = [
        _record(LEGACY, LEGACY_JS, folder),
        _record(ONLY, "var a = 1;\n", folder),
        _record(CLEAN, "var a = 1;\n", folder),
    ]
    manifest = inv.Inventory(
        schema_version=inv.SCHEMA_VERSION,
        source=inv.Source("crm2011", ORG, None, "5.0.9690.4376"),
        extracted_at="2026-11-01T10:00:00Z",
        cli_version="1.119.0",
        scope=inv.Scope("org", ()),
        publication="published",
        records=tuple(records),
        coverage={"web_resource": inv.KindCoverage("complete", 1, len(records), False, ())},
        gaps=(),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")
    return folder


@pytest.fixture(scope="module")
def merged(folder):
    return assess.assess(folder, "online", checker_report=FIXTURE)


def _one_result_report(path: Path, rule: str, uri: str, line: int, severity: str = "High") -> Path:
    """Write a SARIF report holding one *rule* result on *line* of web resource *uri*."""
    location = {
        "physicalLocation": {
            "artifactLocation": {"uri": f"/{uri}"},
            "region": {"startLine": line, "startColumn": 1},
        }
    }
    result = {
        "ruleId": rule,
        "properties": {"severity": severity},
        "message": {"text": "m"},
        "locations": [location],
    }
    run = {"tool": {"driver": {"rules": []}}, "results": [result]}
    path.write_text(json.dumps({"version": "2.1.0", "runs": [run]}), encoding="utf-8")
    return path


def _rows(result, key: str, *, checker_only: bool = True) -> list[assess.Finding]:
    packaged = {r.id for r in result.rules.rules}
    return [
        f
        for f in result.findings
        if f.key == key and (not checker_only or (f.rule and f.rule not in packaged))
    ]


def _by_rule(result, key: str) -> dict[str, list[assess.Finding]]:
    out: dict[str, list[assess.Finding]] = {}
    for f in _rows(result, key):
        out.setdefault(f.rule or "", []).append(f)
    return out


def test_a_matched_result_is_a_finding_with_the_inventory_artifact_and_lines(folder, merged):
    record = next(r for r in inv.read_inventory(folder).records if r.key == LEGACY)
    (artifact,) = record.artifacts
    (api,) = _by_rule(merged, LEGACY)["web-avoid-2011-api"]
    assert (api.cls, api.reason) == ("blocker", None)
    assert isinstance(api.evidence, assess.ContentEvidence)
    assert (api.evidence.path, api.evidence.sha256) == (artifact.path, artifact.sha256)
    assert api.evidence.locations == ((4, 5),) and api.evidence.end_line == 5
    assert api.evidence.match_count == 1
    assert api.to_dict()["evidence"]["end_line"] == 5


@pytest.mark.parametrize(
    ("rule", "cls"),
    [
        ("web-avoid-crm2011-service-soap", "blocker"),
        ("web-avoid-2011-api", "blocker"),
        ("web-use-strict-mode", "deprecated"),
        ("web-use-example-low", "optional"),
        ("web-use-example-info", "optional"),
        ("web-use-async", "deprecated"),
        ("web-avoid-eval", "deprecated"),
    ],
)
def test_the_class_follows_the_severity_and_the_override_table(merged, rule, cls):
    (hit, *_) = _by_rule(merged, LEGACY)[rule]
    assert hit.cls == cls


def test_the_shipped_override_table_holds_two_entries():
    table = load_overrides()
    assert table == {"web-use-async": "deprecated", "web-avoid-eval": "deprecated"}


def test_an_override_in_another_table_changes_the_class_without_code(folder, tmp_path):
    table = tmp_path / "overrides.json"
    table.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "overrides": [
                    {"rule": "web-use-strict-mode", "class": "blocker", "why": "test"},
                    {"rule": "web-avoid-2011-api", "class": "optional", "why": "test"},
                ],
            }
        ),
        encoding="utf-8",
    )
    result = assess.assess(folder, "online", checker_report=FIXTURE, checker_overrides=table)
    by_rule = _by_rule(result, LEGACY)
    assert by_rule["web-use-strict-mode"][0].cls == "blocker"
    assert by_rule["web-avoid-2011-api"][0].cls == "optional"
    # Unlisted rules fall back to the severity: the shipped override no longer applies.
    assert by_rule["web-use-async"][0].cls == "blocker"


def test_the_sarif_level_never_decides_the_class(merged):
    # web-use-strict-mode carries no `level` at all, and is Medium: deprecated, not dropped.
    assert _by_rule(merged, ONLY)["web-use-strict-mode"][0].cls == "deprecated"


def test_a_result_for_a_file_outside_the_inventory_or_a_non_web_rule_is_listed_not_found(merged):
    assert merged.checker is not None
    unmatched = {(u["rule"], u["uri"]): u for u in merged.checker.unmatched}
    missing = unmatched[("web-use-async", "/new_/scripts/missing.js")]
    assert missing["severity"] == "Critical" and (missing["line"], missing["column"]) == (3, 1)
    assert "web-use-async" in missing["message"] and missing["reason"]
    assert unmatched[("meta-remove-example", "/solution.xml")]["reason"]
    assert len(unmatched) == 2
    for key in ("new_/scripts/missing.js", "/solution.xml"):
        assert not [f for f in merged.findings if f.key == key]


def test_a_result_overlapping_a_packaged_finding_of_its_rule_is_skipped_and_counted(merged):
    by_rule = _by_rule(merged, LEGACY)
    # Line 2 is the packaged soap-2011 hit, line 7 is not: only line 7 stays.
    soap = by_rule["web-avoid-crm2011-service-soap"]
    assert [cast(Any, f.evidence).locations for f in soap] == [((7, 3),)]
    # Line 3 is the packaged xrm-page hit, which names web-use-client-context as its counterpart.
    assert "web-use-client-context" not in by_rule
    assert merged.checker is not None and merged.checker.skipped == 2
    packaged = {f.rule for f in merged.findings if f.key == LEGACY}
    assert {"web-resource-soap-2011", "web-resource-xrm-page"} <= packaged


def test_a_packaged_finding_is_never_dropped_by_the_merge(folder, merged):
    plain = assess.assess(folder, "online")
    kept = {assess._canonical(f.to_dict()) for f in merged.findings}
    assert {assess._canonical(f.to_dict()) for f in plain.findings if f.cls != "clear"} <= kept


def test_a_component_with_a_checker_finding_is_no_longer_clear(merged):
    assert {f.cls for f in merged.findings if f.key == ONLY} == {"deprecated"}
    assert {f.cls for f in merged.findings if f.key == CLEAN} == {"clear"}


def test_the_findings_file_records_the_report_and_each_rule_with_its_class_and_link(merged):
    body = json.loads(assess.dump_findings(merged, generated_at="2026-11-11T00:00:00Z"))
    sha = __import__("hashlib").sha256(FIXTURE.read_bytes()).hexdigest()
    assert body["checker_report"] == {"sha256": sha, "results": 12, "skipped_duplicates": 2}
    assert len(body["checker_unmatched"]) == 2
    entry = body["rules_hit"]["web-avoid-2011-api"]
    assert entry["class"] == "blocker" and entry["title"] == "Avoid the 2011 API"
    assert entry["fix"] == "Use the current client API."
    assert entry["sources"] == [
        "https://learn.microsoft.com/power-apps/maker/data-platform/powerapps-checker/rules/web/avoid-2011-api"
    ]
    jsonschema.validate(
        body, json.loads((SCHEMAS / "compatibility-findings-v2.schema.json").read_text("utf-8"))
    )


def test_the_findings_file_reads_back_with_checker_findings(folder, merged, tmp_path):
    path = tmp_path / "findings.json"
    path.write_text(assess.dump_findings(merged), encoding="utf-8")
    read = assess.read_findings(path)
    rules = {f.rule for f in read.findings}
    assert "web-avoid-2011-api" in rules
    assert read.sources["web-avoid-2011-api"]


def test_without_the_option_the_findings_file_holds_only_todays_keys(folder):
    body = json.loads(assess.dump_findings(assess.assess(folder, "online")))
    assert set(body) == {
        "schema_version",
        "generated_at",
        "inventory",
        "target",
        "rules",
        "rules_hit",
        "findings",
        "uncovered_kinds",
        "unattached_gaps",
        "incomplete",
    }
    assert "checker" not in assess.assess(folder, "online").summary()
    assert all("end_line" not in (f["evidence"] or {}) for f in body["findings"])
    assert {f["rule"] for f in body["findings"]} <= {r.id for r in assess.load_rules().rules} | {
        None
    }


def test_the_zip_pac_writes_and_the_bare_sarif_give_the_same_findings(folder, tmp_path):
    archive = tmp_path / "report.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("nested/result.sarif", FIXTURE.read_text("utf-8"))
        z.writestr("readme.txt", "not a report")
    from_zip = assess.assess(folder, "online", checker_report=archive)
    from_sarif = assess.assess(folder, "online", checker_report=FIXTURE)
    assert from_zip.findings == from_sarif.findings
    assert from_zip.checker is not None and from_sarif.checker is not None
    assert from_zip.checker.results == from_sarif.checker.results == 12


def test_a_file_that_is_neither_a_zip_nor_sarif_is_a_clean_error_naming_it(folder, tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("hello", encoding="utf-8")
    with pytest.raises(inv.InventoryError, match=re.escape(str(bad))):
        assess.assess(folder, "online", checker_report=bad)
    not_sarif = tmp_path / "other.json"
    not_sarif.write_text('{"hello": 1}', encoding="utf-8")
    with pytest.raises(inv.InventoryError, match=re.escape(str(not_sarif))):
        assess.assess(folder, "online", checker_report=not_sarif)


def test_a_zip_without_a_sarif_file_is_a_clean_error_naming_it(folder, tmp_path):
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("readme.txt", "no report here")
    with pytest.raises(inv.InventoryError, match=re.escape(str(archive))):
        assess.assess(folder, "online", checker_report=archive)


def test_a_missing_report_is_a_clean_error_naming_it(folder, tmp_path):
    gone = tmp_path / "gone.sarif"
    with pytest.raises(inv.InventoryError, match=re.escape(str(gone))):
        assess.assess(folder, "online", checker_report=gone)


def test_the_fixture_names_no_org_tenant_or_customer():
    text = FIXTURE.read_text("utf-8")
    hosts = set(re.findall(r"https?://([^/\"]+)", text))
    assert hosts <= {"learn.microsoft.com", "json.schemastore.org"}
    assert not re.search(r"\.(onmicrosoft|crm\d*\.dynamics)\.com|tenant", text, re.I)


def test_packaged_rules_name_the_checker_rule_they_correspond_to():
    by_id = {r.id: r for r in assess.load_rules().rules}
    assert by_id["web-resource-soap-2011"].checker_rule == "web-avoid-crm2011-service-soap"
    assert by_id["web-resource-xrm-page"].checker_rule == "web-use-client-context"
    assert by_id["web-resource-get-server-url"].checker_rule == "web-avoid-2011-api"
    assert by_id["web-resource-crmform"].checker_rule is None
    assert by_id["web-resource-odata-v2"].checker_rule == "web-avoid-crm2011-service-odata"
    assert by_id["web-resource-xrm-utility"].checker_rule is None
    assert by_id["web-resource-dom-access"].checker_rule == "web-avoid-dom-form"
    assert by_id["web-resource-parent-dom-access"].checker_rule == "web-avoid-dom-form"


def test_a_rule_file_checker_rule_must_be_text():
    data = json.loads(assess.RULES_PATH.read_text("utf-8"))
    data["rules"][0]["checker_rule"] = 7
    with pytest.raises(inv.InventoryError, match="checker_rule"):
        assess.parse_rules(data)


def test_the_rules_file_still_matches_its_schema():
    schema = json.loads((SCHEMAS / "compatibility-rules-v1.schema.json").read_text("utf-8"))
    jsonschema.validate(json.loads(assess.RULES_PATH.read_text("utf-8")), schema)


def test_the_cli_flag_merges_the_report_and_summarises_it(folder, tmp_path):
    out = tmp_path / "findings.json"
    args = ["--json", "inventory", "assess", str(folder), "--target", "online", "--out", str(out)]
    res = CliRunner().invoke(cli, [*args, "--checker-report", str(FIXTURE)])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)["data"]
    assert data["checker"]["results"] == 12 and data["checker"]["unmatched"] == 2
    assert data["checker"]["skipped_duplicates"] == 2
    assert "checker_report" in json.loads(out.read_text("utf-8"))
    bad = CliRunner().invoke(cli, [*args, "--checker-report", str(tmp_path / "gone.sarif")])
    assert bad.exit_code != 0


def test_packages_places_a_checker_finding_and_convert_writes_nothing_for_it(
    folder, tmp_path, merged
):
    root = tmp_path
    repo = root / "repo"
    repo.mkdir()
    source_map = root / "source-map.json"
    source_map.write_text(dump_source_map(map_source(folder, repo)), encoding="utf-8")
    findings = root / "findings.json"
    findings.write_text(assess.dump_findings(merged), encoding="utf-8")
    packages = root / "packages"
    built: Any = build_packages(
        folder, read_source_map(source_map), None, assess.read_findings(findings)
    )
    write_packages(built, packages)
    placed = [
        f
        for p in built.packages
        for f in p.findings
        if f.type == "compatibility" and f.rule == "web-avoid-2011-api"
    ]
    assert placed and placed[0].component == f"web_resource:{LEGACY}"
    assert placed[0].sources
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    out = root / "out"
    args = ["--json", "inventory", "convert", str(folder), "--findings", str(findings)]
    res = CliRunner().invoke(cli, [*args, "--packages", str(packages), "--out", str(out)])
    assert res.exit_code == 0, res.output
    ledger = json.loads((out / "ledger.json").read_text("utf-8"))
    checker_ids = {"web-avoid-2011-api", "web-use-strict-mode", "web-avoid-eval"}
    assert not [e for e in ledger["entries"] if e["rule"] in checker_ids]
    assert before == {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    shutil.rmtree(out)


def test_a_result_on_a_packaged_hit_past_the_location_cap_is_still_skipped(tmp_path):
    soap = 'var s = "/XRMServices/2011/Organization.svc/web";\n'
    many = "".join(soap for _ in range(assess.MAX_LOCATIONS + 5))
    folder = tmp_path / "inv"
    folder.mkdir()
    record = _record(LEGACY, many, folder)
    manifest = inv.Inventory(
        schema_version=inv.SCHEMA_VERSION,
        source=inv.Source("crm2011", ORG, None, "5.0.9690.4376"),
        extracted_at="2026-11-01T10:00:00Z",
        cli_version="1.119.0",
        scope=inv.Scope("org", ()),
        publication="published",
        records=(record,),
        coverage={"web_resource": inv.KindCoverage("complete", 1, 1, False, ())},
        gaps=(),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")
    last = assess.MAX_LOCATIONS + 5
    report = _one_result_report(
        tmp_path / "r.sarif", "web-avoid-crm2011-service-soap", LEGACY, last
    )
    result = assess.assess(folder, "online", checker_report=report)
    assert result.checker is not None and result.checker.skipped == 1
    assert not _rows(result, LEGACY)


def test_an_unreadable_zip_member_is_a_clean_error_naming_the_file(folder, tmp_path, monkeypatch):
    archive = tmp_path / "locked.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("r.sarif", "{}")

    def locked(self, name, pwd=None):
        raise RuntimeError("File 'r.sarif' is encrypted, password required for extraction")

    monkeypatch.setattr(zipfile.ZipFile, "read", locked)
    with pytest.raises(inv.InventoryError, match=re.escape(str(archive))):
        assess.assess(folder, "online", checker_report=archive)
