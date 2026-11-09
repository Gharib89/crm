"""`crm inventory convert`: a form lists the OData v2 adapter ahead of patched libraries (ADR 0034).

Each case is a library script plus a form listing it. Everything runs through `assess`, `packages`
and the `convert` CLI, then asserts what landed under `--out`: the patched form XML, the ledger's
form entries and the adapter's recorded web resource name.
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
from crm.core.inventory_form_libraries import form_library_tags, plan_adapter_library
from crm.core.inventory_packages import build_packages, write_packages
from crm.core.inventory_source_map import map_source
from crm.core.inventory_transforms import Edit
from crm.tests.test_inventory_assess import _form
from crm.tests.test_inventory_convert import _record
from crm.tests.test_inventory_convert_odata import SVC, _entity, _write

RULE = "form-library-no-odata-adapter"
HANDLER_RULE = "form-handler-no-execution-context"
ADAPTER = "new_/scripts/crm-odata-v2-adapter.js"

ACCT, OTHER, BAD, CLEAN, CTX, REAL = (
    f"new_/scripts/{n}.js" for n in ("acct", "other", "bad", "clean", "ctx", "real-adapter")
)
CALL = f'req.open("GET", "{SVC}/AccountSet?$select=Name", false);\n'
READ = f"var req = new XMLHttpRequest();\n{CALL}req.send();\n"
SCRIPTS = {
    ACCT: READ,
    OTHER: READ,
    BAD: f'var req = new XMLHttpRequest();\nreq.open("POST", "{SVC}/AccountSet", false);\n',
    CLEAN: "function f(ctx) {\n  return ctx.getFormContext();\n}\n",
    REAL: "(function (global) {\n  global.CrmODataV2 = {};\n})(window);\n",
    CTX: "function onLoad() {\n  var n = Xrm.Page.getAttribute('name');\n" + READ + "}\n",
}

PAIR = {ACCT: SCRIPTS[ACCT], OTHER: SCRIPTS[OTHER]}
PLAIN = {CLEAN: SCRIPTS[CLEAN], ACCT: SCRIPTS[ACCT]}


def _lib(name: str, n: int) -> str:
    return f'<Library name="{name}" libraryUniqueId="{{0000000{n}-0000-0000-0000-000000000000}}" />'


def _xml(*libraries: str, handler: str = "") -> str:
    events = (
        f'<events>\n<event name="onload"><Handlers>{handler}</Handlers></event>\n</events>\n'
        if handler
        else ""
    )
    body = "\n".join(libraries)
    return f"<form>\n<formLibraries>\n{body}\n</formLibraries>\n{events}</form>\n"


def _names(xml: str) -> list[str]:
    return re.findall(r'<Library name="([^"]*)"', xml)


def _pipeline(
    root: Path, forms: dict[str, str], *, scripts=None, flag: str | None = None, extra=()
) -> dict:
    folder = root / "inv"
    folder.mkdir(parents=True)
    scripts = SCRIPTS if scripts is None else scripts
    records = [_record(n, js, folder) for n, js in scripts.items()]
    records += [_form(n, xml.encode(), folder) for n, xml in forms.items()]
    _write(folder, "crm2011", records, ("web_resource", "form"))
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
    return {
        "res": res,
        "out": root / "out",
        "ledger": ledger,
        "assess": result,
        "folder": folder,
        "records": records,
    }


def _entries(run: dict, form: str, rule: str = RULE) -> list[dict]:
    keys = {r.key for r in run["records"] if r.kind == "form" and r.fields["name"] == form}
    return [e for e in run["ledger"]["entries"] if e["key"] in keys and e["rule"] == rule]


def _patched(run: dict, entry: dict) -> str:
    return (run["out"] / entry["files"]["patched"]).read_text(encoding="utf-8")


def _finding(run: dict, form: str, rule: str = RULE) -> list:
    keys = {r.key for r in run["records"] if r.kind == "form" and r.fields["name"] == form}
    return [f for f in run["assess"].findings if f.key in keys and f.rule == rule]


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> dict:
    forms = {
        "Plain": _xml(_lib(CLEAN, 1), _lib(ACCT, 2)),
        "Listed": _xml(_lib(CLEAN, 1), _lib(ADAPTER, 3), _lib(ACCT, 2)),
        "Shipped": _xml(_lib(REAL, 8), _lib(ACCT, 2)),
        "Late": _xml(_lib(ACCT, 2), _lib(ADAPTER, 3)),
        "NoCall": _xml(_lib(CLEAN, 1)),
        "Kept": _xml(_lib(BAD, 4)),
        "Twice": _xml(_lib(CLEAN, 1), _lib(ACCT, 2), _lib(OTHER, 5)),
        "Handler": _xml(
            _lib(CTX, 6),
            _lib(CLEAN, 1),
            handler=f'<Handler functionName="onLoad" libraryName="{CTX}" enabled="true" />',
        ),
        "Missing": _xml(_lib(ACCT, 2), _lib("new_/scripts/gone.js", 7)),
    }
    root = tmp_path_factory.mktemp("adapter")
    out = _pipeline(root, forms, flag=ADAPTER)
    assert out["res"].exit_code == 0, out["res"].output
    bare = _pipeline(tmp_path_factory.mktemp("bare"), forms)
    assert bare["res"].exit_code == 0, bare["res"].output
    out["bare"] = bare
    return out


def test_the_rule_flags_a_form_listing_a_library_with_an_odata_v2_call(run):
    for form in ("Plain", "Late", "Twice", "Kept", "Missing"):
        (hit,) = [f for f in _finding(run, form) if f.cls != "unknown"]
        assert isinstance(hit.evidence, assess.RegistrationEvidence)
        assert hit.evidence.registrations[0]["library"] in {ACCT, BAD}


def test_a_form_that_lists_the_adapter_first_or_no_such_library_is_not_flagged(run):
    for form in ("Shipped", "NoCall"):
        assert [f for f in _finding(run, form) if f.cls != "unknown"] == []


def test_a_library_that_cannot_be_read_is_unknown_not_clear(run):
    unknown = [f for f in _finding(run, "Missing") if f.cls == "unknown"]
    assert unknown and "gone.js" in unknown[0].reason


def test_convert_inserts_the_adapter_before_the_first_patched_library(run):
    (entry,) = _entries(run, "Plain")
    assert entry["status"] == "patched" and entry["manual_review"] == []
    patched = _patched(run, entry)
    assert _names(patched) == [CLEAN, ADAPTER, ACCT]
    guid = re.search(
        rf'<Library name="{ADAPTER}" libraryUniqueId="(\{{[0-9A-F-]{{36}}\}})" />', patched
    )
    assert guid, patched
    original = _xml(_lib(CLEAN, 1), _lib(ACCT, 2))
    inserted = f'<Library name="{ADAPTER}" libraryUniqueId="{guid.group(1)}" />'
    assert patched == original.replace(_lib(ACCT, 2), inserted + _lib(ACCT, 2))
    assert patched.count("\n") == original.count("\n")


def test_the_patched_form_re_assessed_with_the_adapter_uploaded_clears_the_finding(run, tmp_path):
    (entry,) = _entries(run, "Plain")
    adapter = (run["out"] / entry["package"] / "crm-odata-v2-adapter.js").read_text(
        encoding="utf-8"
    )
    again = _pipeline(
        tmp_path,
        {"Plain": _patched(run, entry)},
        scripts={CLEAN: SCRIPTS[CLEAN], ACCT: SCRIPTS[ACCT], ADAPTER: adapter},
    )
    assert [f for f in _finding(again, "Plain") if f.cls != "unknown"] == []


def test_without_the_flag_no_form_changes_and_each_affected_form_names_the_flag(run):
    bare = run["bare"]
    for form in ("Plain", "Late", "Twice", "Handler"):
        (entry,) = _entries(bare, form)
        assert entry["status"] == "manual-review" and "patched" not in entry["files"]
        assert "--adapter-resource" in entry["manual_review"][0]["reason"]
    assert all(
        "adapter-resource" not in e["files"].get("patched", "") for e in bare["ledger"]["entries"]
    )
    assert bare["ledger"]["adapters"]
    assert all("web_resource" not in a for a in bare["ledger"]["adapters"].values())


def test_a_form_whose_library_stayed_in_manual_review_gets_no_adapter(run):
    (entry,) = _entries(run, "Kept")
    assert entry["status"] == "manual-review" and "patched" not in entry["files"]
    assert "rewritten in this run" in entry["manual_review"][0]["reason"]


def test_a_form_listing_the_adapter_ahead_by_name_is_left_alone(run):
    assert _entries(run, "Listed") == []


def test_a_form_listing_the_adapter_after_the_patched_library_goes_to_manual_review(run):
    (entry,) = _entries(run, "Late")
    assert entry["status"] == "manual-review"
    assert "after" in entry["manual_review"][0]["reason"]


def test_a_form_with_both_findings_gets_one_patch_with_both_edits(run):
    (adapter,) = _entries(run, "Handler")
    (handler,) = _entries(run, "Handler", HANDLER_RULE)
    assert adapter["status"] == handler["status"] == "patched"
    assert adapter["files"] == handler["files"]
    patched = _patched(run, adapter)
    assert _names(patched) == [ADAPTER, CTX, CLEAN]
    assert 'passExecutionContext="true"' in patched
    assert len({e["files"]["patched"] for e in (adapter, handler)}) == 1


def test_the_ledger_records_the_web_resource_name_of_each_adapter(run):
    adapters = run["ledger"]["adapters"]
    assert adapters and {a["web_resource"] for a in adapters.values()} == {ADAPTER}


def test_the_form_is_listed_once_per_work_package_it_sits_in_and_names_stay_unique(run):
    (entry,) = _entries(run, "Twice")
    assert entry["status"] == "patched"
    assert _names(_patched(run, entry)) == [CLEAN, ADAPTER, ACCT, OTHER]


def test_two_runs_give_byte_identical_patches(run, tmp_path):
    again = _pipeline(
        tmp_path,
        {"Plain": _xml(_lib(CLEAN, 1), _lib(ACCT, 2))},
        scripts=PLAIN,
        flag=ADAPTER,
    )
    (first,) = _entries(run, "Plain")
    (second,) = _entries(again, "Plain")
    assert _patched(run, first) == _patched(again, second)


def _two_packages() -> dict[str, str]:
    return {"One": _xml(_lib(ACCT, 2)), "Two": _xml(_lib(OTHER, 5))}


def test_two_packages_with_adapters_and_one_name_fail_before_anything_is_written(tmp_path):
    forms = _two_packages()
    got = _pipeline(tmp_path, forms, scripts=PAIR, flag=ADAPTER)
    assert got["res"].exit_code == 1
    assert "{package}" in got["res"].output and "crm-odata-v2-adapter" in got["res"].output
    assert not (tmp_path / "out").exists() or not any((tmp_path / "out").iterdir())


def test_a_name_with_package_gives_each_work_package_its_own_adapter(tmp_path):
    got = _pipeline(tmp_path, _two_packages(), scripts=PAIR, flag="new_/{package}/adapter.js")
    assert got["res"].exit_code == 0, got["res"].output
    names = {pkg: a["web_resource"] for pkg, a in got["ledger"]["adapters"].items()}
    assert len(names) == 2 and all(n == f"new_/{pkg}/adapter.js" for pkg, n in names.items())
    for form in ("One", "Two"):
        (entry,) = _entries(got, form)
        assert entry["status"] == "patched" and entry["package"] in _patched(got, entry)


def test_a_blank_name_is_refused(tmp_path):
    got = _pipeline(tmp_path, {"One": _xml(_lib(ACCT, 2))}, scripts=PAIR, flag="  ")
    assert got["res"].exit_code == 1 and "--adapter-resource" in got["res"].output


def test_a_library_list_a_text_scan_and_a_parse_disagree_on_is_not_mapped():
    assert (
        form_library_tags(
            b"<form><formLibraries><!-- <Library name='a' /> --></formLibraries></form>"
        )
        is None
    )
    assert form_library_tags(b"<form><formLibraries><Library name='a'") is None
    tags = form_library_tags(b"<form><formLibraries><Library name='a' /></formLibraries></form>")
    assert tags is not None and [t.name for t in tags] == ["a"]


def test_library_tags_survive_awkward_but_valid_markup():
    text = (
        b"<form><formLibraries>"
        b'<Library name="a&amp;b" libraryUniqueId="{1}"></Library>'
        b'<Library libraryUniqueId="x>y" name="c" />'
        b"</formLibraries></form>"
    )
    tags = form_library_tags(text)
    assert tags is not None and [t.name for t in tags] == ["a&b", "c"]
    assert text[tags[1].start :].startswith(b'<Library libraryUniqueId="x>y"')


def test_the_inserted_element_escapes_the_adapter_name():
    text = b'<form><formLibraries><Library name="a" /></formLibraries></form>'
    tags = form_library_tags(text)
    assert tags is not None
    edit = plan_adapter_library(tags, 'x&"<y', {"a"}, "{G}")
    assert isinstance(edit, Edit)
    patched = text[: edit.start] + edit.text + text[edit.end :]
    again = form_library_tags(patched)
    assert again is not None and [t.name for t in again] == ['x&"<y', "a"]


def test_a_form_whose_library_list_cannot_be_mapped_is_manual_review(tmp_path):
    xml = _xml(_lib(CLEAN, 1), _lib(ACCT, 2)).replace(
        "<formLibraries>", "<formLibraries><!-- <Library name='x' /> -->"
    )
    got = _pipeline(tmp_path, {"Plain": xml}, scripts=PLAIN, flag=ADAPTER)
    (entry,) = _entries(got, "Plain")
    assert entry["status"] == "manual-review"
    assert "library list cannot be mapped" in entry["manual_review"][0]["reason"]


def test_a_patch_the_gate_rejects_leaves_the_form_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr("crm.core.inventory_convert.adapter_gate", lambda *a: "synthetic failure")
    forms = {"Plain": _xml(_lib(CLEAN, 1), _lib(ACCT, 2))}
    got = _pipeline(tmp_path, forms, scripts=PLAIN, flag=ADAPTER)
    (entry,) = _entries(got, "Plain")
    assert entry["status"] == "manual-review" and "patched" not in entry["files"]
    assert "emit gate rejected" in entry["manual_review"][0]["reason"]
