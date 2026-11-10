"""`crm inventory convert`: a ribbon command loads the OData v2 adapter first (ADR 0034).

Each case is a library script plus ribbon diffs calling it. Everything runs through `assess`,
`packages` and the `convert` CLI, then asserts what landed under `--out`: the patched RibbonDiffXml,
the ledger's ribbon_diff entries and their manual-review reasons.
"""

# pyright: basic

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory_assess as assess
from crm.core.inventory_odata import render_adapter
from crm.core.inventory_packages import build_packages, write_packages
from crm.core.inventory_ribbon import plan_ribbon_adapter, read_ribbon_diff
from crm.core.inventory_source_map import map_source
from crm.tests.test_inventory_assess import _record as _component
from crm.tests.test_inventory_convert import _record
from crm.tests.test_inventory_convert_odata import SVC, _entity, _write
from crm.tests.test_inventory_convert_ribbon import FORM_LOC, _call

RULE = "ribbon-action-no-odata-adapter"
HANDLER_RULE = "ribbon-action-no-primary-control"
ADAPTER = "new_/scripts/crm-odata-v2-adapter.js"
LOAD = f'<JavaScriptFunction Library="$webresource:{ADAPTER}" FunctionName="CrmODataV2.load" />'

ACCT, OTHER, BAD, CLEAN, CTX, REAL = (
    f"new_/scripts/{n}.js" for n in ("acct", "other", "bad", "clean", "ctx", "real-adapter")
)
READ = (
    f'var req = new XMLHttpRequest();\nreq.open("GET", "{SVC}/AccountSet?$select=Name", false);\n'
)
SCRIPTS = {
    ACCT: READ + "req.send();\n",
    OTHER: READ + "req.send();\n",
    BAD: f'var req = new XMLHttpRequest();\nreq.open("POST", "{SVC}/AccountSet", false);\n',
    CLEAN: "function f() {\n  return 1;\n}\n",
    REAL: "(function (global) {\n  global.CrmODataV2 = {};\n})(window);\n",
    CTX: "function run() {\n  var n = Xrm.Page.getAttribute('name');\n" + READ + "}\n",
}


def _cmd(command: str, *actions: str, enable: tuple[str, ...] = ()) -> str:
    rules = "".join(f'<EnableRule Id="{r}" />' for r in enable)
    return (
        f'<CommandDefinition Id="{command}"><EnableRules>{rules}</EnableRules>'
        f"<DisplayRules /><Actions>{''.join(actions)}</Actions></CommandDefinition>"
    )


def _rule(rule: str, library: str, function: str = "check") -> str:
    return (
        f'<EnableRule Id="{rule}"><CustomRule Library="$webresource:{library}"'
        f' FunctionName="{function}" Default="true" /></EnableRule>'
    )


def _diff(*commands: str, rules: str = "") -> str:
    shown = "".join(
        f'<CustomAction Id="a{i}" Location="{FORM_LOC}" Sequence="1"><CommandUIDefinition>'
        f'<Button Id="b{i}" Command="{re.findall(r'Id="([^"]+)"', c)[0]}" />'
        f"</CommandUIDefinition></CustomAction>"
        for i, c in enumerate(commands)
    )
    return (
        f"<RibbonDiffXml>\n<CustomActions>{shown}</CustomActions>\n"
        f"<CommandDefinitions>\n{chr(10).join(commands)}\n</CommandDefinitions>\n"
        f"<RuleDefinitions><EnableRules>{rules}</EnableRules></RuleDefinitions>\n</RibbonDiffXml>\n"
    )


