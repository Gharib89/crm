"""`crm inventory assess`: the xaml_pattern detection and the classic workflow rules."""

# pyright: basic

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from crm.core import inventory as inv
from crm.core import inventory_assess as assess
from crm.core import workflow
from crm.core.inventory_assess import InventoryError
from crm.tests.test_inventory_assess import SCHEMAS, _process, _record

WAIT = '<Activity><mxswa:Postpone DisplayName="Wait" /></Activity>'
WAIT_DEFAULT_NS = "<Activity><Postpone /></Activity>"
CUSTOM = (
    "<Activity><mxswa:ActivityReference "
    'AssemblyQualifiedName="Contoso.Flow.DoThing, Contoso.Flow, Version=1.0.0.0" /></Activity>'
)
OOB = (
    "<Activity><mxswa:ActivityReference "
    'AssemblyQualifiedName="Microsoft.Crm.Workflow.Activities.CreateEntity, '
    'Microsoft.Crm.Workflow, Version=9.0.0.0" /></Activity>'
)
NONE = "<Activity><Sequence /></Activity>"
BOTH = WAIT + CUSTOM
# Samples both the live scan and the offline rules must judge alike.
SAMPLES = {"wait": WAIT, "custom": CUSTOM, "oob": OOB, "none": NONE, "both": BOTH}


