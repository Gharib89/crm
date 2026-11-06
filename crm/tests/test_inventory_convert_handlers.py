"""`crm inventory convert`: form handlers get the execution context, in one work package (ADR 0034).

Each case is a library script plus a form registering its functions. It runs through `assess`,
`packages` and `convert` once, then asserts what landed under `--out`: the patched script, the
patched form XML and the manual-review entries.
"""

# pyright: basic

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory_assess as assess
from crm.core.inventory_form_handlers import form_handler_names, form_handler_tags
from crm.core.inventory_packages import build_packages, write_packages
from crm.core.inventory_source_map import dump_source_map, map_source, read_source_map
from crm.tests.test_inventory_assess import _form, _registrations, _script

LIB = "new_/scripts/acct.js"
FORM_RULE = "form-handler-no-execution-context"
GC = "Xrm.Utility.getGlobalContext()"


def _handler(function: str, *, library: str = LIB, parameters: str | None = None, **attrs) -> str:
    extra = {"enabled": "true", **attrs}
    if parameters is not None:
        extra["parameters"] = parameters
    flags = "".join(f' {k}="{v}"' for k, v in extra.items())
    return f'<Handler functionName="{function}" libraryName="{library}"{flags} />'


def _event(name: str, *handlers: str) -> str:
    return f'<event name="{name}"><Handlers>{"".join(handlers)}</Handlers></event>'


def _xml(*events: str) -> str:
    return f"<form>\n<events>\n{chr(10).join(events)}\n</events>\n</form>\n"


def _pipeline(root: Path, scripts: dict[str, str], forms: dict[str, str]) -> dict:
    folder = root / "inv"
    folder.mkdir(parents=True)
    records = [_script(name, js, folder) for name, js in scripts.items()]
    records += [_form(name, xml.encode(), folder) for name, xml in forms.items()]
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


def _rule_entries(run: dict, kind: str, rule: str, name: str | None = None) -> list[dict]:
    keys = {r.key for r in run["records"] if r.kind == kind and name in (None, r.fields["name"])}
    return [
        e for e in run["entries"] if e["kind"] == kind and e["rule"] == rule and e["key"] in keys
    ]


def _js(run: dict) -> dict:
    (found,) = _rule_entries(run, "web_resource", FORM_RULE, LIB)
    return found


def _text(run: dict, entry: dict, label: str = "patched") -> str:
    return (run["out"] / entry["files"][label]).read_text(encoding="utf-8")


def _form_entry(run: dict, name: str = "Main") -> dict:
    (found,) = _rule_entries(run, "form", FORM_RULE, name)
    return found


BASIC_JS = (
    "function onLoad() {\n"
    "  var name = Xrm.Page.getAttribute('name');\n"
    "  // Xrm.Page.getAttribute stays in a comment\n"
    "  Xrm.Page.ui.setFormNotification(name, 'INFO');\n"
    "}\n"
)


def test_a_handler_body_and_its_form_xml_are_patched_in_one_work_package(tmp_path):
    xml = _xml(_event("onload", _handler("onLoad")))
    run = _pipeline(tmp_path, {LIB: BASIC_JS}, {"Main": xml})
    js, form = _js(run), _form_entry(run)
    assert js["status"] == "patched" and form["status"] == "patched"
    assert js["package"] == form["package"]
    assert _text(run, js) == (
        "function onLoad(executionContext) { var formContext = executionContext.getFormContext();\n"
        "  var name = formContext.getAttribute('name');\n"
        "  // Xrm.Page.getAttribute stays in a comment\n"
        "  formContext.ui.setFormNotification(name, 'INFO');\n"
        "}\n"
    )
    assert _text(run, form) == xml.replace(
        'enabled="true" />', 'enabled="true" passExecutionContext="true" />'
    )
    assert form["edits"] == 1 and form["manual_review"] == []


def test_re_assess_on_the_patched_files_clears_the_js_and_the_registration_finding(tmp_path):
    js = BASIC_JS.replace(
        "  // Xrm.Page.getAttribute stays in a comment\n", ""
    )  # the rule's regex reads comments
    run = _pipeline(tmp_path / "a", {LIB: js}, {"Main": _xml(_event("onload", _handler("onLoad")))})
    patched_js, patched_xml = _text(run, _js(run)), _text(run, _form_entry(run))
    again = _pipeline(tmp_path / "b", {LIB: patched_js}, {"Main": patched_xml})
    assert again["entries"] == []
    data = json.loads((tmp_path / "b" / "findings.json").read_text(encoding="utf-8"))
    mine = {r.key for r in again["records"]}
    flagged = {f["rule"] for f in data["findings"] if f["key"] in mine and f["class"] != "clear"}
    assert "web-resource-xrm-page" not in flagged and FORM_RULE not in flagged