def _pipeline(root: Path, diffs: dict[str, str], *, scripts=None, flag=None, extra=()) -> dict:
    folder = root / "inv"
    folder.mkdir(parents=True)
    scripts = SCRIPTS if scripts is None else scripts
    records = [_record(n, js, folder) for n, js in scripts.items()]
    for entity, xml in diffs.items():
        files = [("ribbon-diff.xml", xml.encode(), True)]
        records.append(_component("ribbon_diff", {"entity": entity}, folder, files=files))
        # The effective ribbon holds the diff's actions: it links the diff to its libraries.
        files = [("ribbon.xml", xml.encode(), True)]
        records.append(_component("ribbon", {"entity": entity}, folder, files=files))
    # One effective ribbon calling every script puts them all in one work package.
    joined = _diff(_cmd("join", *(_call("j", library=n) for n in scripts)))
    files = [("ribbon.xml", joined.encode(), True)]
    records.append(_component("ribbon", {"entity": "join"}, folder, files=files))
    _write(folder, "crm2011", records, ("web_resource", "ribbon", "ribbon_diff"))
    target = _entity("account", "accounts", {"name": "String", "accountid": "Uniqueidentifier"})
    _write(root / "target", "onprem", target, ("entity", "attribute"))
    findings = root / "findings.json"
    result = assess.assess(folder, "online")
    findings.write_text(assess.dump_findings(result), encoding="utf-8")
    repo = root / "repo"
    repo.mkdir()
    built = build_packages(folder, map_source(folder, repo), None, assess.read_findings(findings))
    write_packages(built, root / "packages")
    args = ["--json", "inventory", "convert", str(folder), "--findings", str(findings)]
    args += ["--packages", str(root / "packages"), "--target-inventory", str(root / "target")]
    args += ["--out", str(root / "out"), *extra]
    if flag is not None:
        args += ["--adapter-resource", flag]
    res = CliRunner().invoke(cli, args)
    ledger = None
    if (root / "out" / "ledger.json").exists():
        ledger = json.loads((root / "out" / "ledger.json").read_text(encoding="utf-8"))
    return {"res": res, "out": root / "out", "ledger": ledger, "assess": result, "folder": folder}


def _entries(run: dict, entity: str, rule: str = RULE) -> list[dict]:
    return [
        e
        for e in run["ledger"]["entries"]
        if e["kind"] == "ribbon_diff" and e["key"] == entity and e["rule"] == rule
    ]


def _patched(run: dict, entry: dict) -> str:
    return (run["out"] / entry["files"]["patched"]).read_text(encoding="utf-8")


def _finding(run: dict, entity: str) -> list:
    return [
        f
        for f in run["assess"].findings
        if f.kind == "ribbon_diff" and f.key == entity and f.rule == RULE
    ]


PLAIN = _diff(
    _cmd("c1", _call("f", library=CLEAN), _call("a", library=ACCT), _call("o", library=OTHER)),
    _cmd("c2", _call("a", library=ACCT)),
)
DIFFS = {
    "account": PLAIN,
    "ahead": _diff(_cmd("c1", LOAD, _call("a", library=ACCT))),
    "late": _diff(_cmd("c1", _call("a", library=ACCT), LOAD)),
    "real": _diff(_cmd("c1", _call("x", library=REAL), _call("a", library=ACCT))),
    "kept": _diff(_cmd("c1", _call("b", library=BAD))),
    "clean": _diff(_cmd("c1", _call("f", library=CLEAN))),
    "both": _diff(_cmd("c1", _call("run", library=CTX))),
    "rules": _diff(
        _cmd("c1", _call("a", library=ACCT), enable=("r1",)),
        rules=_rule("r1", ACCT, "isOn"),
    ),
    "ruleonly": _diff(
        _cmd("c9", _call("f", library=CLEAN), enable=("r9",)), rules=_rule("r9", OTHER)
    ),
    "gone": _diff(_cmd("c1", _call("a", library=ACCT), _call("g", library="new_/scripts/gone.js"))),
}


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    out = _pipeline(tmp_path_factory.mktemp("ribbon"), DIFFS, flag=ADAPTER)
    assert out["res"].exit_code == 0, out["res"].output
    bare = _pipeline(tmp_path_factory.mktemp("bare"), DIFFS)
    assert bare["res"].exit_code == 0, bare["res"].output
    out["bare"] = bare
    return out


def test_the_rule_flags_a_command_calling_a_library_with_an_odata_v2_call(run):
    for entity in ("account", "late", "kept", "rules", "gone"):
        (hit,) = [f for f in _finding(run, entity) if f.cls != "unknown"]
        assert isinstance(hit.evidence, assess.RegistrationEvidence)
        assert hit.evidence.registrations[0]["command"].startswith(("c", "r"))


