"""`crm inventory convert`: formContext threads from a handler to the helpers it calls (ADR 0034).

Each case runs a library script and a form registering `onLoad` through `assess`, `packages` and
`convert`, then reads the patched script, the manual-review entries and the ledger's per-package
call-graph counts. A helper changes only when every use of its name is a call the graph resolves.
"""

# pyright: basic

from __future__ import annotations

import json

import pytest

from crm.core.code_parse import parse
from crm.core.inventory_transforms import _plan_handler_context
from crm.tests.test_inventory_convert_handlers import (
    LIB,
    _event,
    _handler,
    _js,
    _pipeline,
    _text,
    _xml,
)

FORM = {"Main": _xml(_event("onload", _handler("onLoad")))}
OTHER = "new_/scripts/other.js"
HEAD = "function onLoad(executionContext) { var formContext = executionContext.getFormContext();\n"


def _run(tmp_path, js: str, scripts: dict[str, str] | None = None):
    run = _pipeline(tmp_path, {LIB: js, **(scripts or {})}, FORM)
    entry = _js(run)
    graph = json.loads((run["out"] / "ledger.json").read_text(encoding="utf-8"))["call_graph"]
    return run, entry, graph[entry["package"]]


def _reasons(entry: dict) -> list[str]:
    return [m["reason"] for m in entry["manual_review"]]


def test_one_and_two_hop_helpers_take_formcontext_and_every_call_passes_it(tmp_path):
    js = (
        "function onLoad() {\n"
        "  mid(1);\n"
        "  leaf();\n"
        "}\n"
        "function mid(n) {\n"
        "  leaf();\n"
        "}\n"
        "function leaf() {\n"
        "  return Xrm.Page.getAttribute('name');\n"
        "}\n"
    )
    run, entry, counts = _run(tmp_path, js)
    assert entry["status"] == "patched"
    assert _text(run, entry) == (
        HEAD + "  mid(formContext, 1);\n"
        "  leaf(formContext);\n"
        "}\n"
        "function mid(formContext, n) {\n"
        "  leaf(formContext);\n"
        "}\n"
        "function leaf(formContext) {\n"
        "  return formContext.getAttribute('name');\n"
        "}\n"
    )
    assert counts == {"threaded": 2, "unreachable": 0, "boundary": 0}


def test_a_helper_two_handlers_call_is_threaded_for_both(tmp_path):
    js = (
        "function onLoad() {\n  leaf();\n}\n"
        "function onSave() {\n  leaf();\n}\n"
        "function leaf() {\n  return Xrm.Page.data;\n}\n"
    )
    form = _xml(_event("onload", _handler("onLoad")), _event("onsave", _handler("onSave")))
    run = _pipeline(tmp_path, {LIB: js}, {"Main": form})
    text = _text(run, _js(run))
    assert text.count("leaf(formContext)") == 3  # two calls and the definition
    assert "return formContext.data;" in text


def test_a_helper_another_web_resource_mentions_stays_whole_and_counts_as_boundary(tmp_path):
    js = (
        "function onLoad() {\n  Xrm.Page.ui;\n  leaf();\n}\n"
        "function leaf() {\n  return Xrm.Page.data;\n}\n"
    )
    run, entry, counts = _run(tmp_path, js, {OTHER: "function other() { leaf(); }\n"})
    text = _text(run, entry)
    assert "  leaf();\n" in text and "function leaf() {\n  return Xrm.Page.data;\n}" in text
    assert any("named outside this file" in r for r in _reasons(entry))
    assert counts == {"threaded": 0, "unreachable": 0, "boundary": 1}


def test_code_no_handler_reaches_is_manual_review_and_unchanged(tmp_path):
    js = (
        "function onLoad() {\n  return Xrm.Page.data;\n}\n"
        "function orphan() {\n  return Xrm.Page.ui;\n}\n"
        "var top = Xrm.Page.data;\n"
    )
    run, entry, counts = _run(tmp_path, js)
    text = _text(run, entry)
    assert "function orphan() {\n  return Xrm.Page.ui;\n}" in text
    assert "var top = Xrm.Page.data;" in text
    reasons = _reasons(entry)
    assert any("no registered handler reaches orphan" in r for r in reasons)
    assert any("outside any named function" in r for r in reasons)
    assert counts == {"threaded": 0, "unreachable": 2, "boundary": 0}


@pytest.mark.parametrize(
    ("name", "extra", "reason"),
    [
        ("callback", "", "nested function"),
        ("recursion", "", "recursive"),
        ("value", "", "other than as a direct call"),
        ("twice", "function leaf() {}\n", "defined more than once"),
        ("optional", "", "optional chain"),
        ("stranger", "function stranger() {\n  leaf();\n}\n", "gets none"),
        ("toplevel", "leaf();\n", "outside any function"),
    ],
)
def test_an_unresolvable_use_leaves_the_helper_and_its_callers_whole(tmp_path, name, extra, reason):
    calls = {
        "callback": "  setTimeout(function () { leaf(); }, 1);\n",
        "recursion": "  leaf();\n",
        "value": "  var f = leaf;\n  f();\n",
        "twice": "  leaf();\n",
        "optional": "  leaf?.();\n",
        "stranger": "",
        "toplevel": "",
    }[name]
    body = "  if (n) { leaf(); }\n" if name == "recursion" else ""
    helper = f"function leaf(n) {{\n{body}  return Xrm.Page.data;\n}}\n"
    if name == "stranger":
        calls = "  stranger();\n"
    js = f"function onLoad() {{\n  Xrm.Page.ui;\n{calls}}}\n{helper}{extra}"
    if name == "stranger":
        js += "stranger();\n"
    run, entry, counts = _run(tmp_path, js)
    text = _text(run, entry)
    assert "function leaf(n) {" in text and "leaf(formContext" not in text
    assert counts["threaded"] == 0
    assert any(reason in r for r in _reasons(entry)), _reasons(entry)


def test_a_helper_with_an_unpatched_handler_caller_stays_whole(tmp_path):
    js = (
        "function onLoad() {\n  Xrm.Page.ui;\n}\n"
        "async function onSave() {\n  leaf();\n}\n"
        "function leaf() {\n  return Xrm.Page.data;\n}\n"
    )
    form = _xml(_event("onload", _handler("onLoad")), _event("onsave", _handler("onSave")))
    run = _pipeline(tmp_path, {LIB: js}, {"Main": form})
    text = _text(run, _js(run))
    assert "function leaf() {\n  return Xrm.Page.data;\n}" in text


def test_a_helper_a_form_also_registers_stays_whole(tmp_path):
    js = (
        "function onLoad() {\n  Xrm.Page.ui;\n  leaf();\n}\n"
        "function leaf() {\n  return Xrm.Page.data;\n}\n"
    )
    form = _xml(
        _event("onload", _handler("onLoad")),
        _event("onsave", _handler("leaf", passExecutionContext="true")),
    )
    run = _pipeline(tmp_path, {LIB: js}, {"Main": form})
    entry = _js(run)
    text = _text(run, entry)
    assert "  leaf();\n" in text and "function leaf() {\n  return Xrm.Page.data;\n}" in text
    assert any("named outside this file" in r for r in _reasons(entry))


def test_an_unreadable_neighbour_script_keeps_every_helper_whole():
    js = b"function onLoad() {\n  leaf();\n}\nfunction leaf() {\n  return Xrm.Page.data;\n}\n"
    plan = _plan_handler_context(parse("javascript", js), {"onLoad": False}, None)
    assert plan.edits == () and plan.boundary == 1