def test_xrm_page_outside_a_handler_body_is_left_alone(tmp_path):
    js = BASIC_JS + "function helper() {\n  return Xrm.Page.getAttribute('other');\n}\n"
    run = _pipeline(tmp_path, {LIB: js}, {"Main": _xml(_event("onload", _handler("onLoad")))})
    text = _text(run, _js(run))
    assert "function helper() {\n  return Xrm.Page.getAttribute('other');\n}\n" in text
    assert text.count("Xrm.Page") == 2  # the comment and the helper


def test_positional_parameters_keep_their_order_after_the_context(tmp_path):
    js = "function onChange(a, b) {\n  return Xrm.Page.getAttribute(a) + b;\n}\n"
    xml = _xml(_event("onchange", _handler("onChange", parameters="'x',2")))
    run = _pipeline(tmp_path, {LIB: js}, {"Main": xml})
    assert _text(run, _js(run)).startswith(
        "function onChange(executionContext, a, b) { var formContext = "
    )
    assert 'parameters="\'x\',2" passExecutionContext="true"' in _text(run, _form_entry(run))


def test_an_existing_context_parameter_is_reused_not_doubled(tmp_path):
    js = "function onSave(ctx) {\n  return Xrm.Page.getAttribute('a');\n}\n"
    run = _pipeline(tmp_path, {LIB: js}, {"Main": _xml(_event("onsave", _handler("onSave")))})
    assert _text(run, _js(run)).startswith(
        "function onSave(ctx) { var formContext = ctx.getFormContext();\n"
    )
    assert _form_entry(run)["status"] == "patched"


def test_an_existing_context_parameter_with_positional_parameters_is_manual_review(tmp_path):
    js = "function onSave(ctx, x) {\n  return Xrm.Page.getAttribute(x);\n}\n"
    xml = _xml(_event("onsave", _handler("onSave", parameters="1")))
    run = _pipeline(tmp_path, {LIB: js}, {"Main": xml})
    assert _js(run)["status"] == "manual-review" and _js(run)["files"] == {}
    assert _form_entry(run)["status"] == "manual-review" and _form_entry(run)["files"] == {}
    assert "ctx" in _js(run)["manual_review"][0]["reason"]


def test_a_handler_shared_by_several_forms_is_patched_on_every_form(tmp_path):
    xml = _xml(_event("onload", _handler("onLoad")))
    run = _pipeline(tmp_path, {LIB: BASIC_JS}, {"Main": xml, "Quick": xml})
    forms = [_form_entry(run, "Main"), _form_entry(run, "Quick")]
    assert [f["status"] for f in forms] == ["patched", "patched"]
    assert _js(run)["status"] == "patched"
    for form in forms:
        assert 'passExecutionContext="true"' in _text(run, form)


def test_an_already_passing_registration_of_the_same_function_makes_it_manual_review(tmp_path):
    main = _xml(_event("onload", _handler("onLoad")))
    quick = _xml(_event("onload", _handler("onLoad", passExecutionContext="true")))
    run = _pipeline(tmp_path, {LIB: BASIC_JS}, {"Main": main, "Quick": quick})
    assert not list(run["out"].rglob("*.patched*"))
    assert _form_entry(run, "Main")["status"] == "manual-review"
    assert "onLoad" in _form_entry(run, "Main")["manual_review"][0]["reason"]


def test_a_disabled_handler_is_not_patched_and_does_not_block_the_function(tmp_path):
    xml = _xml(
        _event("onload", _handler("onLoad"), _handler("onLoad", enabled="false")),
    )
    run = _pipeline(tmp_path, {LIB: BASIC_JS}, {"Main": xml})
    text = _text(run, _form_entry(run))
    assert text.count('passExecutionContext="true"') == 1
    assert 'enabled="false" />' in text


