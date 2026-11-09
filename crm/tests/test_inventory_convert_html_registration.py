"""`crm inventory convert`: a page loads the OData v2 adapter ahead of patched scripts (#1182).

Each case is a library script plus a web resource page (type 1) that loads it with `<script src>`
or calls the OData v2 endpoint inline. Everything runs through `assess`, `packages` and the
`convert` CLI, then asserts what landed under `--out`: the patched page, the ledger's page entries
and the adapter's recorded web resource name.
"""

# pyright: basic

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory_assess as assess
from crm.core.inventory_packages import build_packages, write_packages
from crm.core.inventory_source_map import map_source
from crm.tests.test_inventory_convert import _record
from crm.tests.test_inventory_convert_odata import SVC, _entity, _write

RULE = "web-resource-page-no-odata-adapter"
ODATA_RULE = "web-resource-odata-v2"
ADAPTER = "new_/scripts/crm-odata-v2-adapter.js"
ADAPTER_TAG = '<script src="../scripts/crm-odata-v2-adapter.js"></script>'

ACCT, BAD, CLEAN, REAL = (f"new_/scripts/{n}.js" for n in ("acct", "bad", "clean", "real-adapter"))
CALL = f'req.open("GET", "{SVC}/AccountSet?$select=Name", false);\n'
READ = f"var req = new XMLHttpRequest();\n{CALL}req.send();\n"
SCRIPTS = {
    ACCT: READ,
    BAD: f'var req = new XMLHttpRequest();\nreq.open("POST", "{SVC}/AccountSet", false);\n',
    CLEAN: "function f(ctx) {\n  return ctx.getFormContext();\n}\n",
    REAL: "(function (global) {\n  global.CrmODataV2 = {};\n})(window);\n",
}
INLINE = f"<script>\n{READ}</script>"


def _tag(library: str) -> str:
    return f'<script src="../{library.removeprefix("new_/")}"></script>'


def _page(*parts: str, lead: bool = True) -> str:
    # every page loads the clean library, so the packages join them into one work package
    body = "\n".join(([_tag(CLEAN)] if lead else []) + list(parts))
    return f"<html>\n<head>\n{body}\n</head>\n<body></body>\n</html>\n"


MIXED = f'<script>\n{READ}var u = "{SVC}/ContactSet";\n</script>'
SHAPE = f'<script>\nvar u = "{SVC}/AccountSet";\n</script>'
PROSE = (
    f'<!-- {SVC}/AccountSet -->\n<p>{SVC}/AccountSet</p>\n<a href="{SVC}/AccountSet">x</a>\n'
    f"<script>\n// {SVC}/AccountSet\nvar a = 1;\n</script>"
)

PAGES = {
    "Lib": _page(_tag(ACCT)),
    "Inline": _page(INLINE),
    "Both": _page(_tag(ACCT), INLINE),
    "InlineFirst": _page(INLINE, _tag(ACCT)),
    "Listed": _page(_tag(ADAPTER), _tag(ACCT)),
    "Late": _page(_tag(ACCT), _tag(ADAPTER)),
    "NoCall": _page(),
    "NameOnly": _page('<script src="acct.js"></script>'),
    "Prose": _page(PROSE),
    "Unreadable": _page(f"<!--\n{_tag(ACCT)}\n-->"),
    "Kept": _page(_tag(BAD)),
    "Shape": _page(SHAPE),
    "Mixed": _page(MIXED),
}


def _pipeline(root: Path, pages: dict[str, str], *, flag: str | None = None) -> dict:
    folder = root / "inv"
    folder.mkdir(parents=True)
    records = [_record(n, js, folder) for n, js in {**SCRIPTS, ADAPTER: SCRIPTS[REAL]}.items()]
    records += [
        _record(f"new_/html/{n}.htm", html, folder, code=1, ext="htm") for n, html in pages.items()
    ]
    _write(folder, "crm2011", records, ("web_resource",))
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
    args += ["--out", str(root / "out")]
    if flag is not None:
        args += ["--adapter-resource", flag]
    res = CliRunner().invoke(cli, args)
    ledger = None
    if (root / "out" / "ledger.json").exists():
        ledger = json.loads((root / "out" / "ledger.json").read_text(encoding="utf-8"))
    return {
        "res": res,
        "out": root / "out",
        "ledger": ledger,
        "assess": result,
        "folder": folder,
        "records": records,
    }


def _keys(run: dict, page: str) -> set[str]:
    return {r.key for r in run["records"] if r.fields["name"] == f"new_/html/{page}.htm"}


