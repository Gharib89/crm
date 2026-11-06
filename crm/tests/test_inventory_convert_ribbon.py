"""`crm inventory convert`: ribbon actions get PrimaryControl for command functions (ADR 0034).

Each case is a library script plus ribbon diffs (and sometimes a form) calling its functions. It
runs through `assess`, `packages` and `convert` once, then asserts what landed under `--out`.
"""

# pyright: basic

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory_assess as assess
from crm.core.inventory_packages import build_packages, write_packages
from crm.core.inventory_source_map import dump_source_map, map_source, read_source_map
from crm.tests.test_inventory_assess import _form, _record, _registrations, _script
from crm.tests.test_inventory_convert_handlers import _event, _handler, _xml

LIB = "new_/scripts/acct.js"
RIBBON_RULE = "ribbon-action-no-primary-control"
FORM_LOC = "Mscrm.Form.account.MainTab.Actions.Controls._children"
GRID_LOC = "Mscrm.HomepageGrid.account.MainTab.Actions.Controls._children"
SUBGRID_LOC = "Mscrm.SubGrid.account.MainTab.Actions.Controls._children"

CMD_JS = "function run() {\n  return Xrm.Page.getAttribute('name');\n}\n"


def _call(function: str, *params: str, library: str = LIB) -> str:
    inner = "".join(f'<CrmParameter Value="{p}" />' for p in params)
    if not inner:
        return f'<JavaScriptFunction Library="$webresource:{library}" FunctionName="{function}" />'
    return (
        f'<JavaScriptFunction Library="$webresource:{library}" FunctionName="{function}">'
        f"{inner}</JavaScriptFunction>"
    )


def _diff_xml(
    commands: dict[str, str], shown: dict[str, str] | None = None, rules: str = ""
) -> str:
    """A diff of *commands* (id to action XML), each shown at its location in *shown*."""
    where = {c: FORM_LOC for c in commands} if shown is None else shown
    custom = "".join(
        f'<CustomAction Id="a{i}" Location="{loc}" Sequence="1"><CommandUIDefinition>'
        f'<Button Id="b{i}" Command="{cmd}" /></CommandUIDefinition></CustomAction>'
        for i, (cmd, loc) in enumerate(where.items())
    )
    defs = "\n".join(
        f'<CommandDefinition Id="{c}"><Actions>{a}</Actions></CommandDefinition>'
        for c, a in commands.items()
    )
    return (
        f"<RibbonDiffXml>\n<CustomActions>{custom}</CustomActions>\n"
        f"<CommandDefinitions>\n{defs}\n</CommandDefinitions>\n"
        f"<RuleDefinitions>{rules}</RuleDefinitions>\n</RibbonDiffXml>\n"
    )


def _pipeline(
    root: Path,
    js: str,
    diffs: dict[str, str],
    forms: dict[str, str] | None = None,
    scripts: dict[str, str] | None = None,
):
    folder = root / "inv"
    folder.mkdir(parents=True)
    records = [_script(LIB, js, folder)]
    records += [_script(name, text, folder) for name, text in (scripts or {}).items()]
    for entity, xml in diffs.items():
        records.append(
            _record(
                "ribbon_diff",
                {"entity": entity},
                folder,
                files=[("ribbon-diff.xml", xml.encode(), True)],
            )
        )
    # The effective ribbon holds the diff's actions, and is what links the diff to its library.
    records += [
        _record("ribbon", {"entity": entity}, folder, files=[("ribbon.xml", xml.encode(), True)])
        for entity, xml in diffs.items()
    ]
    records += [_form(name, xml.encode(), folder) for name, xml in (forms or {}).items()]
    _registrations(folder, records)
    repo = root / "repo"
    repo.mkdir(parents=True)
    source_map = root / "source-map.json"
    source_map.write_text(dump_source_map(map_source(folder, repo)), encoding="utf-8")
    findings = root / "findings.json"
    findings.write_text(assess.dump_findings(assess.assess(folder, "online")), encoding="utf-8")
    packages = root / "packages"
    built = build_packages(
        folder, read_source_map(source_map), None, assess.read_findings(findings)
    )
    write_packages(built, packages)
    out = root / "out"
    args = ["--json", "inventory", "convert", str(folder), "--findings", str(findings)]
    res = CliRunner().invoke(cli, [*args, "--packages", str(packages), "--out", str(out)])
    assert res.exit_code == 0, res.output
    ledger = json.loads((out / "ledger.json").read_text(encoding="utf-8"))
    return {"out": out, "entries": ledger["entries"], "records": records, "folder": folder}