def test_a_dotted_function_assigned_in_the_library_is_converted(tmp_path):
    js = (
        "var Acct = {};\n"
        "Acct.onLoad = function (e) {\n  return Xrm.Page.getAttribute('a');\n};\n"
        "var onSave = function () {\n  return Xrm.Page.getAttribute('b');\n};\n"
    )
    xml = _xml(_event("onload", _handler("Acct.onLoad"), _handler("onSave")))
    run = _pipeline(tmp_path, {LIB: js}, {"Main": xml})
    text = _text(run, _js(run))
    assert "Acct.onLoad = function (executionContext, e) { var formContext =" in text
    assert "var onSave = function (executionContext) { var formContext =" in text


def test_a_shadowed_or_qualified_xrm_stays_manual_review_and_blocks_the_form_flag(tmp_path):
    js = (
        "function onLoad() {\n"
        "  var x = window.Xrm.Page.getAttribute('a');\n"
        "  var y = Xrm.Page?.getAttribute('b');\n"
        "  var z = Xrm.Page;\n"
        "}\n"
    )
    run = _pipeline(tmp_path, {LIB: js}, {"Main": _xml(_event("onload", _handler("onLoad")))})
    assert _js(run)["status"] == "manual-review"
    assert len(_js(run)["manual_review"]) == 3
    assert _form_entry(run)["status"] == "manual-review" and _form_entry(run)["files"] == {}
    run = _pipeline(
        tmp_path / "s",
        {LIB: "var Xrm = window.parent.Xrm;\n" + BASIC_JS},
        {"Main": _xml(_event("onload", _handler("onLoad")))},
    )
    assert _js(run)["status"] == "manual-review"


def test_a_function_with_arguments_a_name_clash_or_a_second_reference_is_manual_review(tmp_path):
    cases = {
        "args": "function onLoad() {\n  var n = arguments.length;\n  return Xrm.Page.a;\n}\n",
        "clash": "function onLoad() {\n  var formContext = 1;\n  return Xrm.Page.a;\n}\n",
        "called": "function onLoad() {\n  return Xrm.Page.a;\n}\nonLoad();\n",
        "twice": "function onLoad() {\n  return Xrm.Page.a;\n}\nfunction onLoad() {}\n",
    }
    for case, js in cases.items():
        run = _pipeline(
            tmp_path / case, {LIB: js}, {"Main": _xml(_event("onload", _handler("onLoad")))}
        )
        assert _js(run)["status"] == "manual-review", case
        assert _form_entry(run)["status"] == "manual-review", case
        assert not list(run["out"].rglob("*.patched*")), case


def test_a_function_missing_from_the_library_is_manual_review_on_its_form(tmp_path):
    xml = _xml(_event("onload", _handler("onLoad"), _handler("gone")))
    run = _pipeline(tmp_path, {LIB: BASIC_JS}, {"Main": xml})
    form = _form_entry(run)
    assert form["status"] == "patched" and form["edits"] == 1
    (review,) = form["manual_review"]
    assert "gone" in review["reason"] and review["line"] == 3


def test_the_context_method_in_a_handler_still_goes_to_global_context_in_one_patch(tmp_path):
    js = (
        "function onLoad() {\n"
        "  var u = Xrm.Page.context.getClientUrl();\n"
        "  return Xrm.Page.getAttribute('a') + u;\n"
        "}\n"
    )
    run = _pipeline(tmp_path, {LIB: js}, {"Main": _xml(_event("onload", _handler("onLoad")))})
    patched = [
        e for e in run["entries"] if e["kind"] == "web_resource" and e["status"] == "patched"
    ]
    assert {e["rule"] for e in patched} == {"web-resource-xrm-page", FORM_RULE}
    text = _text(run, patched[0])
    assert f"{GC}.getClientUrl()" in text and "formContext.getAttribute('a')" in text
    assert "Xrm.Page" not in text
    assert len({e["files"]["patched"] for e in patched}) == 1


def test_xrm_page_in_a_nested_callback_or_an_async_handler_is_manual_review(tmp_path):
    nested = (
        "function onLoad() {\n  setTimeout(function () {\n    Xrm.Page.getAttribute('a');\n"
        "  }, 1);\n  Xrm.Page.ui.x();\n}\n"
    )
    run = _pipeline(
        tmp_path / "n", {LIB: nested}, {"Main": _xml(_event("onload", _handler("onLoad")))}
    )
    assert [m["line"] for m in _js(run)["manual_review"]] == [3]
    assert "formContext.ui.x()" in _text(run, _js(run))
    asynced = "async function onLoad() {\n  Xrm.Page.getAttribute('a');\n}\n"
    run = _pipeline(
        tmp_path / "a", {LIB: asynced}, {"Main": _xml(_event("onload", _handler("onLoad")))}
    )
    assert (
        _js(run)["status"] == "manual-review" and "async" in _js(run)["manual_review"][0]["reason"]
    )


