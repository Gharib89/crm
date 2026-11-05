"""`crm inventory convert`: engine, registry, emit gate and the global-context transform."""

# pyright: basic

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory as inv
from crm.core import inventory_assess as assess
from crm.core import inventory_transforms as transforms
from crm.core.code_parse import error_count, parse
from crm.core.inventory_convert import LEDGER_NAME, convert
from crm.core.inventory_packages import build_packages, write_packages
from crm.core.inventory_source_map import dump_source_map, map_source, read_source_map

ORG = "https://crm.example.test/Org"
CTX_JS = (
    "// Xrm.Page.context.getVersion() in a comment stays.\n"
    "function load() {\n"
    "  var url = Xrm.Page.context.getClientUrl();\n"
    "  var id = Xrm.Page.context.getVersion();\n"
    "  var note = 'Xrm.Page.context.getClientUrl()';\n"
    "  return Xrm.Page.getAttribute('name') + url + id + note;\n"
    "}\n"
)
STRINGS_JS = '// Xrm.Page.context.getClientUrl()\nvar s = "Xrm.Page.context.getUserId()";\n'
CLEAN_JS = "function f(ctx) {\n  return ctx.getFormContext();\n}\n"
ALONE_JS = "function g() {\n  return Xrm.Page.context.getVersion();\n}\n"
MIXED_JS = (
    "var c = Xrm.Page.context;\n"
    "var n = Xrm.Page.context.getCurrentTheme();\n"
    "var u = Xrm.Page.context.getClientUrl();\n"
)
CTX_NAME, STRINGS_NAME, CLEAN_NAME, ALONE_NAME, MIXED_NAME = (
    f"new_/scripts/{n}.js" for n in ("ctx", "strings", "clean", "alone", "mixed")
)


def _record(name: str, content: str, folder: Path, *, code: int = 3, ext: str = "js") -> inv.Record:
    fields = {"name": name, "web_resource_type": code}
    key = inv.component_key("web_resource", fields)
    path = inv.artifact_path("web_resource", key, f"content.{ext}")
    (folder / path).parent.mkdir(parents=True, exist_ok=True)
    data = content.encode()
    (folder / path).write_bytes(data)
    h = inv.artifact_hashes(data, text=True)
    artifact = inv.Artifact(path, "text", h.sha256, h.normalized_sha256)
    return inv.Record(
        "web_resource", key, None, None, "custom", False, "published", fields, (artifact,)
    )