def _inventory(tmp_path: Path, records: list[inv.Record]) -> Path:
    folder = tmp_path / "inv"
    folder.mkdir(exist_ok=True)
    counts: dict[str, int] = {}
    for r in records:
        counts[r.kind] = counts.get(r.kind, 0) + 1
    manifest = inv.Inventory(
        schema_version=inv.SCHEMA_VERSION,
        source=inv.Source("crm2011", "https://crm.example.test/Org", None, "5.0.9690.4376"),
        extracted_at="2026-11-01T10:00:00Z",
        cli_version="1.91.0",
        scope=inv.Scope("org", ()),
        publication="published",
        records=tuple(records),
        coverage={k: inv.KindCoverage("complete", 1, n, False, ()) for k, n in counts.items()},
        gaps=(),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")
    return folder


def _assess(tmp_path: Path, name: str, category: int, xaml: str | None, target="online"):
    folder = tmp_path / "inv"
    folder.mkdir(exist_ok=True)
    record = _process(name, category, folder, xaml)
    folder = _inventory(tmp_path, [record])
    result = assess.assess(folder, target)
    return record, [f for f in result.findings if f.key == record.key]


def _rule_by_id(rule_id: str):
    (rule,) = [r for r in assess.load_rules().rules if r.id == rule_id]
    return rule


@pytest.mark.parametrize("target", ["online", "onprem-9.1"])
@pytest.mark.parametrize("xaml", [WAIT, WAIT_DEFAULT_NS])
def test_a_wait_condition_is_optional(tmp_path, xaml, target):
    record, found = _assess(tmp_path, "Wait flow", 0, xaml, target)
    (hit,) = found
    assert (hit.cls, hit.rule, hit.reason) == ("optional", "workflow-wait-condition", None)
    (artifact,) = record.artifacts
    assert hit.evidence.path == artifact.path and hit.evidence.sha256 == artifact.sha256
    assert hit.evidence.locations == ((1, 11),) and hit.evidence.match_count == 1


@pytest.mark.parametrize("target", ["online", "onprem-9.1"])
def test_a_custom_activity_is_optional_and_an_out_of_box_one_is_clear(tmp_path, target):
    _, found = _assess(tmp_path, "Custom flow", 0, CUSTOM, target)
    (hit,) = found
    assert (hit.cls, hit.rule) == ("optional", "workflow-custom-activity")
    assert hit.evidence.match_count == 1
    for sample in (OOB, NONE):
        _, found = _assess(tmp_path, "Plain flow", 0, sample, target)
        assert [(f.cls, f.rule, f.reason) for f in found] == [("clear", None, None)]


def test_a_workflow_with_both_features_gets_both_findings(tmp_path):
    _, found = _assess(tmp_path, "Both", 0, BOTH)
    assert {(f.cls, f.rule) for f in found} == {
        ("optional", "workflow-wait-condition"),
        ("optional", "workflow-custom-activity"),
    }


def test_the_match_locations_stop_at_ten_and_the_count_does_not(tmp_path):
    _, found = _assess(tmp_path, "Many waits", 0, "\n".join([WAIT] * 12))
    (hit,) = found
    assert len(hit.evidence.locations) == 10 and hit.evidence.match_count == 12
    assert hit.evidence.locations[1] == (2, 11)


def test_no_text_artifact_is_unknown(tmp_path):
    _, found = _assess(tmp_path, "No xaml", 0, None)
    assert [(f.cls, f.rule, f.reason) for f in found] == [("unknown", None, "no text artifact")]


def test_a_missing_file_is_unknown(tmp_path):
    record, _ = _assess(tmp_path, "Gone", 0, WAIT)
    (tmp_path / "inv" / record.artifacts[0].path).unlink()
    (found,) = [
        f for f in assess.assess(tmp_path / "inv", "online").findings if f.key == record.key
    ]
    assert (found.cls, found.reason) == (
        "unknown",
        f"artifact unreadable: {record.artifacts[0].path}",
    )


def test_a_changed_file_is_unknown(tmp_path):
    record, _ = _assess(tmp_path, "Edited", 0, WAIT)
    path = tmp_path / "inv" / record.artifacts[0].path
    path.write_bytes(path.read_bytes() + b"<!-- edited -->")
    assert hashlib.sha256(path.read_bytes()).hexdigest() != record.artifacts[0].sha256
    (found,) = [
        f for f in assess.assess(tmp_path / "inv", "online").findings if f.key == record.key
    ]
    assert (found.cls, found.reason) == (
        "unknown",
        f"artifact changed since extraction: {record.artifacts[0].path}",
    )


@pytest.mark.parametrize("category", [1, 3, 4, 5])
def test_other_categories_are_not_judged_by_the_xaml_rules(tmp_path, category):
    folder = tmp_path / "inv"
    folder.mkdir()
    fields = {"category": category, "primary_entity": "new_project", "name": "Other"}
    other = _record("process", fields, folder, files=[("workflow.xaml", BOTH.encode(), True)])
    result = assess.assess(_inventory(tmp_path, [other]), "online")
    rules = {f.rule for f in result.findings if f.key == other.key}
    assert not rules & {"workflow-wait-condition", "workflow-custom-activity"}


def _rules_with(**detection) -> dict:
    data = json.loads(assess.RULES_PATH.read_text(encoding="utf-8"))
    rule = dict(_rules_json(data, "workflow-wait-condition"))
    rule["detection"] = detection
    data["rules"] = [rule]
    return data


def _rules_json(data: dict, rule_id: str) -> dict:
    return next(r for r in data["rules"] if r["id"] == rule_id)


def test_xaml_pattern_on_another_kind_is_refused(tmp_path):
    data = _rules_with(type="xaml_pattern", pattern="x", categories=[0])
    data["rules"][0]["kind"] = "web_resource"
    with pytest.raises(InventoryError, match="xaml_pattern applies to process only"):
        assess.parse_rules(data)


@pytest.mark.parametrize(
    ("detection", "message"),
    [
        ({"type": "xaml_pattern", "pattern": "(", "categories": [0]}, "does not compile"),
        ({"type": "xaml_pattern", "pattern": "x", "categories": []}, "non-empty list"),
        ({"type": "xaml_pattern", "pattern": "x", "categories": ["0"]}, "must hold integers"),
        ({"type": "xaml_pattern", "categories": [0]}, "pattern must be a non-empty string"),
    ],
)
def test_a_malformed_xaml_pattern_is_refused(tmp_path, detection, message):
    with pytest.raises(InventoryError, match=message):
        assess.parse_rules(_rules_with(**detection))


def test_the_published_schema_describes_xaml_pattern():
    schema = json.loads(
        (SCHEMAS / "compatibility-rules-v1.schema.json").read_text(encoding="utf-8")
    )
    import jsonschema

    data = json.loads(assess.RULES_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(data, schema)
    bad = _rules_json(data, "workflow-wait-condition")
    bad["detection"] = {"type": "xaml_pattern", "pattern": "x"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(data, schema)


@pytest.mark.parametrize(
    ("rule_id", "shared"),
    [
        ("workflow-wait-condition", workflow.WAIT_ACTIVITY_PATTERN),
        ("workflow-custom-activity", workflow.CUSTOM_ACTIVITY_PATTERN),
    ],
)
def test_the_rule_pattern_is_the_one_migration_assess_uses(rule_id, shared):
    assert _rule_by_id(rule_id).detection.pattern.pattern == shared


@pytest.mark.parametrize("sample", sorted(SAMPLES))
def test_rules_and_migration_assess_give_the_same_verdicts(tmp_path, sample):
    xaml = SAMPLES[sample]
    live = workflow.assess_workflow_migration({"xaml": xaml})["blockers"]
    _, found = _assess(tmp_path, "Parity", 0, xaml)
    offline = {f.rule for f in found if f.rule}
    assert (workflow.MIGRATION_BLOCKER_WAIT in live) == ("workflow-wait-condition" in offline)
    assert (workflow.MIGRATION_BLOCKER_CUSTOM_ACTIVITY in live) == (
        "workflow-custom-activity" in offline
    )


def test_the_custom_activity_pattern_ignores_spacing_around_the_out_of_box_assembly():
    spaced = 'AssemblyQualifiedName="X.Y,   Microsoft.Crm.Workflow , Version=9"'
    assert workflow.assess_workflow_migration({"xaml": spaced})["blockers"] == []


@pytest.mark.parametrize("rule_id", ["workflow-wait-condition", "workflow-custom-activity"])
def test_the_rules_are_optional_modernization_with_their_fix_and_source(rule_id):
    rule = _rule_by_id(rule_id)
    assert (rule.cls, rule.kind, rule.targets) == ("optional", "process", ("onprem-9.1", "online"))
    assert "no cloud flow equivalent" in rule.fix and "Dataverse online still runs" in rule.fix
    assert "Feature capability comparison" in rule.fix
    assert any(s.endswith("/power-automate/replace-workflows-with-flows") for s in rule.sources)
    assert rule.detection.categories == (0,)