def _entries(run: dict, kind: str, name: str | None = None) -> list[dict]:
    keys = {r.key for r in run["records"] if r.kind == kind}
    found = [e for e in run["entries"] if e["kind"] == kind and e["key"] in keys]
    return found if name is None else [e for e in found if name in e["key"]]


def _text(run: dict, entry: dict) -> str:
    return (run["out"] / entry["files"]["patched"]).read_text(encoding="utf-8")


def _js(run: dict) -> dict:
    (found,) = _entries(run, "web_resource")
    return found


def _js_status(run: dict) -> str:
    """The library's entry status; a library nothing rewrote and nothing flagged has none."""
    return "".join(e["status"] for e in _entries(run, "web_resource")) or "untouched"


def _ribbon(run: dict, entity: str = "account") -> dict:
    (found,) = _entries(run, "ribbon_diff", entity)
    return found


def test_a_form_command_gets_primary_control_first_and_its_function_takes_it(tmp_path):
    xml = _diff_xml({"cmd.run": _call("run")})
    run = _pipeline(tmp_path, CMD_JS, {"account": xml})
    js, ribbon = _js(run), _ribbon(run)
    assert js["status"] == "patched" and ribbon["status"] == "patched"
    assert js["package"] == ribbon["package"]
    assert _text(run, js) == (
        "function run(primaryControl) { var formContext = primaryControl;\n"
        "  return formContext.getAttribute('name');\n}\n"
    )
    assert _text(run, ribbon) == xml.replace(
        'FunctionName="run" />',
        'FunctionName="run" ><CrmParameter Value="PrimaryControl" /></JavaScriptFunction>',
    )
    assert ribbon["edits"] == 1 and ribbon["manual_review"] == []


def test_existing_crm_parameters_keep_their_order_behind_primary_control(tmp_path):
    js = "function run(a, b) {\n  return Xrm.Page.getAttribute(a) + b;\n}\n"
    xml = _diff_xml({"cmd.run": _call("run", "FirstPrimaryItemId", "OrgName")})
    run = _pipeline(tmp_path, js, {"account": xml})
    assert _text(run, _js(run)).startswith("function run(primaryControl, a, b) { var formContext")
    assert (
        '<CrmParameter Value="PrimaryControl" /><CrmParameter Value="FirstPrimaryItemId" />'
        '<CrmParameter Value="OrgName" />' in _text(run, _ribbon(run))
    )


def test_a_command_without_a_control_in_the_diff_is_a_form_command_by_its_id(tmp_path):
    xml = _diff_xml({"Mscrm.Form.account.Save": _call("run")}, shown={})
    run = _pipeline(tmp_path, CMD_JS, {"account": xml})
    assert _ribbon(run)["status"] == "patched" and _js(run)["status"] == "patched"


@pytest.mark.parametrize(
    ("command", "shown", "why"),
    [
        ("cmd.run", {"cmd.run": GRID_LOC}, "HomepageGrid"),
        ("cmd.run", {"cmd.run": SUBGRID_LOC}, "SubGrid"),
        ("cmd.run", {"cmd.run": FORM_LOC, "cmd.run ": GRID_LOC}, "not on a form"),
        ("cmd.run", {}, "names no control"),
        ("Mscrm.HomepageGrid.account.Run", {}, "names no control"),
    ],
)
def test_a_grid_subgrid_or_unplaced_command_is_manual_review(tmp_path, command, shown, why):
    xml = _diff_xml({command: _call("run")}, shown=shown)
    if "cmd.run " in shown:  # a second control naming the same command, on a grid
        xml = xml.replace('Command="cmd.run "', 'Command="cmd.run"')
    run = _pipeline(tmp_path, CMD_JS, {"account": xml})
    assert _ribbon(run)["status"] == "manual-review" and _ribbon(run)["files"] == {}
    assert why in _ribbon(run)["manual_review"][0]["reason"]
    assert not list(run["out"].rglob("*.patched*"))


