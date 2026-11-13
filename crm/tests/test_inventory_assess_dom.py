"""Direct DOM access in web resources (#1290): one rule per web resource type.

A script reads the wide pattern; an HTML page is its own page, so only its reach into the
parent form's DOM counts. Both rules name the Solution Checker rule `web-avoid-dom-form`, so a
checker result on the lines they flagged is skipped as already covered.
"""

# pyright: basic

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crm.core import inventory as inv
from crm.core import inventory_assess as assess
from crm.tests.test_inventory_convert import ORG, _record

SCRIPT = "new_/scripts/form.js"
PAGE = "new_/html/page.htm"
OWN_PAGE = "new_/html/own.htm"
SCRIPT_TEXT = "var a = 1;\nvar e = document.getElementById('name_d');\n"
PAGE_TEXT = "<script>\nvar t = parent.document.getElementById('tab');\n</script>\n"
OWN_PAGE_TEXT = "<script>\nvar t = document.getElementById('txtCity');\n</script>\n"


@pytest.fixture
def folder(tmp_path) -> Path:
    folder = tmp_path / "inv"
    folder.mkdir()
    records = (
        _record(SCRIPT, SCRIPT_TEXT, folder),
        _record(PAGE, PAGE_TEXT, folder, code=1, ext="htm"),
        _record(OWN_PAGE, OWN_PAGE_TEXT, folder, code=1, ext="htm"),
    )
    manifest = inv.Inventory(
        schema_version=inv.SCHEMA_VERSION,
        source=inv.Source("crm2011", ORG, None, "5.0.9690.4376"),
        extracted_at="2026-11-01T10:00:00Z",
        cli_version="1.120.9",
        scope=inv.Scope("org", ()),
        publication="published",
        records=records,
        coverage={"web_resource": inv.KindCoverage("complete", 1, len(records), False, ())},
        gaps=(),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")
    return folder


def _rules(result, name: str) -> dict[str, str]:
    key = inv.component_key("web_resource", {"name": name})
    return {f.rule or "": f.cls for f in result.findings if f.key == key}


@pytest.mark.parametrize("target", ["online", "onprem-9.1"])
def test_a_script_and_a_page_reaching_the_form_dom_are_deprecated(folder, target):
    result = assess.assess(folder, target)
    assert _rules(result, SCRIPT) == {"web-resource-dom-access": "deprecated"}
    assert _rules(result, PAGE) == {"web-resource-parent-dom-access": "deprecated"}


def test_an_html_page_reading_its_own_dom_is_clear(folder):
    assert _rules(assess.assess(folder, "online"), OWN_PAGE) == {"": "clear"}


def test_both_rules_name_the_checker_rule_and_a_learn_source():
    by_id = {r.id: r for r in assess.load_rules().rules}
    for rid in ("web-resource-dom-access", "web-resource-parent-dom-access"):
        rule = by_id[rid]
        assert rule.checker_rule == "web-avoid-dom-form"
        assert (
            "https://learn.microsoft.com/power-apps/maker/data-platform/powerapps-checker/rules/web/avoid-dom-form"
            in rule.sources
        )


def _report(path: Path, uri: str, line: int) -> Path:
    path.write_text(
        json.dumps(
            {
                "version": "2.1.0",
                "runs": [
                    {
                        "tool": {"driver": {"rules": []}},
                        "results": [
                            {
                                "ruleId": "web-avoid-dom-form",
                                "properties": {"severity": "Medium"},
                                "message": {"text": "m"},
                                "locations": [
                                    {
                                        "physicalLocation": {
                                            "artifactLocation": {"uri": f"/{uri}"},
                                            "region": {"startLine": line, "startColumn": 9},
                                        }
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize("name", [SCRIPT, PAGE])
def test_a_checker_result_on_the_flagged_line_is_skipped_as_a_duplicate(folder, tmp_path, name):
    report = _report(tmp_path / "r.sarif", name, 2)
    result = assess.assess(folder, "online", checker_report=report)
    assert result.checker is not None and result.checker.skipped == 1
    assert "web-avoid-dom-form" not in _rules(result, name)
