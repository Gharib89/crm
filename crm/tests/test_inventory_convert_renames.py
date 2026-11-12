"""`crm inventory convert`: the deprecation-table renames, through the CLI seam (ADR 0034).

One inventory holds a web resource per case. It runs through `assess`, `packages` and the
`convert` CLI once, then each case asserts what landed under `--out`.
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

GC = "Xrm.Utility.getGlobalContext()"
CTX = "Xrm.Page.context"

#: name: (before, after or None when the code must stay untouched, rule, manual-review count)
CASES: dict[str, tuple[str, str | None, str, int]] = {
    "user-name": (
        f"var n = {CTX}.getUserName();\n",
        f"var n = {GC}.userSettings.userName;\n",
        "web-resource-context-settings",
        0,
    ),
    "user-id-and-lcid": (
        f"var a = {CTX}.getUserId();\nvar b = {CTX}.getUserLcid();\n",
        f"var a = {GC}.userSettings.userId;\nvar b = {GC}.userSettings.languageId;\n",
        "web-resource-context-settings",
        0,
    ),
    "time-zone": (
        f"var z = {CTX}.getTimeZoneOffsetMinutes();\n",
        f"var z = {GC}.userSettings.getTimeZoneOffsetMinutes();\n",
        "web-resource-context-settings",
        0,
    ),
    "org-settings": (
        f"var u = {CTX}.getOrgUniqueName();\n"
        f"var l = {CTX}.getOrgLcid();\n"
        f"var s = {CTX}.getIsAutoSaveEnabled();\n",
        f"var u = {GC}.organizationSettings.uniqueName;\n"
        f"var l = {GC}.organizationSettings.languageId;\n"
        f"var s = {GC}.organizationSettings.isAutoSaveEnabled;\n",
        "web-resource-context-settings",
        0,
    ),
    "global-context-form": (
        f"var n = {GC}.getUserName();\n",
        f"var n = {GC}.userSettings.userName;\n",
        "web-resource-context-settings",
        0,
    ),
    "roles-passed-on": (
        f"check({CTX}.getUserRoles());\n",
        None,
        "web-resource-context-settings",
        1,
    ),
    "roles-read-as-list": (
        f"var first = {CTX}.getUserRoles()[0];\nvar n = {CTX}.getUserRoles().length;\n",
        None,
        "web-resource-context-settings",
        2,
    ),
    "roles-via-variable": (
        f"var r = {CTX}.getUserRoles();\nr.forEach(f);\n",
        None,
        "web-resource-context-settings",
        1,
    ),
    "optional-chain": (
        f"var n = {CTX}?.getUserName();\nvar m = {CTX}.getUserName?.();\n",
        None,
        "web-resource-context-settings",
        2,
    ),
    "window-qualified": (
        f"var n = window.{CTX}.getUserName();\n",
        None,
        "web-resource-context-settings",
        1,
    ),
    "shadowed-xrm": (
        "function f(Xrm) { return Xrm.Mobile.offline.createRecord(1); }\n",
        None,
        "web-resource-xrm-mobile-offline",
        1,
    ),
    "server-url-receivers": (
        f"var a = {CTX}.getServerUrl();\nvar b = {GC}.getServerUrl();\n"
        "var c = GetGlobalContext().getServerUrl();\n"
        "var d = GetGlobalContext(). getServerUrl ();\n",
        f"var a = {GC}.getClientUrl();\nvar b = {GC}.getClientUrl();\n"
        "var c = GetGlobalContext().getClientUrl();\n"
        "var d = GetGlobalContext(). getClientUrl ();\n",
        "web-resource-get-server-url",
        0,
    ),
    "server-url-not-provable": (
        "function f(Xrm) { return Xrm.Page.context.getServerUrl(); }\n"
        "var a = window.Xrm.Page.context.getServerUrl();\n"
        f"var b = {CTX}?.getServerUrl();\nvar c = {CTX}.getServerUrl?.();\n"
        "var p = parent.Xrm.Page.context.getServerUrl();\n"
        'var d = Xrm["Page"].context.getServerUrl();\n'
        f"var e = {CTX}.getServerUrl(1);\nvar f = {CTX}.getServerUrl;\n"
        f"var g = {GC}\n  .getServerUrl(\n);\n"
        "var h = foo.getServerUrl();\n",
        None,
        "web-resource-get-server-url",
        10,
    ),
    "server-url-own-get-global-context": (
        "function GetGlobalContext() { return ctx; }\nvar a = GetGlobalContext().getServerUrl();\n",
        None,
        "web-resource-get-server-url",
        1,
    ),
    "server-url-bound-get-global-context": (
        "var GetGlobalContext = make();\nvar a = GetGlobalContext().getServerUrl();\n"
        "function g(GetGlobalContext) { return GetGlobalContext().getServerUrl(); }\n",
        None,
        "web-resource-get-server-url",
        2,
    ),
    "server-url-spaced-xrm": (
        f"var a = {GC}. getServerUrl ();\nvar b = {CTX} . getServerUrl();\n",
        f"var a = {GC}. getClientUrl ();\nvar b = {GC} . getClientUrl();\n",
        "web-resource-get-server-url",
        0,
    ),
    "server-url-text-only": (
        '// ctx.getServerUrl()\nvar s = "ctx.getServerUrl()";\n'
        "var a = GetGlobalContext().getServerUrl();\n",
        '// ctx.getServerUrl()\nvar s = "ctx.getServerUrl()";\n'
        "var a = GetGlobalContext().getClientUrl();\n",
        "web-resource-get-server-url",
        0,
    ),
    "grid-row": (
        "var e = row.getData().getEntity();\n",
        "var e = row.data.entity;\n",
        "web-resource-grid-row",
        0,
    ),
    "grid-lone-calls": (
        "var d = row.getData();\nvar e = d.getEntity();\n",
        None,
        "web-resource-grid-row",
        2,
    ),
    "mobile-offline": (
        'Xrm.Mobile.offline.createRecord("account", data).then(ok, fail);\n',
        'Xrm.WebApi.offline.createRecord("account", data).then(ok, fail);\n',
        "web-resource-xrm-mobile-offline",
        0,
    ),
    "mobile-is-offline-enabled": (
        'var on = Xrm.Mobile.offline.isOfflineEnabled("account");\n',
        None,
        "web-resource-xrm-mobile-offline",
        1,
    ),
    "entity-save": (
        "formContext.data.entity.save();\n",
        "formContext.data.save();\n",
        "web-resource-entity-save",
        0,
    ),
    "entity-save-reference": (
        "var s = formContext.data.entity.save.bind(formContext.data.entity);\n",
        None,
        "web-resource-entity-save",
        1,
    ),
    "entity-save-shadowed-xrm": (
        "function f(Xrm) { Xrm.Page.data.entity.save(); }\n",
        None,
        "web-resource-entity-save",
        1,
    ),
    "entity-save-with-option": (
        'formContext.data.entity.save("saveandnew");\n',
        None,
        "web-resource-entity-save",
        1,
    ),
    "utility-dialogs": (
        'Xrm.Utility.alertDialog("a");\nXrm.Utility.confirmDialog("b", ok);\n'
        'var t = Xrm.Utility.isActivityType("email");\n',
        None,
        "web-resource-xrm-utility",
        3,
    ),
    "utility-navigation": (
        'Xrm.Utility.openEntityForm("account");\nXrm.Utility.openQuickCreate("account");\n'
        'Xrm.Utility.openWebResource("new_page");\n',
        None,
        "web-resource-xrm-utility",
        3,
    ),
    "keypress": (
        "control.addOnKeyPress(handler);\ncontrol.showAutoComplete(list);\n",
        None,
        "web-resource-keypress",
        2,
    ),
}
MULTI = (
    f"var u = {CTX}.getUserName();\n"
    f"var url = {CTX}.getClientUrl();\n"
    'Xrm.Utility.openEntityForm("account");\n'
    "var e = row.getData().getEntity();\n"
    "var raw = row.getData();\n"
)
SERVER_URL_MIXED = f"var a = {GC}.getServerUrl();\nvar b = foo.getServerUrl();\n"

#: A comment inside the chain: context-settings' detection sees through it, so the rule patches
#: the site (#1246).
COMMENT_CHAIN = f"var id = {CTX}./* who */getUserId();\n"
#: Same shape for getServerUrl: its own rule patches it too.
SERVER_URL_COMMENT_CHAIN = f"var u = {CTX}./* who */getServerUrl();\n"


def _name(case: str) -> str:
    return f"new_/scripts/{case}.js"


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    root = tmp_path_factory.mktemp("renames")
    folder = root / "inv"
    folder.mkdir()
    sources = {c: v[0] for c, v in CASES.items()} | {"multi": MULTI, "comment-chain": COMMENT_CHAIN}
    sources["server-url-mixed"] = SERVER_URL_MIXED
    sources["server-url-comment-chain"] = SERVER_URL_COMMENT_CHAIN
    records = [_record(_name(c), text, folder) for c, text in sources.items()]
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
    findings = assess.assess(folder, "online")
    findings_path.write_text(assess.dump_findings(findings), encoding="utf-8")
    packages = root / "packages"
    built = build_packages(
        folder, read_source_map(source_map), None, assess.read_findings(findings_path)
    )
    write_packages(built, packages)
    out = root / "out"
    args = ["--json", "inventory", "convert", str(folder), "--findings", str(findings_path)]
    res = CliRunner().invoke(cli, [*args, "--packages", str(packages), "--out", str(out)])
    assert res.exit_code == 0, res.output
    ledger = json.loads((out / "ledger.json").read_text(encoding="utf-8"))
    return {
        "out": out,
        "entries": ledger["entries"],
        "findings": findings_path,
        "folder": folder,
        "packages": packages,
        "root": root,
    }


def _entries(run: dict, case: str, rule: str | None = None) -> list[dict]:
    return [
        e for e in run["entries"] if e["key"] == _name(case) and (rule is None or e["rule"] == rule)
    ]


def _patched(run: dict, entry: dict) -> str:
    return (run["out"] / entry["files"]["patched"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("case", sorted(CASES))
def test_assess_reports_the_rule_for_each_case(run, case):
    data = json.loads(run["findings"].read_text(encoding="utf-8"))
    rules = {f["rule"] for f in data["findings"] if f["key"] == _name(case)}
    assert CASES[case][2] in rules


@pytest.mark.parametrize("case", sorted(c for c, v in CASES.items() if v[1] is not None))
def test_a_convertible_row_has_a_before_and_after(run, case):
    _, after, rule, _ = CASES[case]
    (entry,) = _entries(run, case, rule)
    assert entry["status"] == "patched" and entry["manual_review"] == []
    assert _patched(run, entry) == after


@pytest.mark.parametrize("case", sorted(c for c, v in CASES.items() if v[1] is None))
def test_a_row_that_cannot_convert_is_manual_review_and_untouched(run, case):
    before, _, rule, count = CASES[case]
    (entry,) = _entries(run, case, rule)
    assert entry["status"] == "manual-review" and entry["files"] == {}
    reviews = entry["manual_review"]
    assert len(reviews) == count and {m["rule"] for m in reviews} == {rule}
    assert all(m["line"] and m["reason"] for m in reviews)
    assert not [e for e in _entries(run, case) if e["status"] == "patched"]
    assert not list(run["out"].rglob(f"*{case}*patched*"))


def test_one_file_keeps_every_rules_edits_and_review_entries(run):
    entries = _entries(run, "multi")
    patched = {e["rule"]: e for e in entries if e["status"] == "patched"}
    assert set(patched) == {
        "web-resource-context-settings",
        "web-resource-grid-row",
        "web-resource-xrm-page",
    }
    text = _patched(run, next(iter(patched.values())))
    assert text == (
        f"var u = {GC}.userSettings.userName;\n"
        f"var url = {GC}.getClientUrl();\n"
        'Xrm.Utility.openEntityForm("account");\n'
        "var e = row.data.entity;\n"
        "var raw = row.getData();\n"
    )
    reviews = {(m["rule"], m["line"]) for e in entries for m in e["manual_review"]}
    assert reviews == {
        ("web-resource-xrm-utility", 3),
        ("web-resource-grid-row", 5),
    }


def test_findings_made_before_the_context_settings_rule_still_convert_its_sites(run):
    data = json.loads(run["findings"].read_text(encoding="utf-8"))
    data["findings"] = [f for f in data["findings"] if f["rule"] != "web-resource-context-settings"]
    old = run["root"] / "old-findings.json"
    old.write_text(json.dumps(data), encoding="utf-8")
    out = run["root"] / "old-out"
    args = ["--json", "inventory", "convert", str(run["folder"]), "--findings", str(old)]
    res = CliRunner().invoke(cli, [*args, "--packages", str(run["packages"]), "--out", str(out)])
    assert res.exit_code == 0, res.output
    ledger = json.loads((out / "ledger.json").read_text(encoding="utf-8"))
    mine = [e for e in ledger["entries"] if e["key"] == _name("user-name")]
    patched = {e["rule"] for e in mine if e["status"] == "patched"}
    assert "web-resource-context-settings" in patched


def test_a_comment_inside_the_chain_does_not_hide_the_site_from_its_rule(run):
    (entry,) = _entries(run, "comment-chain", "web-resource-context-settings")
    assert entry["status"] == "patched" and not entry["manual_review"]
    assert _patched(run, entry) == f"var id = {GC}./* who */userSettings.userId;\n"


def test_a_provable_and_an_unprovable_get_server_url_share_one_patch(run):
    (entry,) = _entries(run, "server-url-mixed", "web-resource-get-server-url")
    assert entry["status"] == "patched"
    assert _patched(run, entry) == f"var a = {GC}.getClientUrl();\nvar b = foo.getServerUrl();\n"
    (review,) = entry["manual_review"]
    assert review["line"] == 2 and review["rule"] == "web-resource-get-server-url"
    assert "receiver" in review["reason"]


def test_a_comment_inside_a_get_server_url_chain_does_not_hide_the_site_from_its_rule(run):
    (entry,) = _entries(run, "server-url-comment-chain", "web-resource-get-server-url")
    assert entry["status"] == "patched" and not entry["manual_review"]
    assert _patched(run, entry) == f"var u = {GC}./* who */getClientUrl();\n"