def _write_inventory(folder: Path) -> Path:
    folder.mkdir(parents=True)
    records = [
        _record(CTX_NAME, CTX_JS, folder),
        _record(STRINGS_NAME, STRINGS_JS, folder),
        _record(CLEAN_NAME, CLEAN_JS, folder),
        _record(ALONE_NAME, ALONE_JS, folder),
        _record(MIXED_NAME, MIXED_JS, folder),
        _record(
            "new_/html/page.htm",
            "<script>Xrm.Page.context.getUserId()</script>",
            folder,
            code=1,
            ext="htm",
        ),
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
    return folder


class World:
    """An inventory with its findings, source map and packages, as the pipeline writes them."""

    def __init__(self, root: Path) -> None:
        self.inventory = _write_inventory(root / "inv")
        repo = root / "repo"
        (repo / "WebResources/new_/scripts").mkdir(parents=True)
        (repo / "WebResources/new_/scripts/ctx.js").write_text(CTX_JS, encoding="utf-8")
        self.source_map = root / "source-map.json"
        sm = map_source(self.inventory, repo)
        self.source_map.write_text(dump_source_map(sm), encoding="utf-8")
        self.findings = root / "findings.json"
        result = assess.assess(self.inventory, "online")
        self.findings.write_text(assess.dump_findings(result), encoding="utf-8")
        self.packages = root / "packages"
        built = build_packages(
            self.inventory,
            read_source_map(self.source_map),
            None,
            assess.read_findings(self.findings),
        )
        write_packages(built, self.packages)
        self.out = root / "out"

    def run(self, *, with_source_map: bool = False, out: Path | None = None):
        return convert(
            self.inventory,
            self.findings,
            self.packages,
            out or self.out,
            source_map_path=self.source_map if with_source_map else None,
        )


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _ledger(out: Path) -> dict:
    return json.loads((out / LEDGER_NAME).read_text(encoding="utf-8"))


def _entry(out: Path, name: str, rule: str = "web-resource-xrm-page") -> dict:
    (entry,) = [e for e in _ledger(out)["entries"] if e["key"] == name and e["rule"] == rule]
    return entry


def test_a_component_with_the_finding_gets_diff_patched_file_hash_and_ledger(world):
    world.run()
    entry = _entry(world.out, CTX_NAME)
    assert entry["status"] == "patched" and entry["edits"] == 2
    assert entry["package"].startswith("pkg-") and entry["rule"] == "web-resource-xrm-page"
    patched = (world.out / entry["files"]["patched"]).read_text(encoding="utf-8")
    assert patched.count("Xrm.Utility.getGlobalContext().getClientUrl()") == 1
    assert "Xrm.Utility.getGlobalContext().getVersion()" in patched
    assert hashlib.sha256(patched.encode()).hexdigest() == entry["patched_sha256"]
    diff = (world.out / entry["files"]["diff"]).read_text(encoding="utf-8")
    assert diff.startswith("--- a/artifacts/web_resource/")
    assert "-  var url = Xrm.Page.context.getClientUrl();" in diff
    assert "+  var url = Xrm.Utility.getGlobalContext().getClientUrl();" in diff


def test_a_component_without_the_finding_writes_nothing(world):
    world.run()
    keys = {e["key"] for e in _ledger(world.out)["entries"]}
    assert CLEAN_NAME not in keys
    on_disk = {p.name for p in world.out.rglob("*") if p.is_file()}
    assert not any("clean" in n for n in on_disk)


def test_text_in_a_string_or_comment_is_not_rewritten(world):
    world.run()
    assert STRINGS_NAME not in {e["key"] for e in _ledger(world.out)["entries"]}
    entry = _entry(world.out, CTX_NAME)
    patched = (world.out / entry["files"]["patched"]).read_text(encoding="utf-8")
    assert "// Xrm.Page.context.getVersion() in a comment stays." in patched
    assert "'Xrm.Page.context.getClientUrl()'" in patched


def test_other_uses_of_the_context_become_manual_review_entries_and_stay_untouched(world):
    world.run()
    entry = _entry(world.out, MIXED_NAME)
    assert entry["status"] == "patched" and entry["edits"] == 1
    reviews = {(m["line"], m["rule"]) for m in entry["manual_review"]}
    assert reviews == {(1, "web-resource-xrm-page"), (2, "web-resource-xrm-page")}
    assert all(m["file"].startswith("artifacts/web_resource/") for m in entry["manual_review"])
    patched = (world.out / entry["files"]["patched"]).read_text(encoding="utf-8")
    assert "var c = Xrm.Page.context;" in patched and "getCurrentTheme()" in patched


def test_a_web_resource_type_the_transform_does_not_read_goes_to_manual_review(world):
    world.run()
    entry = _entry(world.out, "new_/html/page.htm")
    assert entry["status"] == "manual-review" and entry["files"] == {}
    assert entry["manual_review"][0]["line"] is None


def test_a_patch_the_gate_rejects_leaves_the_code_untouched(world, monkeypatch):
    def broken(root):
        node = root.children[0]
        return transforms.Plan(
            (transforms.Edit(node.start_byte, node.end_byte, b"function ("),), ()
        )

    monkeypatch.setitem(
        transforms.TRANSFORMS, "global-context", transforms.Transform("javascript", (3,), broken)
    )
    world.run()
    entries = _ledger(world.out)["entries"]
    assert entries and all(e["status"] == "manual-review" and e["files"] == {} for e in entries)
    assert "emit gate rejected" in _entry(world.out, CTX_NAME)["manual_review"][0]["reason"]
    assert not list(world.out.rglob("*.patched.js"))


def test_the_gate_rejects_a_rewrite_that_leaves_the_rule_matching(world, monkeypatch):
    def noop(root):
        return transforms.Plan((transforms.Edit(0, 0, b"/* x */"),), ())

    monkeypatch.setitem(
        transforms.TRANSFORMS, "global-context", transforms.Transform("javascript", (3,), noop)
    )
    world.run()
    reason = _entry(world.out, CTX_NAME)["manual_review"][0]["reason"]
    assert "re-running the rule clears 0 of the 1 rewritten sites" in reason


def test_a_repo_diff_appears_only_when_the_source_map_links_the_file(world, tmp_path):
    world.run(out=tmp_path / "without")
    assert not list((tmp_path / "without").rglob("*.repo.diff"))
    world.run(with_source_map=True)
    with_map = _entry(world.out, CTX_NAME)
    assert with_map["repo_path"] == "WebResources/new_/scripts/ctx.js"
    repo_diff = (world.out / with_map["files"]["repo_diff"]).read_text(encoding="utf-8")
    assert repo_diff.startswith("--- a/WebResources/new_/scripts/ctx.js\n+++ b/WebResources/")
    unlinked = _entry(world.out, ALONE_NAME)
    assert unlinked["status"] == "patched" and "repo_diff" not in unlinked["files"]
    assert unlinked["repo_path"] is None


def test_the_repo_diff_applies_to_the_repo_file(world, tmp_path):
    world.run(with_source_map=True)
    entry = _entry(world.out, CTX_NAME)
    repo = tmp_path / "repo"
    patch = (world.out / entry["files"]["repo_diff"]).resolve()
    subprocess.run(["git", "apply", str(patch)], cwd=repo, check=True)
    patched = (world.out / entry["files"]["patched"]).read_text(encoding="utf-8")
    assert (repo / "WebResources/new_/scripts/ctx.js").read_text(encoding="utf-8") == patched


def test_nothing_is_written_outside_out_and_the_inventory_is_untouched(world):
    before = {p: p.read_bytes() for p in world.inventory.rglob("*") if p.is_file()}
    tree = {p for p in world.out.parent.rglob("*")}
    world.run(with_source_map=True)
    assert {p: p.read_bytes() for p in world.inventory.rglob("*") if p.is_file()} == before
    created = {p for p in world.out.parent.rglob("*")} - tree
    assert all(p == world.out or world.out in p.parents for p in created)


def test_two_runs_write_the_same_ledger(world, tmp_path):
    world.run(out=tmp_path / "one")
    world.run(out=tmp_path / "two")
    one, two = (tmp_path / "one" / LEDGER_NAME), (tmp_path / "two" / LEDGER_NAME)
    assert one.read_bytes() == two.read_bytes()


def test_the_ledger_is_versioned_and_names_its_inventory(world):
    world.run()
    ledger = _ledger(world.out)
    assert ledger["schema_version"] == 1
    manifest = hashlib.sha256((world.inventory / inv.MANIFEST_NAME).read_bytes()).hexdigest()
    assert ledger["inventory"]["manifest_sha256"] == manifest


def test_an_out_inside_the_inventory_or_not_empty_is_refused(world, tmp_path):
    with pytest.raises(inv.InventoryError, match="inside the inventory"):
        world.run(out=world.inventory / "patches")
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "x").write_text("x")
    with pytest.raises(inv.InventoryError, match="not empty"):
        world.run(out=busy)


