"""Browser-only code in web resources (#1276): Silverlight, ActiveX, attachEvent, showModalDialog.

Each script rule reads text, so a match inside a comment still counts, the way every other
script rule behaves; the Silverlight rule reads the web resource type, since a XAP holds no text.
"""

# pyright: basic

from __future__ import annotations

from pathlib import Path

import pytest

from crm.core import inventory as inv
from crm.core import inventory_assess as assess
from crm.core import inventory_readiness as readiness
from crm.tests.test_inventory_convert import ORG, _record

XAP = "new_/silverlight/app.xap"

# rule id -> (a script that matches, a script that does not)
SCRIPT_RULES = {
    "web-resource-activex": (
        "var x = new ActiveXObject('Msxml2.XMLHTTP');\n",
        "var ok = 'ActiveXObject' in window;\n",
    ),
    "web-resource-attach-event": (
        "el.attachEvent('onclick', f);\n",
        "el.addEventListener('click', f);\nfunction attachEventually() {}\n",
    ),
    "web-resource-show-modal-dialog": (
        "var r = window.showModalDialog(url, null, 'dialogWidth:300px');\n",
        "Xrm.Navigation.openDialog('x');\n",
    ),
}


def _folder(tmp_path: Path) -> Path:
    folder = tmp_path / "inv"
    folder.mkdir()
    records = [_xap_record(folder)]
    for rule, (hit, miss) in SCRIPT_RULES.items():
        records.append(_record(f"new_/{rule}-hit.js", hit, folder))
        records.append(_record(f"new_/{rule}-miss.js", miss, folder))
        records.append(
            _record(f"new_/{rule}-page.htm", f"<script>{hit}</script>", folder, code=1, ext="htm")
        )
        records.append(_record(f"new_/{rule}-comment.js", f"// {hit}", folder))
    manifest = inv.Inventory(
        schema_version=inv.SCHEMA_VERSION,
        source=inv.Source("crm2011", ORG, None, "5.0.9690.4376"),
        extracted_at="2026-11-13T10:00:00Z",
        cli_version="1.120.18",
        scope=inv.Scope("org", ()),
        publication="published",
        records=tuple(records),
        coverage={"web_resource": inv.KindCoverage("complete", 1, len(records), False, ())},
        gaps=(),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")
    return folder


def _xap_record(folder: Path) -> inv.Record:
    fields = {"name": XAP, "web_resource_type": 8}
    key = inv.component_key("web_resource", fields)
    path = inv.artifact_path("web_resource", key, "content.xap")
    (folder / path).parent.mkdir(parents=True, exist_ok=True)
    data = b"PK\x03\x04 silverlight"
    (folder / path).write_bytes(data)
    h = inv.artifact_hashes(data, text=False)
    artifact = inv.Artifact(path, "binary", h.sha256, h.normalized_sha256)
    return inv.Record(
        "web_resource", key, None, None, "custom", False, "published", fields, (artifact,)
    )


def _rules(result, name: str) -> dict[str, str]:
    key = inv.component_key("web_resource", {"name": name})
    return {f.rule or "": f.cls for f in result.findings if f.key == key}


@pytest.fixture
def folder(tmp_path) -> Path:
    return _folder(tmp_path)


@pytest.mark.parametrize("target", ["online", "onprem-9.1"])
def test_a_silverlight_web_resource_is_a_blocker(folder, target):
    assert _rules(assess.assess(folder, target), XAP) == {"web-resource-silverlight": "blocker"}


@pytest.mark.parametrize("target", ["online", "onprem-9.1"])
@pytest.mark.parametrize("rule", sorted(SCRIPT_RULES))
def test_a_script_rule_matches_script_and_html_but_not_the_lookalike(folder, rule, target):
    result = assess.assess(folder, target)
    assert _rules(result, f"new_/{rule}-hit.js") == {rule: "blocker"}
    assert _rules(result, f"new_/{rule}-page.htm") == {rule: "blocker"}
    assert _rules(result, f"new_/{rule}-miss.js") == {"": "clear"}


@pytest.mark.parametrize("rule", sorted(SCRIPT_RULES))
def test_commented_out_code_is_a_hit_like_every_other_script_rule(folder, rule):
    result = assess.assess(folder, "online")
    assert _rules(result, f"new_/{rule}-comment.js") == {rule: "blocker"}


def test_a_script_is_not_a_silverlight_web_resource(folder):
    assert "web-resource-silverlight" not in _rules(
        assess.assess(folder, "online"), "new_/web-resource-activex-hit.js"
    )


def test_readiness_lists_each_rule_as_an_online_gate_and_fails_it_on_a_hit(folder, tmp_path):
    findings = tmp_path / "findings.json"
    findings.write_text(assess.dump_findings(assess.assess(folder, "online")), encoding="utf-8")
    rows = {r.gate: r for r in readiness.readiness(findings).rows}
    # A script rule hits the script, the HTML page and the commented-out script; the other, the XAP.
    expected = {"web-resource-silverlight": 1} | dict.fromkeys(SCRIPT_RULES, 3)
    for rule, components in expected.items():
        assert (rows[rule].status, rows[rule].components) == ("fail", components)
