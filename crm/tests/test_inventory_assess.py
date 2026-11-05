"""`crm inventory assess`: compatibility rules over a migration inventory."""

# pyright: basic

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory as inv
from crm.core import inventory_assess as assess

SCHEMAS = Path(assess.__file__).parent.parent / "schemas"
ORG = "https://crm.example.test/Org"
XRM_PAGE_JS = (
    "function onLoad() {\n  var name = Xrm.Page.getAttribute('name');\n  return name;\n}\n"
)
CLEAN_JS = "function onLoad(ctx) {\n  return ctx.getFormContext();\n}\n"


def _record(kind: str, fields: dict, folder: Path, *, files=()) -> inv.Record:
    key = inv.component_key(kind, fields)
    artifacts = []
    for name, data, text in files:
        path = inv.artifact_path(kind, key, name)
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_bytes(data)
        hashes = inv.artifact_hashes(data, text=text)
        content = "text" if text else "binary"
        artifacts.append(inv.Artifact(path, content, hashes.sha256, hashes.normalized_sha256))
    return inv.Record(kind, key, None, None, "custom", False, "published", fields, tuple(artifacts))


def _script(name: str, content: str, folder: Path) -> inv.Record:
    fields = {"name": name, "web_resource_type": 3}
    return _record("web_resource", fields, folder, files=[("content.js", content.encode(), True)])


def _assembly(name: str, folder: Path, **fields) -> inv.Record:
    return _record("plugin_assembly", {"name": name, **fields}, folder)


