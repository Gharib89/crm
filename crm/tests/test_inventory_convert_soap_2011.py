"""`crm inventory convert`: the 2011 SOAP endpoint is recorded for review, never rewritten (#1203).

One inventory holds a web resource per case. It runs through `assess`, `packages` and the
`convert` CLI once, then each case asserts the ledger and the files under `--out`.
"""

# pyright: basic

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory as inv
from crm.core import inventory_assess as assess
from crm.core.inventory_packages import build_packages, write_packages
from crm.core.inventory_source_map import dump_source_map, map_source, read_source_map
from crm.core.inventory_transforms import _SOAP_2011, TRANSFORMS
from crm.tests.test_inventory_convert import ORG, _record

RULE = "web-resource-soap-2011"
ORG_CALL = (
    "var x = new XMLHttpRequest();\n"
    'x.open("POST", url + "/XRMServices/2011/Organization.svc/web", false);\n'
)
CASES: dict[str, str] = {
    "organization": ORG_CALL,
    "discovery": 'x.open("POST", "/XRMServices/2011/Discovery.svc");\n',
    "upper-case": 'x.open("POST", "/xrmservices/2011/ORGANIZATION.SVC/web");\n',
    "two-sites": (
        'x.open("POST", "/XRMServices/2011/Organization.svc/web");\n'
        "var y = 1;\n"
        'x.open("POST", "/XRMServices/2011/Discovery.svc");\n'
    ),
    "same-line": 'a("/XRMServices/2011/Organization.svc"); b("/XRMServices/2011/Discovery.svc");\n',
    "comment-only": "// was /XRMServices/2011/Organization.svc/web\nvar z = 1;\n",
    "mixed": (
        "var c = Xrm.Page.context.getClientUrl();\n"
        'x.open("POST", "/XRMServices/2011/Organization.svc/web");\n'
    ),
    "soap-2007": 'x.open("POST", "/MSCrmServices/2007/CrmService.asmx");\n',
}
NAMES = {c: f"new_/scripts/soap-{c}.js" for c in CASES}
HTML_NAME = "new_/html/soap.htm"
HTML_SOURCE = '<script>x.open("POST", "/XRMServices/2011/Organization.svc/web");</script>'


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("soap-2011")
    folder = root / "inv"
    folder.mkdir()
    records = [_record(NAMES[c], src, folder) for c, src in CASES.items()]
    records.append(_record(HTML_NAME, HTML_SOURCE, folder, code=1, ext="htm"))
    manifest = inv.Inventory(
        schema_version=inv.SCHEMA_VERSION,
        source=inv.Source("crm2011", ORG, None, "5.0.9690.4376"),
        extracted_at="2026-11-01T10:00:00Z",
        cli_version="1.98.0",
        scope=inv.Scope("org", ()),
        publication="published",
        records=tuple(records),
        coverage={"web_resource": inv.KindCoverage("complete", 1, len(records), False, ())},
        gaps=(),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")
    repo = root / "repo"
    repo.mkdir()
    source_map = root / "source-map.json"
    source_map.write_text(dump_source_map(map_source(folder, repo)), encoding="utf-8")
    findings_path = root / "findings.json"
    findings_path.write_text(
        assess.dump_findings(assess.assess(folder, "online")), encoding="utf-8"
    )
    packages = root / "packages"
    write_packages(
        build_packages(
            folder, read_source_map(source_map), None, assess.read_findings(findings_path)
        ),
        packages,
    )
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    out = root / "out"
    args = ["--json", "inventory", "convert", str(folder), "--findings", str(findings_path)]
    res = CliRunner().invoke(cli, [*args, "--packages", str(packages), "--out", str(out)])
    assert res.exit_code == 0, res.output
    ledger = json.loads((out / "ledger.json").read_text(encoding="utf-8"))
    unchanged = before == {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    return {"out": out, "entries": ledger["entries"], "unchanged": unchanged}


def _entries(run: dict, name: str, rule: str = RULE) -> list[dict]:
    return [e for e in run["entries"] if e["key"] == name and e["rule"] == rule]


def _reviews(run: dict, name: str, rule: str = RULE) -> list[dict]:
    return [m for e in _entries(run, name, rule) for m in e["manual_review"]]


def _fix() -> str:
    return next(r.fix for r in assess.load_rules().rules if r.id == RULE)


def test_the_rule_names_the_registered_review_transform():
    rule = next(r for r in assess.load_rules().rules if r.id == RULE)
    assert rule.transform == "soap-2011-review" and rule.transform in TRANSFORMS


def test_the_transform_matches_what_the_rule_detects():
    rule = next(r for r in assess.load_rules().rules if r.id == RULE)
    assert isinstance(rule.detection, assess.ContentPattern)
    assert _SOAP_2011.pattern.decode() == rule.detection.pattern.pattern


@pytest.mark.parametrize(
    ("case", "line", "snippet", "endpoint"),
    [
        (
            "organization",
            2,
            'x.open("POST", url + "/XRMServices/2011/Organization.svc/web", false);',
            "Organization.svc",
        ),
        ("discovery", 1, 'x.open("POST", "/XRMServices/2011/Discovery.svc");', "Discovery.svc"),
        (
            "upper-case",
            1,
            'x.open("POST", "/xrmservices/2011/ORGANIZATION.SVC/web");',
            "ORGANIZATION.SVC",
        ),
    ],
)
def test_a_soap_site_is_one_entry_with_snippet_and_fix(run, case, line, snippet, endpoint):
    (review,) = _reviews(run, NAMES[case])
    assert review["line"] == line
    assert review["snippet"] == snippet
    assert review["replacement"] == _fix()
    assert endpoint.lower() in review["reason"].lower()
    assert "synchronous SOAP call cannot be proved equivalent" in review["reason"]
    assert review["file"].endswith(".js")


def test_two_sites_give_two_entries(run):
    assert [m["line"] for m in _reviews(run, NAMES["two-sites"])] == [1, 3]


def test_two_sites_on_one_line_give_two_entries(run):
    assert [m["line"] for m in _reviews(run, NAMES["same-line"])] == [1, 1]


def test_the_inventory_bytes_are_unchanged_by_convert(run):
    assert run["unchanged"]


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_script_is_never_rewritten_for_this_rule(run, case):
    entries = _entries(run, NAMES[case])
    assert all(e["status"] == "manual-review" and e["files"] == {} for e in entries)


def test_a_comment_only_reference_is_a_whole_file_entry_without_snippet(run):
    (review,) = _reviews(run, NAMES["comment-only"])
    assert review["line"] is None
    assert "snippet" not in review
    assert review["replacement"] == _fix()


def test_an_html_page_gets_the_whole_file_entry(run):
    (review,) = _reviews(run, HTML_NAME)
    assert review["line"] is None and "snippet" not in review
    assert all(
        e["status"] == "manual-review" and e["files"] == {} for e in _entries(run, HTML_NAME)
    )


def test_a_script_with_another_transformable_finding_is_patched_and_reviewed(run):
    (review,) = _reviews(run, NAMES["mixed"])
    assert review["line"] == 2
    others = [e for e in run["entries"] if e["key"] == NAMES["mixed"] and e["rule"] != RULE]
    assert any(e["status"] == "patched" for e in others)
    assert len(_entries(run, NAMES["mixed"])) == 1


def test_the_2007_endpoint_still_has_no_transform(run):
    rule = next(r for r in assess.load_rules().rules if r.id == "web-resource-soap-2007")
    assert rule.transform is None
    crmform = next(r for r in assess.load_rules().rules if r.id == "web-resource-crmform")
    assert crmform.transform is None
    assert _entries(run, NAMES["soap-2007"], "web-resource-soap-2007") == []