def test_findings_of_another_inventory_are_refused(world):
    data = json.loads(world.findings.read_text(encoding="utf-8"))
    data["inventory"]["manifest_sha256"] = "0" * 64
    world.findings.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(inv.InventoryError, match="another inventory"):
        world.run()


def _edit_index(world, edit):
    path = world.packages / "index.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_a_package_id_that_escapes_out_is_refused(world):
    _edit_index(world, lambda d: d["packages"][0].update(id="../escaped"))
    with pytest.raises(inv.InventoryError, match="malformed package"):
        world.run()
    assert not (world.out.parent / "escaped.json").exists()


def test_a_packages_folder_of_another_inventory_or_version_is_refused(world):
    _edit_index(world, lambda d: d["inventory"].update(manifest_sha256="0" * 64))
    with pytest.raises(inv.InventoryError, match="packages folder was made from another"):
        world.run()
    _edit_index(world, lambda d: d.update(schema_version=999))
    with pytest.raises(inv.InventoryError, match="schema_version 999"):
        world.run()


def test_a_package_file_of_another_version_is_refused(world):
    pkg = next(p for p in world.packages.glob("pkg-*.json"))
    data = json.loads(pkg.read_text(encoding="utf-8"))
    data["schema_version"] = 999
    pkg.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(inv.InventoryError, match="schema_version"):
        world.run()