def _write_inventory(folder: Path, *, version: str = "5.0.9690.4376") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    records = [
        _script("new_/scripts/legacy.js", XRM_PAGE_JS, folder),
        _script("new_/scripts/clean.js", CLEAN_JS, folder),
        _record(
            "web_resource",
            {"name": "new_/img/logo.png", "web_resource_type": 5},
            folder,
            files=[("content.png", b"\x89PNG", False)],
        ),
        _assembly("Contoso.Gac", folder, source_type=2, isolation_mode=2),
        _assembly("Contoso.Open", folder, source_type=0, isolation_mode=1),
        _assembly("Contoso.Sandboxed", folder, source_type=0, isolation_mode=2),
        _assembly("Contoso.Unrecorded", folder, source_type=0),
        _record("entity", {"logical_name": "new_project"}, folder),
        _record("view", {"entity": "new_project", "name": "Active Projects"}, folder),
    ]
    counts: dict[str, int] = {}
    for record in records:
        counts[record.kind] = counts.get(record.kind, 0) + 1
    coverage = {k: inv.KindCoverage("complete", 1, n, False, ()) for k, n in counts.items()}
    coverage["view"] = inv.KindCoverage("partial", 1, 1, True, ("page 2 failed",))
    manifest = inv.Inventory(
        schema_version=inv.SCHEMA_VERSION,
        source=inv.Source("crm2011", ORG, None, version),
        extracted_at="2026-11-01T10:00:00Z",
        cli_version="1.91.0",
        scope=inv.Scope("org", ()),
        publication="published",
        records=tuple(records),
        coverage=coverage,
        gaps=(
            inv.ExtractionGap(
                "plugin_assembly", "Contoso.Gac", None, "the assembly binary is stored in the GAC"
            ),
            inv.ExtractionGap(None, None, "external integrations", "outside the org"),
        ),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")
    return folder


@pytest.fixture
def inventory(tmp_path: Path) -> Path:
    return _write_inventory(tmp_path / "inv")


def _findings(result, key: str) -> list:
    return [f for f in result.findings if f.key == key]


def _classes(result, key: str) -> set[str]:
    return {f.cls for f in _findings(result, key)}


def test_xrm_page_is_deprecated_with_its_location(inventory):
    result = assess.assess(inventory, "online")
    (hit,) = [f for f in _findings(result, "new_/scripts/legacy.js") if f.rule]
    assert (hit.cls, hit.rule) == ("deprecated", "web-resource-xrm-page")
    record = next(r for r in inv.read_inventory(inventory).records if r.key == hit.key)
    (artifact,) = record.artifacts
    assert hit.evidence is not None
    assert hit.evidence.path == artifact.path and hit.evidence.sha256 == artifact.sha256
    assert hit.evidence.locations == ((2, 14),)
    assert hit.evidence.match_count == 1


def test_a_gac_assembly_is_a_blocker_online_and_its_gap_is_unknown(inventory):
    result = assess.assess(inventory, "online")
    gac = _findings(result, "Contoso.Gac")
    assert {(f.cls, f.rule) for f in gac} >= {("blocker", "assembly-storage-online")}
    (gap,) = [f for f in gac if f.cls == "unknown"]
    assert gap.reason == "extraction gap: the assembly binary is stored in the GAC"


def test_isolation_none_is_a_blocker_online_and_an_unrecorded_field_is_unknown(inventory):
    result = assess.assess(inventory, "online")
    assert ("blocker", "assembly-isolation-none") in {
        (f.cls, f.rule) for f in _findings(result, "Contoso.Open")
    }
    (unknown,) = _findings(result, "Contoso.Unrecorded")
    assert unknown.cls == "unknown" and unknown.rule == "assembly-isolation-none"
    assert unknown.reason == "field isolation_mode not recorded"
    assert _classes(result, "Contoso.Sandboxed") == {"clear"}


def test_targets_select_the_rules(inventory):
    result = assess.assess(inventory, "onprem-9.1")
    assert "blocker" not in _classes(result, "Contoso.Gac")
    assert "blocker" not in _classes(result, "Contoso.Open")


def test_a_kind_no_rule_covers_is_unknown_no_rule(inventory):
    result = assess.assess(inventory, "online")
    (entity,) = _findings(result, "new_project")
    assert (entity.cls, entity.reason, entity.rule) == ("unknown", "no rule", None)
    (logo,) = _findings(result, "new_/img/logo.png")
    assert (logo.cls, logo.reason) == ("unknown", "no rule")


def test_partial_coverage_gives_unknown_for_its_components(inventory):
    result = assess.assess(inventory, "online")
    reasons = {f.reason for f in _findings(result, "new_project|Active Projects")}
    assert "kind coverage partial" in reasons


def test_every_component_gets_a_finding(inventory):
    result = assess.assess(inventory, "online")
    keys = {(r.kind, r.key) for r in inv.read_inventory(inventory).records}
    assert keys == {(f.kind, f.key) for f in result.findings}
    assert _classes(result, "new_/scripts/clean.js") == {"clear"}
    assert [g["description"] for g in result.unattached_gaps] == ["external integrations"]


def test_a_source_outside_a_rule_range_skips_that_rule(tmp_path):
    folder = _write_inventory(tmp_path / "v9", version="9.0.2.3034")
    data = json.loads(assess.RULES_PATH.read_text(encoding="utf-8"))
    for rule in data["rules"]:
        if rule["id"] == "web-resource-xrm-page":
            rule["source"]["before"] = "9.0"
    rules = assess.parse_rules(data)
    result = assess.assess(folder, "online", rules=rules)
    assert _classes(result, "new_/scripts/legacy.js") == {"clear"}
    assert "deprecated" in _classes(
        assess.assess(tmp_path / "v9", "online"), "new_/scripts/legacy.js"
    )


def test_an_artifact_changed_since_extraction_is_unknown(inventory):
    record = next(
        r for r in inv.read_inventory(inventory).records if r.key == "new_/scripts/clean.js"
    )
    (inventory / record.artifacts[0].path).write_text("Xrm.Page;\n", encoding="utf-8")
    result = assess.assess(inventory, "online")
    reasons = {f.reason for f in _findings(result, "new_/scripts/clean.js")}
    assert reasons == {"artifact changed since extraction: " + record.artifacts[0].path}


def test_every_shipped_rule_has_a_range_and_a_learn_source():
    rules = assess.load_rules()
    assert rules.rules
    for rule in rules.rules:
        assert rule.source_from, rule.id
        assert rule.targets, rule.id
        assert any(s.startswith("https://learn.microsoft.com/") for s in rule.sources), rule.id
    assert len({r.id for r in rules.rules}) == len(rules.rules)


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (lambda r: r.pop("source"), "source"),
        (lambda r: r.update(sources=["https://example.test/blog"]), "Microsoft Learn"),
        (lambda r: r.update(targets=[]), "targets"),
        (lambda r: r.update({"class": "fatal"}), "class"),
    ],
)
def test_a_rule_without_range_or_provenance_is_refused(change, match):
    data = json.loads(assess.RULES_PATH.read_text(encoding="utf-8"))
    change(data["rules"][0])
    with pytest.raises(inv.InventoryError, match=match):
        assess.parse_rules(data)


def test_rules_and_findings_validate_against_their_schemas(inventory):
    rules_schema = json.loads((SCHEMAS / "compatibility-rules-v1.schema.json").read_text())
    jsonschema.validate(json.loads(assess.RULES_PATH.read_text(encoding="utf-8")), rules_schema)
    findings_schema = json.loads((SCHEMAS / "compatibility-findings-v1.schema.json").read_text())
    text = assess.dump_findings(assess.assess(inventory, "online"))
    jsonschema.validate(json.loads(text), findings_schema)