def test_a_command_with_the_adapter_library_first_or_no_such_library_is_not_flagged(run):
    for entity in ("real", "clean"):
        assert [f for f in _finding(run, entity) if f.cls != "unknown"] == []


def test_a_library_that_cannot_be_read_is_unknown_not_clear(run):
    unknown = [f for f in _finding(run, "gone") if f.cls == "unknown"]
    assert unknown and "gone.js" in unknown[0].reason


def test_a_custom_rule_calling_such_a_library_is_flagged_by_rule_id(run):
    for entity, rule in (("rules", "r1"), ("ruleonly", "r9")):
        hits = [f for f in _finding(run, entity) if f.cls != "unknown"]
        assert [r["rule"] for h in hits for r in h.evidence.registrations if "rule" in r] == [rule]


def test_convert_inserts_one_adapter_action_before_the_first_patched_action_of_each_command(run):
    (entry,) = _entries(run, "account")
    assert entry["status"] == "patched" and entry["manual_review"] == []
    patched = _patched(run, entry)
    assert patched == PLAIN.replace(_call("a", library=ACCT), LOAD + _call("a", library=ACCT))
    assert patched.count(LOAD) == 2 and entry["edits"] == 2
    assert patched.count("\n") == PLAIN.count("\n")


def test_the_patched_diff_re_assessed_with_the_adapter_uploaded_clears_the_finding(run, tmp_path):
    (entry,) = _entries(run, "account")
    package = entry["package"]
    adapter = (run["out"] / package / "crm-odata-v2-adapter.js").read_text(encoding="utf-8")
    again = _pipeline(
        tmp_path,
        {"account": _patched(run, entry)},
        scripts={
            CLEAN: SCRIPTS[CLEAN],
            ACCT: SCRIPTS[ACCT],
            OTHER: SCRIPTS[OTHER],
            ADAPTER: adapter,
        },
        flag=ADAPTER,
    )
    assert [f for f in _finding(again, "account") if f.cls != "unknown"] == []
    assert _entries(again, "account") == []


def test_a_command_with_the_adapter_action_ahead_by_name_is_left_alone(run):
    assert _entries(run, "ahead") == []


def test_a_command_with_the_adapter_action_after_the_patched_one_goes_to_manual_review(run):
    (entry,) = _entries(run, "late")
    assert entry["status"] == "manual-review" and "patched" not in entry["files"]
    assert "after" in entry["manual_review"][0]["reason"]


def test_a_command_whose_library_stayed_in_manual_review_gets_no_adapter(run):
    (entry,) = _entries(run, "kept")
    assert entry["status"] == "manual-review" and "patched" not in entry["files"]
    assert "rewritten in this run" in entry["manual_review"][0]["reason"]


def test_a_command_with_both_findings_gets_one_patch_with_both_edits(run):
    (adapter,) = _entries(run, "both")
    (handler,) = _entries(run, "both", HANDLER_RULE)
    assert adapter["status"] == handler["status"] == "patched"
    assert adapter["files"] == handler["files"]
    patched = _patched(run, adapter)
    assert patched.count(LOAD) == 1 and 'Value="PrimaryControl"' in patched
    assert patched.index(LOAD) < patched.index('FunctionName="run"')


def test_without_the_flag_no_diff_changes_and_each_names_the_flag(run):
    bare = run["bare"]
    for entity in ("account", "both"):
        (entry,) = _entries(bare, entity)
        assert entry["status"] == "manual-review" and "patched" not in entry["files"]
        assert "--adapter-resource" in entry["manual_review"][0]["reason"]


def test_a_custom_rule_caller_is_manual_review_naming_the_rule_and_command_and_is_not_edited(run):
    (entry,) = _entries(run, "rules")
    assert entry["status"] == "patched"
    (review,) = entry["manual_review"]
    assert "r1" in review["reason"] and "c1" in review["reason"] and "isOn" in review["reason"]
    patched = _patched(run, entry)
    assert patched.count(LOAD) == 1
    assert _rule("r1", ACCT, "isOn") in patched