def test_re_assess_on_the_patched_files_clears_the_ribbon_finding(tmp_path):
    xml = _diff_xml({"cmd.run": _call("run", "OrgName")})
    run = _pipeline(tmp_path / "a", CMD_JS, {"account": xml})
    patched_js, patched_xml = _text(run, _js(run)), _text(run, _ribbon(run))
    again = _pipeline(tmp_path / "b", patched_js, {"account": patched_xml})
    assert again["entries"] == []
    data = json.loads((tmp_path / "b" / "findings.json").read_text(encoding="utf-8"))
    mine = {r.key for r in again["records"]}
    flagged = {f["rule"] for f in data["findings"] if f["key"] in mine and f["class"] != "clear"}
    assert not flagged & {RIBBON_RULE, "web-resource-xrm-page"}
    # the ribbon alone clears even while the library is unpatched
    third = _pipeline(tmp_path / "c", CMD_JS, {"account": patched_xml})
    data = json.loads((tmp_path / "c" / "findings.json").read_text(encoding="utf-8"))
    diff = {r.key for r in third["records"] if r.kind == "ribbon_diff"}
    assert not [
        f
        for f in data["findings"]
        if f["rule"] == RIBBON_RULE and f["key"] in diff and f["class"] != "clear"
    ]


def test_a_helper_a_command_calls_gains_the_form_context(tmp_path):
    js = (
        "function run() {\n  return helper(1);\n}\n"
        "function helper(x) {\n  return Xrm.Page.getAttribute('a') + x;\n}\n"
    )
    run = _pipeline(tmp_path, js, {"account": _diff_xml({"cmd.run": _call("run")})})
    text = _text(run, _js(run))
    assert "function run(primaryControl) { var formContext = primaryControl;\n" in text
    assert "return helper(formContext, 1);" in text
    assert "function helper(formContext, x) {" in text and "formContext.getAttribute('a')" in text
    assert _ribbon(run)["status"] == "patched"


def test_a_function_that_is_also_a_form_handler_is_manual_review_on_both_sides(tmp_path):
    form = _xml(_event("onload", _handler("run")))
    run = _pipeline(
        tmp_path, CMD_JS, {"account": _diff_xml({"cmd.run": _call("run")})}, {"Main": form}
    )
    assert _ribbon(run)["status"] == "manual-review"
    (form_entry,) = _entries(run, "form")
    assert form_entry["status"] == "manual-review" and form_entry["files"] == {}
    assert not list(run["out"].rglob("*.patched*"))
    reasons = " ".join(m["reason"] for m in _ribbon(run)["manual_review"])
    assert "form handler" in reasons


def test_a_form_handler_a_custom_rule_also_calls_is_not_rewritten(tmp_path):
    rule = (
        f'<EnableRule Id="r"><CustomRule Library="$webresource:{LIB}" FunctionName="run"'
        ' Default="true" /></EnableRule>'
    )
    form = _xml(_event("onload", _handler("run")))
    run = _pipeline(
        tmp_path,
        CMD_JS,
        {"account": _diff_xml({"cmd.other": _call("other")}, rules=rule)},
        {"Main": form},
    )
    (form_entry,) = _entries(run, "form")
    assert form_entry["status"] == "manual-review" and form_entry["files"] == {}
    assert _js_status(run) != "patched"


def test_commands_sharing_a_function_are_patched_only_with_the_same_parameters(tmp_path):
    same = _diff_xml({"cmd.a": _call("run", "OrgName"), "cmd.b": _call("run", "OrgName")})
    run = _pipeline(tmp_path / "same", CMD_JS, {"account": same})
    assert _ribbon(run)["status"] == "patched" and _ribbon(run)["edits"] == 2
    differ = _diff_xml({"cmd.a": _call("run", "OrgName"), "cmd.b": _call("run")})
    run = _pipeline(tmp_path / "differ", CMD_JS, {"account": differ})
    assert _ribbon(run)["status"] == "manual-review" and _js_status(run) != "patched"
    assert "different CrmParameters" in _ribbon(run)["manual_review"][0]["reason"]
    assert not list(run["out"].rglob("*.patched*"))