def test_xrm_page_in_a_string_stays_in_a_converted_handler(tmp_path):
    js = "function onLoad() {\n  var s = 'Xrm.Page.x';\n  return Xrm.Page.getAttribute(s);\n}\n"
    run = _pipeline(tmp_path, {LIB: js}, {"Main": _xml(_event("onload", _handler("onLoad")))})
    assert "var s = 'Xrm.Page.x';" in _text(run, _js(run))
    assert "return formContext.getAttribute(s);" in _text(run, _js(run))


def test_a_handler_body_without_xrm_page_is_left_alone(tmp_path):
    js = "function onLoad() {\n  return 1;\n}\nfunction other() {\n  return Xrm.Page.a;\n}\n"
    xml = _xml(_event("onload", _handler("onLoad"), _handler("other")))
    run = _pipeline(tmp_path, {LIB: js}, {"Main": xml})
    text = _text(run, _form_entry(run))
    assert text.count('passExecutionContext="true"') == 1
    assert 'functionName="onLoad" libraryName="new_/scripts/acct.js" enabled="true" />' in text


def _registered(key: str, *, target: bool = True):
    from crm.core.inventory_convert import _Form
    from crm.core.inventory_form_handlers import HandlerTag

    tag = HandlerTag("onLoad", LIB, True, False, "", 1, None, 0)
    return _Form(key, f"{key}.xml", b"", [tag], "", target, [(LIB.lower(), "onLoad")])


def test_a_function_is_converted_only_when_its_forms_and_library_share_exactly_the_same_packages():
    from crm.core.inventory_convert import _eligible

    libs = {LIB.lower(): ("webresource", "lib", "x")}
    key = (LIB.lower(), "onLoad")

    def run(lib_pkgs: set[str], **form_pkgs: set[str]):
        forms = [_registered(k) for k in form_pkgs]
        return _eligible(forms, libs, lambda kind, k: lib_pkgs if kind != "form" else form_pkgs[k])

    ok, none = run({"A"}, Main={"A"})
    assert ok and not none
    for lib_pkgs, forms in (
        ({"A", "B"}, {"Main": {"A"}}),
        ({"A"}, {"Main": {"A"}, "Quick": {"B"}}),
    ):
        ok, why = run(lib_pkgs, **forms)
        assert not ok and "exactly the work packages" in why[key]


def test_a_parameter_default_is_manual_review_and_a_directive_prologue_stays_first(tmp_path):
    js = (
        "function onLoad(a = Xrm.Page.getAttribute('n')) {\n  Xrm.Page.ui.x();\n}\n"
        "function onSave() {\n  // note\n  'use strict'\n  Xrm.Page.ui.y();\n}\n"
    )
    forms = {"Main": _xml(_event("onload", _handler("onLoad"), _handler("onSave")))}
    run = _pipeline(tmp_path, {LIB: js}, forms)
    text = _text(run, _js(run))
    assert "a = Xrm.Page.getAttribute('n')" in text
    assert "function onSave(executionContext) {\n  // note\n  'use strict'; var formContext" in text
    assert [m["line"] for m in _js(run)["manual_review"]] == [1]


def test_xml_character_references_and_a_multiline_flag_are_read_like_the_assessor_does():
    ref = b'<f><event><Handlers><Handler functionName="a" libraryName="l" enabled="tr&#117;e"/>'
    tags = form_handler_tags(ref + b"</Handlers></event></f>")
    assert tags is not None and tags[0].enabled
    flag = b'<Handler functionName="a" libraryName="l" passExecutionContext="\nfalse\n"/>'
    assert form_handler_tags(b"<f><event><Handlers>" + flag + b"</Handlers></event></f>") is None


def test_the_functions_a_form_that_cannot_be_mapped_still_registers_are_named():
    handler = b'<Handler functionName="a" libraryName="L"/>'
    text = b"<f><x><InternalHandlers>" + handler + b"</InternalHandlers></x></f>"
    assert form_handler_tags(text) is None
    assert form_handler_names(text) == [("l", "a")]