def test_diff_lines_split_on_lf_only(world):
    from crm.core.inventory_convert import _diff

    old = "a\u0085b\rc\nXrm.Page.context.getUserId();\n"
    diff = _diff(old, old.replace("Xrm.Page.context", "Y"), "p.js")
    assert diff.count("\n-") == 1 and "@@ -1,2 +1,2 @@" in diff


def test_names_that_slug_alike_keep_distinct_files(tmp_path):
    from crm.core.inventory_convert import Entry, _write

    out = tmp_path / "o"
    for path in (
        "artifacts/web_resource/main.content-aaa.content.js",
        "artifacts/web_resource/main.content.extra-bbb.content.js",
    ):
        _write(out, Entry("pkg-x", "web_resource", path, "r", "t", "patched"), path, b"a\n", b"b\n")
    assert len(list((out / "pkg-x").glob("*.diff"))) == 2


def test_an_artifact_changed_since_extraction_is_manual_review(world):
    record = next(r for r in inv.read_inventory(world.inventory).records if r.key == CTX_NAME)
    (world.inventory / record.artifacts[0].path).write_text("// edited\n", encoding="utf-8")
    world.run()
    entry = _entry(world.out, CTX_NAME)
    assert entry["status"] == "manual-review"
    assert "changed since extraction" in entry["manual_review"][0]["reason"]


def test_cli_converts_and_reports_counts(world):
    args = ["--json", "inventory", "convert", str(world.inventory)]
    args += ["--findings", str(world.findings), "--packages", str(world.packages)]
    args += ["--source-map", str(world.source_map), "--out", str(world.out)]
    res = CliRunner().invoke(cli, args)
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)["data"]
    assert data["patched"] == 3 and data["out"] == str(world.out)
    assert data["manual_review"] == 4


def test_cli_exits_1_for_a_non_empty_out(world, tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "x").write_text("x")
    args = ["--json", "inventory", "convert", str(world.inventory)]
    args += ["--findings", str(world.findings), "--packages", str(world.packages)]
    res = CliRunner().invoke(cli, args + ["--out", str(tmp_path / "out")])
    assert res.exit_code == 1 and "not empty" in res.output


# ── the transform, on its own ───────────────────────────────────────────────


def _convert_text(source: str) -> tuple[str, list[str]]:
    plan = transforms.TRANSFORMS["global-context"].plan(parse("javascript", source.encode()))
    out = source.encode()
    for e in reversed(plan.edits):
        out = out[: e.start] + e.text + out[e.end :]
    return out.decode(), [u.reason for u in plan.unproven]


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (
            "x = Xrm.Page.context.getClientUrl();",
            "x = Xrm.Utility.getGlobalContext().getClientUrl();",
        ),
        (
            "x = Xrm.Page.context.getVersion( );",
            "x = Xrm.Utility.getGlobalContext().getVersion( );",
        ),
        ("f(Xrm.Page.context.getVersion());", "f(Xrm.Utility.getGlobalContext().getVersion());"),
        (
            "x = Xrm.Page.context\n  .getVersion();",
            "x = Xrm.Utility.getGlobalContext()\n  .getVersion();",
        ),
        ("x = 'Xrm.Page.context.getUserId()'; // Xrm.Page.context.getUserId()", None),
        ("x = `Xrm.Page.context.getUserId()`;", None),
        ("x = Xrm.Page.getAttribute('a');", None),
        ("x = Xrm.Utility.getGlobalContext().getUserId();", None),
    ],
)
def test_global_context_before_and_after(before, after):
    patched, unproven = _convert_text(before)
    assert patched == (after if after is not None else before) and unproven == []