def test_a_function_called_from_another_diff_is_patched_there_too_or_not_at_all(tmp_path):
    xml = _diff_xml({"cmd.run": _call("run")})
    run = _pipeline(tmp_path / "all", CMD_JS, {"account": xml, "contact": xml})
    assert [e["status"] for e in _entries(run, "ribbon_diff")] == ["patched", "patched"]
    passing = _diff_xml({"cmd.run": _call("run", "PrimaryControl")})
    run = _pipeline(tmp_path / "one", CMD_JS, {"account": xml, "contact": passing})
    assert _ribbon(run)["status"] == "manual-review"
    assert "already called with PrimaryControl" in _ribbon(run)["manual_review"][0]["reason"]
    assert _js_status(run) != "patched"


def test_a_custom_rule_calling_the_function_keeps_it_whole(tmp_path):
    rule = (
        f'<EnableRule Id="r"><CustomRule Library="$webresource:{LIB}" FunctionName="run"'
        ' Default="true" /></EnableRule>'
    )
    run = _pipeline(tmp_path, CMD_JS, {"account": _diff_xml({"cmd.run": _call("run")}, rules=rule)})
    assert _ribbon(run)["status"] == "manual-review" and _js_status(run) != "patched"
    assert "CustomRule" in _ribbon(run)["manual_review"][0]["reason"]


def test_a_custom_rule_of_an_unmappable_diff_keeps_the_function_whole(tmp_path):
    rule = (
        f'<EnableRule Id="r"><CustomRule Library="$webresource:{LIB}" FunctionName="run"'
        ' Default="true" /></EnableRule>'
    )
    bad = _diff_xml({"cmd.other": _call("other")}, rules=rule).replace(
        "<RuleDefinitions>", "<!-- <JavaScriptFunction FunctionName='x' /> --><RuleDefinitions>"
    )
    good = _diff_xml({"cmd.run": _call("run")})
    run = _pipeline(tmp_path, CMD_JS, {"account": good, "contact": bad})
    assert not list(run["out"].rglob("*.patched*"))
    assert "cannot be patched" in _ribbon(run, "account")["manual_review"][0]["reason"]


def test_a_function_another_web_resource_calls_is_manual_review(tmp_path):
    other = {"new_/scripts/caller.js": "function go() {\n  run();\n}\n"}
    run = _pipeline(
        tmp_path, CMD_JS, {"account": _diff_xml({"cmd.run": _call("run")})}, scripts=other
    )
    assert _ribbon(run)["status"] == "manual-review" and _js_status(run) != "patched"
    assert "another web resource" in _ribbon(run)["manual_review"][0]["reason"]


def test_a_shadowed_xrm_or_a_nested_callback_stays_manual_review(tmp_path):
    shadowed = "var Xrm = window.parent.Xrm;\n" + CMD_JS
    run = _pipeline(tmp_path / "s", shadowed, {"account": _diff_xml({"cmd.run": _call("run")})})
    assert _js_status(run) != "patched" and _ribbon(run)["status"] == "manual-review"
    nested = "function run() {\n  setTimeout(function () {\n    Xrm.Page.a();\n  }, 1);\n}\n"
    run = _pipeline(tmp_path / "n", nested, {"account": _diff_xml({"cmd.run": _call("run")})})
    assert _js_status(run) != "patched" and _ribbon(run)["status"] == "manual-review"


def test_a_command_function_already_using_primary_control_is_manual_review(tmp_path):
    js = "function run() {\n  var primaryControl = 1;\n  return Xrm.Page.a;\n}\n"
    run = _pipeline(tmp_path, js, {"account": _diff_xml({"cmd.run": _call("run")})})
    assert _js_status(run) != "patched" and _ribbon(run)["status"] == "manual-review"
    assert "primaryControl" in _js(run)["manual_review"][0]["reason"]