def _entries(run: dict, page: str, rule: str = RULE) -> list[dict]:
    keys = _keys(run, page)
    return [e for e in run["ledger"]["entries"] if e["key"] in keys and e["rule"] == rule]


def _patched(run: dict, entry: dict) -> str:
    return (run["out"] / entry["files"]["patched"]).read_text(encoding="utf-8")


def _patch_files(entry: dict) -> dict:
    """The entry's diff and patched page: the odata entry also lists the generated adapter."""
    return {k: v for k, v in entry["files"].items() if k != "adapter"}


def _finding(run: dict, page: str, rule: str = RULE) -> list:
    keys = _keys(run, page)
    return [f for f in run["assess"].findings if f.key in keys and f.rule == rule]


def _hits(run: dict, page: str) -> list:
    return [f for f in _finding(run, page) if f.cls != "unknown"]


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    out = _pipeline(tmp_path_factory.mktemp("html"), PAGES, flag=ADAPTER)
    assert out["res"].exit_code == 0, out["res"].output
    bare = _pipeline(tmp_path_factory.mktemp("bare"), PAGES)
    assert bare["res"].exit_code == 0, bare["res"].output
    out["bare"] = bare
    return out


def test_the_rule_flags_a_page_loading_a_library_with_an_odata_v2_call(run):
    for page in ("Lib", "Both", "InlineFirst", "Late", "Kept"):
        (hit,) = _hits(run, page)
        assert isinstance(hit.evidence, assess.RegistrationEvidence)
        assert (
            hit.evidence.registrations[0].keys() == {"library"}
            or "inline" in (hit.evidence.registrations[0])
        )


def test_the_rule_flags_a_page_with_an_inline_call(run):
    for page in ("Inline", "Shape", "Mixed"):
        (hit,) = _hits(run, page)
        assert "inline" in hit.evidence.registrations[0]


def test_a_page_that_loads_the_adapter_first_or_calls_nothing_is_not_flagged(run):
    for page in ("Listed", "NoCall", "NameOnly", "Prose"):
        assert _hits(run, page) == []


def test_a_page_whose_scripts_cannot_be_read_is_unknown_not_clear(run):
    (unknown,) = [f for f in _finding(run, "Unreadable") if f.cls == "unknown"]
    assert "<script>" in unknown.reason
    assert _hits(run, "Unreadable") == []


def test_convert_inserts_the_adapter_before_the_first_patched_library(run):
    (entry,) = _entries(run, "Lib")
    assert entry["status"] == "patched" and entry["manual_review"] == []
    original = PAGES["Lib"]
    patched = _patched(run, entry)
    assert patched == original.replace(_tag(ACCT), ADAPTER_TAG + _tag(ACCT))
    assert patched.count("\n") == original.count("\n")


def test_the_patched_page_re_assessed_with_the_adapter_uploaded_clears_the_finding(run, tmp_path):
    (entry,) = _entries(run, "Lib")
    again = _pipeline(tmp_path, {"Lib": _patched(run, entry)}, flag=ADAPTER)
    assert _hits(again, "Lib") == []


def test_an_inline_call_is_rewritten_and_the_adapter_loads_before_that_script(run):
    (adapter,) = _entries(run, "Inline")
    (odata,) = _entries(run, "Inline", ODATA_RULE)
    assert adapter["status"] == odata["status"] == "patched"
    assert _patch_files(adapter) == _patch_files(odata)
    patched = _patched(run, adapter)
    original = PAGES["Inline"]
    assert patched.count("\n") == original.count("\n")
    assert SVC not in patched and "CrmODataV2.open(req" in patched
    assert patched.index(ADAPTER_TAG) < patched.index("<script>")
    assert patched.startswith(original[: original.index(INLINE)])


def test_a_page_with_a_patched_library_and_an_inline_call_gets_one_patch(run):
    (adapter,) = _entries(run, "Both")
    (odata,) = _entries(run, "Both", ODATA_RULE)
    assert _patch_files(adapter) == _patch_files(odata) and adapter["status"] == "patched"
    patched = _patched(run, adapter)
    assert patched.count(ADAPTER_TAG) == 1
    assert patched.index(ADAPTER_TAG) < patched.index(_tag(ACCT)) < patched.index("<script>")


def test_the_adapter_goes_ahead_of_an_inline_script_that_comes_first(run):
    (adapter,) = _entries(run, "InlineFirst")
    patched = _patched(run, adapter)
    assert patched.index(ADAPTER_TAG) < patched.index("<script>\n") < patched.index(_tag(ACCT))


