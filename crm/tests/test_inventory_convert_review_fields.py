"""`crm inventory convert`: a manual-review entry's `snippet`, `replacement`, `return_use` (#1202).

One inventory holds a web resource per case. It runs through `assess`, `packages` and the
`convert` CLI once, then each case asserts the manual-review entries under `--out`.
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
from crm.tests.test_inventory_convert import ORG, _record

CTX = "Xrm.Page.context"
NEW_FIELDS = ("snippet", "replacement", "return_use")
LONG_ARG = "x" * 300

#: case: (source, line of the site, expected snippet, expected return_use)
ROLES: dict[str, tuple[str, int, str, str]] = {
    "assigned": (
        f"var r = {CTX}.getUserRoles();\n",
        1,
        f"var r = {CTX}.getUserRoles();",
        "assigned",
    ),
    "reassigned": (f"r = {CTX}.getUserRoles();\n", 1, f"r = {CTX}.getUserRoles();", "assigned"),
    "returned": (
        f"function f() {{\n  return {CTX}.getUserRoles();\n}}\n",
        2,
        f"return {CTX}.getUserRoles();",
        "returned",
    ),
    "arrow-returned": (
        f"var f = () => {CTX}.getUserRoles();\n",
        1,
        f"var f = () => {CTX}.getUserRoles();",
        "returned",
    ),
    "argument": (f"check({CTX}.getUserRoles());\n", 1, f"check({CTX}.getUserRoles());", "argument"),
    "index": (
        f"var a = {CTX}.getUserRoles()[0];\n",
        1,
        f"var a = {CTX}.getUserRoles()[0];",
        "member",
    ),
    "length": (
        f"var n = {CTX}.getUserRoles().length;\n",
        1,
        f"var n = {CTX}.getUserRoles().length;",
        "member",
    ),
    "method": (
        f"var h = {CTX}.getUserRoles().indexOf(id) >= 0;\n",
        1,
        f"var h = {CTX}.getUserRoles().indexOf(id) >= 0;",
        "member",
    ),
    "parenthesised": (
        f"var n = ({CTX}.getUserRoles()).length;\n",
        1,
        f"var n = ({CTX}.getUserRoles()).length;",
        "member",
    ),
    "one-line-if-header": (
        f"if ({CTX}.getUserRoles().length) {{ foo(); }}\n",
        1,
        f"if ({CTX}.getUserRoles().length) {{ foo(); }}",
        "member",
    ),
    "one-line-if-body": (
        f"if (ok) {{ check({CTX}.getUserRoles()); }}\n",
        1,
        f"check({CTX}.getUserRoles());",
        "argument",
    ),
    "after-a-rename-on-the-line": (
        f"{CTX}.getClientUrl(); var r = {CTX}.getUserRoles();\n",
        1,
        f"var r = {CTX}.getUserRoles();",
        "assigned",
    ),
    "default-parameter": (
        f"function f(r = {CTX}.getUserRoles()) {{}}\n",
        1,
        f"function f(r = {CTX}.getUserRoles()) {{}}",
        "other",
    ),
    "discarded": (f"{CTX}.getUserRoles();\n", 1, f"{CTX}.getUserRoles();", "discarded"),
    "other": (
        f"var r = {CTX}.getUserRoles() || [];\n",
        1,
        f"var r = {CTX}.getUserRoles() || [];",
        "other",
    ),
    "reference": (f"var f = {CTX}.getUserRoles;\n", 1, f"var f = {CTX}.getUserRoles;", "other"),
    "multi-line": (
        f"var r =\n  {CTX}.getUserRoles();\n",
        2,
        f"var r =\n  {CTX}.getUserRoles();",
        "assigned",
    ),
}
ROLES_NAMES = {c: f"new_/scripts/roles-{c}.js" for c in ROLES}
OTHERS = {
    "utility": (
        '  Xrm.Utility.alertDialog("a");\n',
        "web-resource-xrm-utility",
        'Xrm.Utility.alertDialog("a");',
    ),
    "utility-second-on-line": (
        'if (ok) { go(); } Xrm.Utility.alertDialog("b");\n',
        "web-resource-xrm-utility",
        'Xrm.Utility.alertDialog("b");',
    ),
    "keypress": (
        "control.addOnKeyPress(handler);\n",
        "web-resource-keypress",
        "control.addOnKeyPress(handler);",
    ),
    "grid": ("var d = row.getData();\n", "web-resource-grid-row", "var d = row.getData();"),
    "mobile": (
        "var on = Xrm.Mobile.offline.isOfflineEnabled('a');\n",
        "web-resource-xrm-mobile-offline",
        "var on = Xrm.Mobile.offline.isOfflineEnabled('a');",
    ),
    "save": (
        "formContext.data.entity.save('saveandnew');\n",
        "web-resource-entity-save",
        "formContext.data.entity.save('saveandnew');",
    ),
    "rename": (f"var c = {CTX};\n", "web-resource-xrm-page", f"var c = {CTX};"),
}
OTHERS_NAMES = {c: f"new_/scripts/other-{c}.js" for c in OTHERS}
LONG_NAME = "new_/scripts/long.js"
LONG_SOURCE = f'check({CTX}.getUserRoles(), "{LONG_ARG}");\n'
SHORT_PAD_NAME = "new_/scripts/pad.js"
PAD_SOURCE = f"\n   check({CTX}.getUserRoles());   \n"
WHOLE_NAME = "new_/html/page.htm"
WHOLE_SOURCE = "<script>Xrm.Page.context.getUserId()</script>"


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("review-fields")
    folder = root / "inv"
    folder.mkdir()
    records = [_record(ROLES_NAMES[c], v[0], folder) for c, v in ROLES.items()]
    records += [_record(OTHERS_NAMES[c], v[0], folder) for c, v in OTHERS.items()]
    records += [
        _record(LONG_NAME, LONG_SOURCE, folder),
        _record(SHORT_PAD_NAME, PAD_SOURCE, folder),
        _record(WHOLE_NAME, WHOLE_SOURCE, folder, code=1, ext="htm"),
    ]
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
    out = root / "out"
    args = ["--json", "inventory", "convert", str(folder), "--findings", str(findings_path)]
    res = CliRunner().invoke(cli, [*args, "--packages", str(packages), "--out", str(out)])
    assert res.exit_code == 0, res.output
    ledger = json.loads((out / "ledger.json").read_text(encoding="utf-8"))
    return {"out": out, "entries": ledger["entries"], "ledger": ledger}


def _reviews(run: dict, key: str, rule: str | None = None) -> list[dict]:
    return [
        m
        for e in run["entries"]
        if e["key"] == key and (rule is None or e["rule"] == rule)
        for m in e["manual_review"]
    ]


@pytest.mark.parametrize("case", sorted(ROLES))
def test_a_get_user_roles_entry_carries_snippet_replacement_and_return_use(run, case):
    _, line, snippet, use = ROLES[case]
    (review,) = _reviews(run, ROLES_NAMES[case], "web-resource-context-settings")
    assert review["line"] == line
    assert review["snippet"] == snippet
    assert review["return_use"] == use
    assert review["replacement"] == _fix("web-resource-context-settings")


@pytest.mark.parametrize("case", sorted(OTHERS))
def test_other_rule_driven_entries_carry_snippet_and_replacement_but_no_return_use(run, case):
    _, rule, snippet = OTHERS[case]
    (review,) = _reviews(run, OTHERS_NAMES[case], rule)
    assert review["snippet"] == snippet
    assert review["replacement"] == _fix(rule)
    assert "return_use" not in review


def test_a_whole_file_entry_carries_replacement_and_no_snippet(run):
    reviews = _reviews(run, WHOLE_NAME)
    assert reviews and all(m["line"] is None for m in reviews)
    assert all("snippet" not in m and "return_use" not in m for m in reviews)
    assert all(m["replacement"] == _fix(m["rule"]) for m in reviews)


def test_a_long_statement_is_cut_to_the_cap_and_ends_in_an_ellipsis(run):
    (review,) = _reviews(run, LONG_NAME, "web-resource-context-settings")
    assert len(review["snippet"]) == 240 and review["snippet"].endswith("...")
    assert review["snippet"].startswith(f'check({CTX}.getUserRoles(), "xxx')


def test_a_short_statement_is_kept_whole_and_trimmed(run):
    (review,) = _reviews(run, SHORT_PAD_NAME, "web-resource-context-settings")
    assert review["snippet"] == f"check({CTX}.getUserRoles());"


def test_the_existing_keys_stay_and_an_unknown_field_is_omitted_not_null(run):
    for entry in run["entries"]:
        for m in entry["manual_review"]:
            assert {"file", "line", "rule", "reason"} <= set(m)
            assert all(m[k] is not None for k in NEW_FIELDS if k in m)
    assert run["ledger"]["schema_version"] == 1


def test_get_user_roles_code_is_untouched_and_no_patch_is_written(run):
    for case in ROLES:
        entries = [
            e
            for e in run["entries"]
            if e["key"] == ROLES_NAMES[case] and e["rule"] == "web-resource-context-settings"
        ]
        assert entries and all(e["status"] == "manual-review" and e["files"] == {} for e in entries)
    # The rename case patches its getClientUrl() under another rule, never the roles site.
    assert not list(run["out"].rglob("*roles-[!a]*patched*"))


def _fix(rule_id: str) -> str:
    return {r.id: r.fix for r in assess.load_rules().rules}[rule_id]