def test_a_ribbon_that_cannot_be_mapped_to_its_text_is_not_patched(tmp_path):
    xml = _diff_xml({"cmd.run": _call("run")}).replace(
        "<RuleDefinitions>", "<!-- <JavaScriptFunction FunctionName='x' /> --><RuleDefinitions>"
    )
    run = _pipeline(tmp_path, CMD_JS, {"account": xml})
    assert _ribbon(run)["status"] == "manual-review" and _js_status(run) != "patched"
    assert not list(run["out"].rglob("*.patched*"))


def test_quoted_attribute_values_with_a_bracket_or_escapes_are_read_and_patched(tmp_path):
    action = (
        "<JavaScriptFunction Library='$webresource:new_/scripts/acct.js' "
        "FunctionName='r&#117;n' Note=\"a>b\" />"
    )
    run = _pipeline(tmp_path, CMD_JS, {"account": _diff_xml({"cmd.run": action})})
    assert _ribbon(run)["status"] == "patched" and _js(run)["status"] == "patched"
    assert 'Note="a>b" ><CrmParameter Value="PrimaryControl" />' in _text(run, _ribbon(run))


def test_a_function_an_unmappable_diff_calls_is_not_changed_behind_it(tmp_path):
    good = _diff_xml({"cmd.run": _call("run")})
    bad = _diff_xml({"cmd.run": _call("run")}).replace(
        "<RuleDefinitions>", "<!-- <JavaScriptFunction FunctionName='x' /> --><RuleDefinitions>"
    )
    run = _pipeline(tmp_path, CMD_JS, {"account": good, "contact": bad})
    assert not list(run["out"].rglob("*.patched*"))
    assert "cannot be patched" in _ribbon(run, "account")["manual_review"][0]["reason"]


def test_a_diff_that_vanished_after_assessment_still_counts_as_a_caller(tmp_path):
    inv = tmp_path / "inv"
    run = _pipeline(
        tmp_path / "first",
        CMD_JS,
        {
            "account": _diff_xml({"cmd.run": _call("run")}),
            "contact": _diff_xml({"cmd.x": _call("run")}),
        },
    )
    # Reuse the first run's inventory, then remove one diff before converting again.
    folder = run["folder"]
    (diff,) = [
        folder / r.artifacts[0].path
        for r in run["records"]
        if r.kind == "ribbon_diff" and r.fields["entity"] == "contact"
    ]
    diff.unlink()
    again = tmp_path / "again"
    findings = tmp_path / "first" / "findings.json"
    packages = tmp_path / "first" / "packages"
    res = CliRunner().invoke(
        cli,
        [
            "--json",
            "inventory",
            "convert",
            str(folder),
            "--findings",
            str(findings),
            "--packages",
            str(packages),
            "--out",
            str(again),
        ],
    )
    assert res.exit_code == 0, res.output
    assert not list(again.rglob("*.patched*"))
    assert inv.name == "inv"


def test_a_control_outside_any_custom_action_does_not_place_the_command_on_a_form(tmp_path):
    xml = _diff_xml({"cmd.run": _call("run")}, shown={}).replace(
        "<RuleDefinitions>",
        '<Templates><Button Id="t" Command="cmd.run" /></Templates><RuleDefinitions>',
    )
    run = _pipeline(tmp_path, CMD_JS, {"account": xml})
    assert _ribbon(run)["status"] == "manual-review"
    assert "outside any CustomAction" in _ribbon(run)["manual_review"][0]["reason"]


def test_an_optional_chain_stays_manual_review_while_a_string_and_a_comment_stay_as_written(
    tmp_path,
):
    js = (
        "function run() {\n"
        "  var s = 'Xrm.Page.x'; // Xrm.Page.y\n"
        "  var a = Xrm.Page?.getAttribute('a');\n"
        "  return Xrm.Page.getAttribute(s);\n"
        "}\n"
    )
    run = _pipeline(tmp_path, js, {"account": _diff_xml({"cmd.run": _call("run")})})
    text = _text(run, _js(run))
    assert (
        "var s = 'Xrm.Page.x'; // Xrm.Page.y" in text
        and "return formContext.getAttribute(s);" in text
    )
    assert [m["line"] for m in _js(run)["manual_review"]] == [3]
