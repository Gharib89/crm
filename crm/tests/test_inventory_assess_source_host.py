"""`crm inventory assess --source-host`: text that names the source org's host (#1214)."""

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
from crm.core.inventory_assess import InventoryError
from crm.tests.test_inventory_assess import SCHEMAS, _record
from crm.tests.test_inventory_assess_xaml import _inventory

RULES = {
    "web_resource": "source-host-web-resource",
    "sitemap": "source-host-sitemap",
    "ribbon": "source-host-ribbon",
    "ribbon_diff": "source-host-ribbon-diff",
    "process": "source-host-process",
}
JS = "var url = 'http://CRM01/Org/api';\nvar ok = 1;\n// see https://crm01.contoso.com/x\n"
OTHER = "var url = 'http://crm010/Org/api'; // also crm01-test and mycrm01 and a.crm01.com\n"


def _file(kind: str, fields: dict, folder: Path, name: str, text: str) -> inv.Record:
    return _record(kind, fields, folder, files=[(name, text.encode(), True)])


def _records(folder: Path) -> list[inv.Record]:
    return [
        _file("web_resource", {"name": "new_hit.js", "web_resource_type": 3}, folder, "c.js", JS),
        _file("web_resource", {"name": "new_hit.htm", "web_resource_type": 1}, folder, "c.htm", JS),
        _file("web_resource", {"name": "new_hit.png", "web_resource_type": 5}, folder, "c.png", JS),
        _file(
            "web_resource", {"name": "new_near.js", "web_resource_type": 3}, folder, "n.js", OTHER
        ),
        _file(
            "sitemap", {"unique_name": "site"}, folder, "sitemap.xml", '<Url Url="http://crm01/x"/>'
        ),
        _file(
            "ribbon",
            {"entity": "account"},
            folder,
            "ribbon.xml",
            "<A Library='http://crm01/l.js'/>",
        ),
        _file("ribbon_diff", {"entity": "lead"}, folder, "ribbon-diff.xml", "<A Url='//crm01/l'/>"),
        _file(
            "process",
            {"category": 0, "primary_entity": "lead", "name": "Flow"},
            folder,
            "workflow.xaml",
            "<A Url='http://crm01/'/>",
        ),
        _file(
            "process",
            {"category": 0, "primary_entity": "lead", "name": "Clean"},
            folder,
            "workflow.xaml",
            "<A/>",
        ),
    ]


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "inv").mkdir()
    return _inventory(tmp_path, _records(tmp_path / "inv"))


def _hits(result):
    rule_ids = set(RULES.values())
    return {(f.kind, f.key): f for f in result.findings if f.rule in rule_ids}


def test_each_kind_with_text_naming_the_host_gets_a_blocker_with_matched_lines(folder):
    result = assess.assess(folder, "online", source_hosts=("crm01",))
    hits = _hits(result)
    assert {(k, key) for k, key in hits} == {
        ("web_resource", "new_hit.js"),
        ("web_resource", "new_hit.htm"),
        ("sitemap", "site"),
        ("ribbon", "account"),
        ("ribbon_diff", "lead"),
        (
            "process",
            inv.component_key("process", {"category": 0, "primary_entity": "lead", "name": "Flow"}),
        ),
    }
    js = hits[("web_resource", "new_hit.js")]
    assert (js.cls, js.rule) == ("blocker", RULES["web_resource"])
    assert isinstance(js.evidence, assess.ContentEvidence)
    assert js.evidence.locations == ((1, 19), (3, 16)) and js.evidence.match_count == 2


@pytest.mark.parametrize("target", ["online", "onprem-9.1"])
def test_the_rules_apply_to_both_targets(folder, target):
    assert _hits(assess.assess(folder, target, source_hosts=("crm01",)))


def test_the_match_is_case_insensitive_and_whole_host(folder):
    near = ("web_resource", "new_near.js")
    result = assess.assess(folder, "online", source_hosts=("CRM01",))
    assert near not in _hits(result)
    assert ("web_resource", "new_hit.js") in _hits(result)
    assert near in _hits(assess.assess(folder, "online", source_hosts=("crm010",)))