@pytest.mark.parametrize(
    "source",
    [
        "function f(Xrm) { return Xrm.Page.context.getClientUrl(); }",
        "var Xrm = mock; x = Xrm.Page.context.getClientUrl();",
        "var f = (Xrm) => Xrm.Page.context.getClientUrl();",
        "try {} catch (Xrm) { x = Xrm.Page.context.getClientUrl(); }",
        "function load({Xrm = fb}) { return Xrm.Page.context.getClientUrl(); }",
        "import Xrm from 'x'; x = Xrm.Page.context.getClientUrl();",
        "import {Xrm} from 'x'; x = Xrm.Page.context.getClientUrl();",
        "for (Xrm of list) { x = Xrm.Page.context.getClientUrl(); }",
        "x = window.Xrm.Page.context.getClientUrl();",
        "x = parent.Xrm.Page.context.getClientUrl();",
        "x = Xrm.Page?.context.getClientUrl();",
        "x = Xrm?.Page.context.getClientUrl();",
        "x = Xrm.Page.context?.getClientUrl();",
        "x = Xrm.Page.context.getClientUrl?.();",
        "var c = Xrm.Page.context;",
        "x = Xrm.Page.context.getCurrentTheme();",
        "x = Xrm.Page.context.getClientUrl;",
        "x = Xrm.Page.context['getClientUrl']();",
        "x = (Xrm.Page.context).getClientUrl();",
    ],
)
def test_global_context_leaves_what_it_cannot_prove(source):
    patched, unproven = _convert_text(source)
    assert patched == source and len(unproven) == 1


def test_a_broken_original_does_not_block_an_unrelated_rewrite():
    patched, _ = _convert_text("x = Xrm.Page.context.getVersion();\nfunction (\n")
    assert patched.startswith("x = Xrm.Utility.getGlobalContext().getVersion();")
    assert error_count(parse("javascript", patched.encode())) == error_count(
        parse("javascript", b"x = 1;\nfunction (\n")
    )


def test_a_rule_may_name_a_transform_only_by_a_lowercase_hyphen_id():
    data = json.loads(assess.RULES_PATH.read_text(encoding="utf-8"))
    data["rules"][0]["transform"] = "Not An Id"
    with pytest.raises(inv.InventoryError, match="transform"):
        assess.parse_rules(data)
    rules = {r.id: r for r in assess.load_rules().rules}
    assert rules["web-resource-xrm-page"].transform == "global-context"
    assert rules["web-resource-xrm-utility"].transform == "xrm-utility-review"
    assert rules["web-resource-crmform"].transform is None


def test_tree_sitter_is_not_imported_until_convert_parses():
    code = (
        "import sys, crm.cli, crm.commands.inventory;"
        "assert not [m for m in sys.modules if m.startswith('tree_sitter')], 'eager import'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_the_bundle_smoke_fixture_still_converts(tmp_path):
    """The package CI job and scripts/build.* run `convert` on this folder in the frozen binary."""
    fixture = Path(__file__).parent / "fixtures" / "inventory" / "convert-smoke"
    result = convert(
        fixture / "inventory", fixture / "findings.json", fixture / "packages", tmp_path / "out"
    )
    assert result.summary()["patched"] == 1
    patched = next((tmp_path / "out").rglob("*.patched.js")).read_text(encoding="utf-8")
    assert "Xrm.Utility.getGlobalContext().getClientUrl()" in patched
