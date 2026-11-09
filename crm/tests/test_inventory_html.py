"""The HTML script reader of `crm inventory convert` (ADR 0034, #1182).

A web resource page is read with the standard library's `html.parser`; each script tag's byte
range is checked against a scan of the same text, so a page the two disagree on is refused rather
than guessed at.
"""

# pyright: basic

from __future__ import annotations

import re

import pytest

from crm.core.inventory_html import (
    adapter_src,
    inline_errors,
    inline_hits,
    plan_inline,
    resolve_src,
    script_tags,
)
from crm.core.inventory_transforms import Edit, Plan, Unproven


def _only(text):
    tags = script_tags(text)
    assert tags is not None and len(tags) == 1
    return tags[0]


PAGE = (
    '<html>\n<head><script src="../scripts/a.js"></script>\n</head>\n<body>\n'
    "<script>\nvar x = 1;\n</script>\n</body></html>\n"
)


def test_script_tags_give_each_tag_its_byte_range_in_document_order():
    text = PAGE.encode()
    tags = script_tags(text)
    assert tags is not None and len(tags) == 2
    first, second = tags
    assert text[first.start : first.tag_end] == b'<script src="../scripts/a.js">'
    assert first.src == "../scripts/a.js" and first.line == 2
    assert text[second.body[0] : second.body[1]] == b"\nvar x = 1;\n"
    assert second.src is None and second.line == 5 and second.javascript


def test_byte_offsets_hold_after_non_ascii_text():
    text = "<title>café ✓</title>\n<script>var a;</script>".encode()
    tag = _only(text)
    assert text[tag.start : tag.start + 8] == b"<script>"
    assert text[tag.body[0] : tag.body[1]] == b"var a;"


def test_a_tag_name_and_attributes_are_read_case_insensitively():
    text = b'<SCRIPT SRC="A.JS" data-x="a>b" ></SCRIPT>'
    tag = _only(text)
    assert tag.src == "A.JS" and tag.tag_end == text.index(b"></SCRIPT>") + 1


@pytest.mark.parametrize(
    "markup",
    [
        '<!-- <script src="a.js"></script> -->',
        '<![CDATA[<script src="a.js"></script>]]>',
        '<script>document.write("<script src=x></script>");</script>',
        '<script src="a.js" />',
        "<script>var a;",
        b"<script>\xff</script>",
        "<script-x>a</script-x><script>b</script>",
        '<div title="<script>">x</div><script>a</script>',
    ],
)
def test_a_page_the_parser_and_the_scan_disagree_on_is_refused(markup):
    text = markup if isinstance(markup, bytes) else markup.encode()
    assert script_tags(text) is None


def test_a_near_miss_tag_name_is_no_script_and_a_spaced_close_tag_is_one():
    assert script_tags(b"<scripts>a</scripts><p>b</p>") == []
    tag = _only(b"<script>a</script\n>")
    assert tag.body == (8, 9)


@pytest.mark.parametrize(
    ("attrs", "javascript"),
    [
        ("", True),
        (' type="text/javascript"', True),
        (' type="Application/JavaScript"', True),
        (' type="text/template"', False),
        (' type="module"', False),
    ],
)
def test_only_a_classic_script_is_javascript(attrs, javascript):
    tag = _only(f"<script{attrs}>x</script>".encode())
    assert tag.javascript is javascript


@pytest.mark.parametrize(
    ("page", "src", "name"),
    [
        ("new_/html/p.htm", "../scripts/x.js", "new_/scripts/x.js"),
        ("new_/html/p.htm", "x.js", "new_/html/x.js"),
        ("new_/html/p.htm", "./sub/../x.js?v=2#top", "new_/html/x.js"),
        ("new_/html/p.htm", "/WebResources/new_/scripts/x.js", "new_/scripts/x.js"),
        ("new_/html/p.htm", "/webresources/New_/x.js", "New_/x.js"),
        ("new_page.htm", "new_x.js", "new_x.js"),
        ("new_/html/p.htm", "../../../x.js", None),
        ("new_/html/p.htm", "https://cdn.example.com/x.js", None),
        ("new_/html/p.htm", "//cdn.example.com/x.js", None),
        ("new_/html/p.htm", "data:text/javascript,1", None),
        ("new_/html/p.htm", "/other/x.js", "other/x.js"),
        ("new_/html/p.htm", "", None),
        ("new_/html/p.htm", "  ", None),
    ],
)
def test_a_src_resolves_against_the_pages_own_web_resource_name(page, src, name):
    assert resolve_src(page, src) == name


def test_adapter_src_is_the_adapters_name_relative_to_the_pages_folder():
    assert adapter_src("new_/html/p.htm", "new_/scripts/a.js") == "../scripts/a.js"
    assert adapter_src("new_/html/p.htm", "new_/html/a.js") == "a.js"
    assert adapter_src("new_page.htm", "new_a.js") == "new_a.js"
    assert resolve_src("new_/html/p.htm", adapter_src("new_/html/p.htm", "new_/scripts/a.js")) == (
        "new_/scripts/a.js"
    )


def _mark(root):
    """A planner for the tests: one edit per string literal, one unproven site per number."""
    edits, unproven = [], []
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.children)
        if node.type == "string":
            edits.append(Edit(node.start_byte, node.end_byte, b'"X"'))
        if node.type == "number":
            unproven.append(Unproven(node.start_point.row + 1, "a number"))
    edits.sort(key=lambda e: e.start)
    return Plan(tuple(edits), tuple(unproven), cleared=len(edits))


def test_plan_inline_shifts_each_scripts_edits_and_lines_into_the_page():
    text = (
        b'<p>"no"</p>\n<script src="a.js">"ignored"</script>\n<script>\nvar a = "one";\n7;\n'
        b'</script>\n<script>var b = "two";</script>\n'
    )
    plan = plan_inline(text, "javascript", _mark)
    assert isinstance(plan, Plan)
    assert [text[e.start : e.end] for e in plan.edits] == [b'"one"', b'"two"']
    assert [(u.line, u.reason) for u in plan.unproven] == [(5, "a number")]
    assert plan.cleared == 2


def test_plan_inline_leaves_a_script_that_is_not_javascript_alone():
    text = b'<script type="text/template">var a = "x";</script>'
    plan = plan_inline(text, "javascript", _mark)
    assert isinstance(plan, Plan) and plan.edits == ()


def test_plan_inline_names_why_a_page_cannot_be_read():
    plan = plan_inline(b"<!-- <script></script> -->", "javascript", _mark)
    assert isinstance(plan, str) and "<script>" in plan


def test_inline_errors_count_syntax_errors_in_each_script_body_only():
    ok = b"<p>if (</p><script>var a = 1;</script>"
    bad = b"<p>if (</p><script>function (</script>"
    assert inline_errors(ok, "javascript") == 0
    assert (inline_errors(bad, "javascript") or 0) > 0
    assert inline_errors(b"<!-- <script></script> -->", "javascript") is None


def test_inline_hits_are_the_strings_in_inline_scripts_holding_the_pattern():
    pattern = re.compile(r"svc/")
    text = (
        b'<!-- svc/ -->\n<p title="svc/">svc/</p>\n<script>\n// svc/\nvar a = "svc/x";\n</script>\n'
        b'<script src="svc/y.js"></script>\n<script type="text/template">"svc/"</script>\n'
    )
    tags = script_tags(text)
    assert tags is not None
    assert inline_hits(text, tags, pattern) == {0: 5}