def test_a_fully_qualified_host_matches_only_itself(folder):
    js = ("web_resource", "new_hit.js")
    hit = _hits(assess.assess(folder, "online", source_hosts=("crm01.contoso.com",)))[js]
    assert hit.evidence.locations == ((3, 16),)


def test_several_hosts_match_either(folder):
    result = assess.assess(folder, "online", source_hosts=("crm010", "crm01"))
    assert ("web_resource", "new_near.js") in _hits(result)
    assert ("sitemap", "site") in _hits(result)


def test_without_a_host_no_such_finding_exists_and_the_file_is_unchanged(folder):
    plain = assess.assess(folder, "online")
    assert not _hits(plain)
    stamp = "2026-11-05T00:00:00Z"
    text = assess.dump_findings(plain, generated_at=stamp)
    assert "source_hosts" not in json.loads(text)
    assert text == assess.dump_findings(
        assess.assess(folder, "online", source_hosts=()), generated_at=stamp
    )


def test_a_host_adds_findings_and_leaves_every_other_rule_finding_as_it_was(folder):
    ours = set(RULES.values())
    plain = assess.assess(folder, "online")
    withhost = assess.assess(folder, "online", source_hosts=("crm01",))
    assert [f for f in plain.findings if f.rule] == [
        f for f in withhost.findings if f.rule and f.rule not in ours
    ]


def test_the_findings_file_records_the_hosts_sorted_and_lowercased(folder):
    result = assess.assess(folder, "online", source_hosts=("CRM01", "crm010", "crm01"))
    data = json.loads(assess.dump_findings(result))
    assert data["source_hosts"] == ["crm01", "crm010"]
    jsonschema.validate(
        data, json.loads((SCHEMAS / "compatibility-findings-v1.schema.json").read_text())
    )


def test_the_host_is_never_written_to_the_rules_file():
    text = (Path(assess.__file__).parent.parent / "rules" / "compatibility-rules.json").read_text()
    assert "crm01" not in text
    rules = {r.id: r for r in assess.load_rules().rules}
    for rule_id in RULES.values():
        rule = rules[rule_id]
        assert rule.cls == "blocker" and rule.transform is None
        assert rule.sources and all(s.startswith(assess.LEARN) for s in rule.sources)
    schema = json.loads((SCHEMAS / "compatibility-rules-v1.schema.json").read_text())
    data = json.loads(
        (Path(assess.__file__).parent.parent / "rules" / "compatibility-rules.json").read_text()
    )
    jsonschema.validate(data, schema)


def test_the_rules_file_version_is_raised():
    assert assess.load_rules().version >= 10


@pytest.mark.parametrize("bad", ["", " ", "http://crm01", "crm01/Org", "crm 01", "crm01:5555"])
def test_a_host_must_be_a_bare_name(folder, bad):
    with pytest.raises(InventoryError, match="--source-host"):
        assess.assess(folder, "online", source_hosts=(bad,))


def test_the_findings_file_reads_back_with_the_new_rules(folder, tmp_path):
    out = tmp_path / "f.json"
    result = assess.assess(folder, "online", source_hosts=("crm01",))
    out.write_text(assess.dump_findings(result), encoding="utf-8")
    read = assess.read_findings(out)
    assert {f.rule for f in read.findings} >= set(RULES.values())


def test_cli_takes_the_option_more_than_once(folder, tmp_path):
    out = tmp_path / "f.json"
    res = CliRunner().invoke(
        cli,
        ["--json", "inventory", "assess", str(folder), "--target", "online", "--out", str(out),
         "--source-host", "CRM01", "--source-host", "crm010"],
    )  # fmt: skip
    assert res.exit_code == 0, res.output
    assert json.loads(out.read_text())["source_hosts"] == ["crm01", "crm010"]


def test_cli_refuses_a_host_with_a_scheme(folder, tmp_path):
    res = CliRunner().invoke(
        cli,
        ["--json", "inventory", "assess", str(folder), "--target", "online",
         "--out", str(tmp_path / "f.json"), "--source-host", "http://crm01"],
    )  # fmt: skip
    assert res.exit_code == 1 and "--source-host" in res.output