def test_without_the_flag_no_page_changes_and_each_affected_page_names_the_flag(run):
    bare = run["bare"]
    for page in ("Lib", "Inline", "Both", "InlineFirst", "Late", "Mixed"):
        entries = _entries(bare, page) + _entries(bare, page, ODATA_RULE)
        assert entries
        for entry in entries:
            assert entry["status"] == "manual-review" and "patched" not in entry["files"]
        assert any("--adapter-resource" in e["manual_review"][0]["reason"] for e in entries)


def test_a_library_whose_call_stayed_in_manual_review_gets_no_adapter_edit(run):
    (entry,) = _entries(run, "Kept")
    assert entry["status"] == "manual-review" and "patched" not in entry["files"]
    assert "rewritten in this run" in entry["manual_review"][0]["reason"]


def test_a_src_resolved_only_by_file_name_is_not_the_library(run):
    assert _entries(run, "NameOnly") == []


def test_an_inline_script_with_a_shape_the_transform_does_not_read_stays_unchanged(run):
    (odata,) = _entries(run, "Shape", ODATA_RULE)
    assert odata["status"] == "manual-review" and "patched" not in odata["files"]
    assert odata["manual_review"][0]["line"] == 5
    (adapter,) = _entries(run, "Shape")
    assert adapter["status"] == "manual-review"


def test_a_patched_inline_script_keeps_an_unproven_site_with_the_pages_line(run):
    (odata,) = _entries(run, "Mixed", ODATA_RULE)
    assert odata["status"] == "patched"
    (site,) = odata["manual_review"]
    assert site["line"] == 5 + READ.count("\n") and "outside" in site["reason"]


def test_a_page_listing_the_adapter_ahead_by_name_is_left_alone(run):
    assert _entries(run, "Listed") == [] and _entries(run, "Listed", ODATA_RULE) == []


def test_a_page_loading_the_adapter_after_the_patched_library_goes_to_manual_review(run):
    (entry,) = _entries(run, "Late")
    assert entry["status"] == "manual-review"
    assert "after" in entry["manual_review"][0]["reason"]


def test_a_page_with_a_url_only_in_markup_or_a_comment_changes_nothing(run):
    assert _entries(run, "Prose") == [] and _entries(run, "Prose", ODATA_RULE) == []


def test_the_ledger_records_the_web_resource_name_of_each_adapter(run):
    adapters = run["ledger"]["adapters"]
    assert adapters and {a["web_resource"] for a in adapters.values()} == {ADAPTER}


def test_two_runs_give_byte_identical_patches(run, tmp_path):
    again = _pipeline(tmp_path, {"Both": PAGES["Both"], "Inline": PAGES["Inline"]}, flag=ADAPTER)
    for page in ("Both", "Inline"):
        (first,) = _entries(run, page)
        (second,) = _entries(again, page)
        assert _patched(run, first) == _patched(again, second)


def test_a_patch_the_gate_rejects_leaves_the_page_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "crm.core.inventory_convert.page_adapter_gate", lambda *a: "synthetic failure"
    )
    got = _pipeline(tmp_path, {"Both": PAGES["Both"]}, flag=ADAPTER)
    for rule in (RULE, ODATA_RULE):
        (entry,) = _entries(got, "Both", rule)
        assert entry["status"] == "manual-review" and "patched" not in entry["files"]
    assert "emit gate rejected" in _entries(got, "Both")[0]["manual_review"][0]["reason"]
    assert not list(got["out"].rglob("Both*"))


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (lambda r: r.update(kind="form"), "html_library_order"),
        (lambda r: r["detection"].update(adapter_pattern="("), "adapter_pattern"),
        (lambda r: r["detection"].update(library_rule="nope"), "library_rule"),
    ],
)
def test_a_malformed_page_rule_is_refused(change, match):
    data = json.loads(assess.RULES_PATH.read_text(encoding="utf-8"))
    change(next(r for r in data["rules"] if r["id"] == RULE))
    with pytest.raises(assess.InventoryError, match=match):
        assess.parse_rules(data)


def test_the_page_rule_selects_html_pages_only():
    rule = next(r for r in assess.load_rules().rules if r.id == RULE)
    page, script = (
        cast(Any, SimpleNamespace(kind="web_resource", fields={"web_resource_type": t}))
        for t in (1, 3)
    )
    assert rule.selects(page) and not rule.selects(script)