def test_a_diff_with_only_a_rule_caller_is_manual_review_with_no_patch(run):
    (entry,) = _entries(run, "ruleonly")
    assert entry["status"] == "manual-review" and "patched" not in entry["files"]
    (review,) = entry["manual_review"]
    assert "r9" in review["reason"] and "c9" in review["reason"]


def test_the_ledger_records_the_adapter_web_resource_name(run):
    adapters = run["ledger"]["adapters"]
    assert adapters and {a["web_resource"] for a in adapters.values()} == {ADAPTER}


def test_two_runs_give_byte_identical_patches(run, tmp_path):
    again = _pipeline(tmp_path, {"account": PLAIN}, flag=ADAPTER)
    (first,) = _entries(run, "account")
    (second,) = _entries(again, "account")
    assert _patched(run, first) == _patched(again, second)


def _packages(monkeypatch, *packages: str, libraries: bool = False) -> None:
    from crm.core import inventory_convert as convert

    real = convert._members

    def members(*args):
        held = real(*args)
        held["ribbon_diff:account"] = list(packages)
        for member in held:
            if libraries and member.startswith("web_resource:"):
                held[member] = list(packages)
        return held

    monkeypatch.setattr(convert, "_members", members)


def test_a_diff_in_other_work_packages_than_its_libraries_is_manual_review(tmp_path, monkeypatch):
    _packages(monkeypatch, "elsewhere")
    got = _pipeline(tmp_path, {"account": PLAIN}, flag=ADAPTER)
    (entry,) = _entries(got, "account")
    assert entry["status"] == "manual-review" and "patched" not in entry["files"]
    assert "work packages" in entry["manual_review"][0]["reason"]


def test_a_package_placeholder_with_a_diff_in_several_packages_is_manual_review(
    tmp_path, monkeypatch
):
    _packages(monkeypatch, "one", "two", libraries=True)
    got = _pipeline(tmp_path, {"account": PLAIN}, flag="new_/{package}/adapter.js")
    entries = _entries(got, "account")
    assert entries and all(e["status"] == "manual-review" for e in entries)
    assert "several work packages" in entries[0]["manual_review"][0]["reason"]


def test_a_patch_the_gate_rejects_leaves_the_diff_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "crm.core.inventory_convert.ribbon_adapter_gate", lambda *a: "synthetic failure"
    )
    got = _pipeline(tmp_path, {"account": PLAIN}, flag=ADAPTER)
    (entry,) = _entries(got, "account")
    assert entry["status"] == "manual-review" and "patched" not in entry["files"]
    assert "emit gate rejected" in entry["manual_review"][0]["reason"]


def test_a_diff_whose_actions_cannot_be_mapped_is_manual_review(tmp_path):
    xml = PLAIN.replace(
        "<CommandDefinitions>", "<CommandDefinitions><!-- <JavaScriptFunction /> -->"
    )
    got = _pipeline(tmp_path, {"account": xml}, flag=ADAPTER)
    entries = _entries(got, "account")
    assert entries and all(e["status"] == "manual-review" for e in entries)


def test_an_actions_block_the_action_is_not_a_direct_child_of_is_not_placed():
    text = (
        b'<RibbonDiffXml><CommandDefinitions><CommandDefinition Id="c1"><Actions><Group>'
        b'<JavaScriptFunction Library="$webresource:a.js" FunctionName="f" />'
        b"</Group></Actions></CommandDefinition></CommandDefinitions></RibbonDiffXml>"
    )
    diff = read_ribbon_diff(text)
    assert diff is not None
    plan = plan_ribbon_adapter(diff, "ad.js", {"a.js"})
    assert plan.edits == () and "cannot be placed" in plan.unproven[0].reason


def test_the_generated_adapter_exports_the_no_op_the_inserted_action_calls():
    text = render_adapter(set())
    assert re.search(r"global\.CrmODataV2 = \{[^}]*\bload: function \(\) \{\}", text, re.S)
    assert "open: function (xhr, method, url)" in text