def test_two_runs_differ_only_in_the_header_time(inventory):
    first = json.loads(assess.dump_findings(assess.assess(inventory, "online")))
    second = json.loads(assess.dump_findings(assess.assess(inventory, "online")))
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second
    stamp = "2026-11-05T00:00:00Z"
    one = assess.dump_findings(assess.assess(inventory, "online"), generated_at=stamp)
    two = assess.dump_findings(assess.assess(inventory, "online"), generated_at=stamp)
    assert one == two


def test_findings_name_their_inventory_and_rules(inventory):
    data = json.loads(assess.dump_findings(assess.assess(inventory, "online")))
    assert data["schema_version"] == 1 and data["target"] == "online"
    assert data["inventory"]["extracted_at"] == "2026-11-01T10:00:00Z"
    assert data["inventory"]["platform"] == "crm2011"
    assert len(data["inventory"]["manifest_sha256"]) == 64
    assert data["rules"]["version"] == assess.load_rules().version
    assert "web-resource-xrm-page" in data["rules_hit"]
    keys = [(f["kind"], f["key"]) for f in data["findings"]]
    assert keys == sorted(keys)


def test_cli_writes_findings_and_counts_with_blockers_exiting_0(inventory, tmp_path):
    out = tmp_path / "findings.json"
    res = CliRunner().invoke(
        cli,
        ["--json", "inventory", "assess", str(inventory), "--target", "online", "--out", str(out)],
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)["data"]
    assert data["out"] == str(out) and out.exists()
    assert data["counts"]["by_class"]["blocker"] >= 2
    assert data["counts"]["by_kind"]["entity"] == {"unknown": 1}


def test_cli_refuses_an_unknown_schema_version(inventory, tmp_path):
    manifest = json.loads((inventory / inv.MANIFEST_NAME).read_text())
    manifest["schema_version"] = 2
    (inventory / inv.MANIFEST_NAME).write_text(json.dumps(manifest))
    out = tmp_path / "findings.json"
    res = CliRunner().invoke(
        cli,
        ["--json", "inventory", "assess", str(inventory), "--target", "online", "--out", str(out)],
    )
    assert res.exit_code == 1 and "schema_version" in res.output
    assert not out.exists()


def test_cli_refuses_an_out_inside_the_inventory(inventory):
    out = inventory / "findings.json"
    res = CliRunner().invoke(
        cli,
        ["--json", "inventory", "assess", str(inventory), "--target", "online", "--out", str(out)],
    )
    assert res.exit_code == 1 and not out.exists()


def test_an_unreadable_source_version_makes_ruled_components_unknown(tmp_path):
    folder = _write_inventory(tmp_path / "unk", version="unknown")
    result = assess.assess(folder, "online")
    (legacy,) = _findings(result, "new_/scripts/legacy.js")
    assert (legacy.cls, legacy.rule) == ("unknown", None)
    assert legacy.reason == "source version 'unknown' is not a dotted version"
    assert _classes(result, "new_project") == {"unknown"}


def test_a_version_with_a_suffix_is_not_dotted():
    with pytest.raises(inv.InventoryError, match="dotted"):
        assess._version("9.0x", "source.from")


def test_a_field_rule_matches_on_type_as_well_as_value(tmp_path):
    folder = tmp_path / "f"
    folder.mkdir()
    record = _assembly("Contoso.Odd", folder, source_type=0, isolation_mode=True)
    rules = [r for r in assess.load_rules().rules if r.id == "assembly-isolation-none"]
    assert [f.cls for f in assess._assess_record(record, rules, folder)] == ["clear"]


def test_a_list_valued_field_hit_is_assessed(tmp_path):
    folder = _write_inventory(tmp_path / "l")
    manifest = json.loads((folder / inv.MANIFEST_NAME).read_text())
    for record in manifest["records"]:
        if record["key"] == "Contoso.Open":
            record["fields"]["isolation_mode"] = [1]
    (folder / inv.MANIFEST_NAME).write_text(json.dumps(manifest))
    data = json.loads(assess.RULES_PATH.read_text(encoding="utf-8"))
    for rule in data["rules"]:
        if rule["id"] == "assembly-isolation-none":
            rule["detection"]["values"] = [[1]]
    result = assess.assess(folder, "online", rules=assess.parse_rules(data))
    (hit,) = [f for f in _findings(result, "Contoso.Open") if f.cls == "blocker"]
    assert hit.evidence == assess.FieldEvidence("isolation_mode", [1])
