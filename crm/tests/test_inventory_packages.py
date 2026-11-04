"""`crm inventory packages`: the dependency graph and work packages of one inventory."""

# pyright: basic

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory as inv
from crm.core.inventory_packages import build_packages, write_packages
from crm.core.inventory_source_map import (
    dump_source_map,
    map_source,
    parse_source_map,
    read_source_map,
)

ORG = "https://crm.example.test/Org"
SHARED_JS = "var Shared = { validate: function () {}, approve: function () {} };\n"
PROJECT_JS = (
    "var Project = { onLoad: function () {\n"
    '  Xrm.Navigation.openWebResource("new_/html/help.html");\n'
    '  Xrm.Navigation.openWebResource(page + ".html");\n'
    "} };\n"
)
HELP_HTML = '<html><script src="../scripts/shared.js"></script></html>\n'
PROJECT_FORM = (
    "<form><formLibraries>"
    '<Library name="new_/scripts/shared.js" /><Library name="new_/scripts/project.js" />'
    "</formLibraries><events>"
    '<event name="onload"><Handlers>'
    '<Handler functionName="Project.onLoad" libraryName="new_/scripts/project.js" enabled="true" />'
    "</Handlers></event>"
    '<event name="onchange" attribute="new_status"><Handlers>'
    '<Handler functionName="Shared.validate" libraryName="new_/scripts/shared.js" enabled="true" />'
    "</Handlers></event></events></form>"
)
ACCOUNT_FORM = (
    '<form><formLibraries><Library name="new_/scripts/shared.js" /></formLibraries></form>'
)
RIBBON = (
    '<RibbonDiffXml><CommandDefinitions><CommandDefinition Id="new.approve"><Actions>'
    '<JavaScriptFunction Library="$webresource:new_/scripts/shared.js"'
    ' FunctionName="Shared.approve" />'
    "</Actions></CommandDefinition></CommandDefinitions></RibbonDiffXml>"
)
SITEMAP = (
    '<SiteMap><SubArea Id="reports" Url="/ISV/reports/summary.aspx" />'
    '<SubArea Id="admin" Url="/tools/admin/admin.aspx" />'
    '<SubArea Id="approve" Url="/ContosoApp/approve.aspx" />'
    '<SubArea Id="home" Url="https://crm.example.test/Org/main.aspx?pagetype=home" />'
    '<SubArea Id="tools" Url="/Org/tools/admin.aspx" />'
    '<SubArea Id="entity" Url="/userdefined/edit.aspx?etc=1" />'
    '<SubArea Id="portal" Url="https://crm.example.test/Portal/start.aspx" /></SiteMap>'
)
STEP = {
    "message": "Update",
    "primary_entity": "new_project",
    "stage": 20,
    "mode": 0,
    "plugin_type": "Contoso.Plugins|Contoso.Plugins.ProjectUpdate",
    "name": "ProjectUpdate: Update of new_project",
    "filtering_attributes": "new_status",
}
STEP_KEY = inv.component_key("step", STEP)
PROJECT_FORM_KEY = "new_project|2|Information"


def _record(kind: str, fields: dict, folder: Path, *, origin="custom", files=()) -> inv.Record:
    key = inv.component_key(kind, fields)
    artifacts = []
    for name, data in files:
        path = inv.artifact_path(kind, key, name)
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_bytes(data.encode("utf-8"))
        hashes = inv.artifact_hashes(data.encode("utf-8"), text=True)
        artifacts.append(inv.Artifact(path, "text", hashes.sha256, hashes.normalized_sha256))
    return inv.Record(kind, key, None, None, origin, False, "published", fields, tuple(artifacts))


def _web_resource(name: str, content: str, folder: Path, ext: str = "js") -> inv.Record:
    code = {"js": 3, "htm": 1}[ext]
    fields = {"name": name, "web_resource_type": code}
    return _record("web_resource", fields, folder, files=[(f"content.{ext}", content)])


def _write_inventory(folder: Path) -> Path:
    """One representative process: shared and process scripts, a ribbon, a plug-in step."""
    folder.mkdir(parents=True, exist_ok=True)
    records = [
        _record("entity", {"logical_name": "new_project"}, folder),
        _record("entity", {"logical_name": "account"}, folder, origin="system"),
        _web_resource("new_/scripts/shared.js", SHARED_JS, folder),
        _web_resource("new_/scripts/project.js", PROJECT_JS, folder),
        _web_resource("new_/html/help.html", HELP_HTML, folder, "htm"),
        _web_resource("new_/scripts/missing.js", "function missing() {}\n", folder),
        _record(
            "form",
            {"entity": "new_project", "form_type": 2, "name": "Information"},
            folder,
            files=[("form.xml", PROJECT_FORM)],
        ),
        _record(
            "form",
            {"entity": "account", "form_type": 2, "name": "Account"},
            folder,
            origin="system",
            files=[("form.xml", ACCOUNT_FORM)],
        ),
        _record("ribbon", {"entity": "new_project"}, folder, files=[("ribbon.xml", RIBBON)]),
        _record("sitemap", {"unique_name": None}, folder, files=[("sitemap.xml", SITEMAP)]),
        _record("plugin_assembly", {"name": "Contoso.Plugins"}, folder),
        _record("plugin_assembly", {"name": "Contoso.Billing"}, folder),
        _record(
            "plugin_type",
            {"assembly": "Contoso.Plugins", "type_name": "Contoso.Plugins.ProjectUpdate"},
            folder,
        ),
        _record(
            "plugin_type",
            {"assembly": "Contoso.Billing", "type_name": "Contoso.Billing.Activity"},
            folder,
        ),
        _record("step", STEP, folder),
        _record(
            "step_image",
            {"step": STEP_KEY, "alias": "PreImage", "image_type": 0, "attributes": "new_status"},
            folder,
        ),
        _record(
            "process",
            {
                "category": 0,
                "primary_entity": "new_project",
                "name": "Project approval",
                "activities": ["Contoso.Billing|Contoso.Billing.Activity"],
            },
            folder,
        ),
        _record(
            "dependency",
            {
                "dependent_kind": "form",
                "dependent_key": PROJECT_FORM_KEY,
                "required_kind": "web_resource",
                "required_key": "new_/scripts/shared.js",
                "dependency_type": 2,
            },
            folder,
            origin="unknown",
        ),
    ]
    counts: dict[str, int] = {}
    for record in records:
        counts[record.kind] = counts.get(record.kind, 0) + 1
    manifest = inv.Inventory(
        schema_version=inv.SCHEMA_VERSION,
        source=inv.Source("cloud", ORG, None, "9.2.0.0000"),
        extracted_at="2026-11-01T10:00:00Z",
        cli_version="1.88.0",
        scope=inv.Scope("org", ()),
        publication="published",
        records=tuple(records),
        coverage={k: inv.KindCoverage("complete", 1, n, False, ()) for k, n in counts.items()},
        gaps=(
            inv.ExtractionGap(
                "plugin_assembly", "Contoso.Plugins", None, "the assembly is stored on disk"
            ),
            inv.ExtractionGap(None, None, "external integrations", "outside the org"),
        ),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")
    return folder


def _csproj(refs: str = "") -> str:
    return f'<Project Sdk="Microsoft.NET.Sdk"><ItemGroup>{refs}</ItemGroup></Project>\n'


SHARED_REF = '<ProjectReference Include="..\\Shared\\Contoso.Shared.csproj" />'


def _write_repo(root: Path) -> Path:
    """A synthetic application repository: two plug-in projects share one C# project."""
    files = {
        "WebResources/new_/scripts/shared.js": SHARED_JS,
        "WebResources/new_/scripts/project.js": "var Project = {};\n",
        "WebResources/new_/html/help.html": HELP_HTML,
        "Plugins/Contoso.Plugins.csproj": _csproj(SHARED_REF),
        "Plugins/ProjectUpdate.cs": "namespace Contoso.Plugins { public class ProjectUpdate"
        " : IPlugin { } }\n",
        "Billing/Contoso.Billing.csproj": _csproj(SHARED_REF),
        "Billing/Activity.cs": "namespace Contoso.Billing { public class Activity"
        " : CodeActivity { } }\n",
        "Shared/Contoso.Shared.csproj": _csproj(),
        "Shared/Helpers.cs": "namespace Contoso.Shared { public static class Helpers { } }\n",
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


SCENARIOS = {
    "scenarios": [
        {
            "name": "Project approval",
            "seeds": [
                f"form:{PROJECT_FORM_KEY}",
                "process:0|new_project|Project approval",
                "entity:new_project",
            ],
            "expected_behavior": ["docs/scenarios/project-approval.md"],
        },
        {"name": "Ghost", "seeds": ["web_resource:new_/ghost.js"], "expected_behavior": []},
    ]
}


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    folder = _write_inventory(tmp_path / "inv")
    source_map = tmp_path / "source-map.json"
    source_map.write_text(
        dump_source_map(map_source(folder, _write_repo(tmp_path / "repo"))), encoding="utf-8"
    )
    scenarios = tmp_path / "scenarios.json"
    scenarios.write_text(json.dumps(SCENARIOS), encoding="utf-8")
    return folder, source_map, scenarios


def _packages(inputs):
    folder, source_map, scenarios = inputs
    return build_packages(folder, read_source_map(source_map), scenarios)


def _package_of(result, member: str):
    (package,) = [p for p in result.packages if member in p.members]
    return package


def _dicts(entries) -> list[dict]:
    return [e.to_dict() for e in entries]


def test_shared_library_and_consumers_fall_in_one_package(inputs):
    result = _packages(inputs)
    js = _package_of(result, "web_resource:new_/scripts/shared.js")
    for member in (
        "web_resource:new_/scripts/project.js",
        "web_resource:new_/html/help.html",
        f"form:{PROJECT_FORM_KEY}",
        "form:account|2|Account",
        "ribbon:new_project",
    ):
        assert member in js.members
    consumers = {(c["kind"], c["key"]) for c in _dicts(js.consumers)}
    assert {("form", PROJECT_FORM_KEY), ("ribbon", "new_project"), ("entity", "account")} <= (
        consumers
    )

    plugins = _package_of(result, "plugin_assembly:Contoso.Plugins")
    assert "plugin_assembly:Contoso.Billing" in plugins.members
    assert f"step:{STEP_KEY}" in plugins.members
    assert "process:0|new_project|Project approval" in plugins.members
    source = {(s["kind"], s["key"]): s for s in _dicts(plugins.source)}
    assert source[("project", "Shared/Contoso.Shared.csproj")]["status"] == "referenced"
    assert source[("plugin_type", "Contoso.Plugins|Contoso.Plugins.ProjectUpdate")]["status"] == (
        "matched"
    )
    (step,) = [r for r in _dicts(plugins.verification) if r["kind"] == "plugin_step"]
    assert step["details"]["stage"] == 20
    assert step["details"]["images"] == [
        {"alias": "PreImage", "attributes": "new_status", "image_type": 0}
    ]
    (process,) = [r for r in _dicts(plugins.verification) if r["kind"] == "process_trigger"]
    assert process["details"]["trigger"] == {
        "status": "unknown",
        "reason": "manifest v1 records no process trigger",
    }
    (form,) = [
        r
        for r in _dicts(js.verification)
        if r["kind"] == "form_script" and r["member"] == f"form:{PROJECT_FORM_KEY}"
    ]
    assert form["details"]["handlers"] == [
        {
            "attribute": None,
            "event": "onload",
            "function": "Project.onLoad",
            "library": "new_/scripts/project.js",
        },
        {
            "attribute": "new_status",
            "event": "onchange",
            "function": "Shared.validate",
            "library": "new_/scripts/shared.js",
        },
    ]
    assert len(result.packages) == 4


def test_every_package_has_five_fields_with_explicit_unknowns(inputs):
    result = _packages(inputs)
    for package in result.packages:
        data = package.to_dict()
        for name in ("source", "consumers", "findings", "expected_behavior", "verification"):
            assert data[name], f"{package.id} {name} is empty"
    lone = _package_of(result, "web_resource:new_/scripts/missing.js").to_dict()
    unknown = {"status": "unknown"}
    for name in ("consumers", "expected_behavior", "verification"):
        assert [unknown.items() <= e.items() for e in lone[name]] == [True], name
    assert {"type": "source_status", "status": "missing_source"}.items() <= (
        lone["findings"][0].items()
    )


def test_dynamic_and_isv_references_become_unknown_edges(inputs):
    result = _packages(inputs)
    js = _package_of(result, "web_resource:new_/scripts/shared.js").to_dict()
    (dynamic,) = [e for e in js["edges"] if e["type"] == "unknown"]
    assert dynamic == {
        "from": "web_resource:new_/scripts/project.js",
        "to": None,
        "type": "unknown",
        "reason": "dynamic",
        "reference": 'page + ".html"',
    }
    assert [f for f in js["findings"] if f["type"] == "unknown_edge"] == [
        {
            "type": "unknown_edge",
            "component": dynamic["from"],
            "status": None,
            "reason": "dynamic",
            "reference": 'page + ".html"',
        }
    ]
    sitemap = _package_of(result, "sitemap:default").to_dict()
    assert sitemap["members"] == ["sitemap:default"]
    assert [(f["reason"], f["reference"]) for f in sitemap["findings"]] == [
        ("isv_page", "/ISV/reports/summary.aspx"),
        ("unresolved_url", "/ContosoApp/approve.aspx"),
        ("unresolved_url", "https://crm.example.test/Portal/start.aspx"),
    ]
    assert result.summary()["unknowns"] == sum(
        1 for p in result.packages for f in p.to_dict()["findings"] if "type" in f
    )


def test_gap_attaches_to_its_node_and_the_rest_is_reported(inputs):
    result = _packages(inputs)
    plugins = _package_of(result, "plugin_assembly:Contoso.Plugins").to_dict()
    assert {
        "type": "extraction_gap",
        "component": "plugin_assembly:Contoso.Plugins",
        "reason": "the assembly is stored on disk",
    }.items() <= plugins["findings"][0].items()
    assert result.summary()["unattached_gaps"] == 1


def test_scenarios_tag_packages_and_report_missing_seeds(inputs):
    result = _packages(inputs)
    js = _package_of(result, "web_resource:new_/scripts/shared.js")
    plugins = _package_of(result, "plugin_assembly:Contoso.Plugins")
    assert js.scenarios == plugins.scenarios == ("Project approval",)
    (expected,) = _dicts(js.expected_behavior)
    assert expected["scenario"] == "Project approval"
    assert expected["references"] == ["docs/scenarios/project-approval.md"]
    assert expected["packages"] == sorted([js.id, plugins.id])
    summary = result.summary()
    assert summary["unmatched_scenarios"] == ["Ghost"]
    assert summary["seeds_unpackaged"] == [
        {"scenario": "Project approval", "seed": "entity:new_project"}
    ]
    assert summary["seeds_not_found"] == [
        {"scenario": "Ghost", "seed": "web_resource:new_/ghost.js"}
    ]


def test_ids_derive_from_member_keys(inputs):
    result = _packages(inputs)
    lone = _package_of(result, "web_resource:new_/scripts/missing.js")
    digest = hashlib.sha256(b"web_resource:new_/scripts/missing.js").hexdigest()
    assert lone.id == f"pkg-{digest[:12]}"
    plugins = _package_of(result, "plugin_assembly:Contoso.Plugins")
    (step,) = [r for r in _dicts(plugins.verification) if r["kind"] == "plugin_step"]
    raw = "\0".join((plugins.id, "plugin_step", f"step:{STEP_KEY}"))
    assert step["id"] == f"req-{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def test_two_runs_write_byte_identical_output(inputs, tmp_path):
    outs = []
    for name in ("a", "b"):
        out = tmp_path / name
        write_packages(_packages(inputs), out)
        outs.append({p.name: p.read_bytes() for p in sorted(out.iterdir())})
    assert outs[0] == outs[1]
    assert "index.json" in outs[0]
    assert len(outs[0]) == 5


def test_source_map_round_trips_with_project_references(inputs):
    _, source_map, _ = inputs
    text = source_map.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["project_references"] == {
        "Billing/Contoso.Billing.csproj": ["Shared/Contoso.Shared.csproj"],
        "Plugins/Contoso.Plugins.csproj": ["Shared/Contoso.Shared.csproj"],
    }
    assert dump_source_map(parse_source_map(data)) == text
    data["external"] = [{"kind": "nuget", "name": "Foo", "version": "", "used_by": []}]
    assert parse_source_map(data).external[0].version == ""
    del data["project_references"]
    assert parse_source_map(data).project_references == {}
    data["schema_version"] = 2
    with pytest.raises(inv.InventoryError, match="schema_version 2"):
        parse_source_map(data)


def test_cli_writes_out_and_summarizes(inputs, tmp_path):
    folder, source_map, scenarios = inputs
    out = tmp_path / "out"
    args = ["--json", "inventory", "packages", str(folder), "--source-map", str(source_map)]
    run = CliRunner().invoke(cli, [*args, "--scenarios", str(scenarios), "--out", str(out)])
    assert run.exit_code == 0, run.output
    data = json.loads(run.output)["data"]
    assert data["packages"] == 4
    assert data["unmatched_scenarios"] == ["Ghost"]
    assert data["out"] == str(out)
    index = json.loads((out / "index.json").read_text(encoding="utf-8"))
    assert [p["file"] for p in index["packages"]] == sorted(
        f"{p['id']}.json" for p in index["packages"]
    )

    table = CliRunner().invoke(cli, [*args[1:], "--out", str(tmp_path / "table")])
    assert table.exit_code == 0, table.output
    assert "gaps in no package" in table.output

    again = CliRunner().invoke(cli, [*args, "--out", str(out)])
    assert again.exit_code == 1
    assert "not empty" in json.loads(again.output)["error"]


def test_cli_refuses_a_source_map_of_another_inventory(inputs, tmp_path):
    folder, source_map, _ = inputs
    data = json.loads(source_map.read_text(encoding="utf-8"))
    data["inventory"]["manifest_sha256"] = "0" * 64
    source_map.write_text(json.dumps(data), encoding="utf-8")
    run = CliRunner().invoke(
        cli,
        ["--json", "inventory", "packages", str(folder), "--source-map", str(source_map)]
        + ["--out", str(tmp_path / "out")],
    )
    assert run.exit_code == 1
    assert "another inventory" in json.loads(run.output)["error"]


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ("[]", "must be an object"),
        ('{"scenarios": [{"name": "x", "seeds": "form:a"}]}', "seeds"),
        ('{"scenarios": [{"name": "x", "seeds": []}, {"name": "x", "seeds": []}]}', "twice"),
        ("{", "cannot read"),
    ],
)
def test_bad_scenario_file_is_refused(inputs, tmp_path, body, match):
    folder, source_map, _ = inputs
    bad = tmp_path / "bad.json"
    bad.write_text(body, encoding="utf-8")
    with pytest.raises(inv.InventoryError, match=match):
        build_packages(folder, read_source_map(source_map), bad)


def test_reference_reader_keeps_unresolvable_and_skips_platform_refs(tmp_path):
    folder = tmp_path / "inv"
    page = (
        '<script src="https://cdn.example.net/lib.js"></script>'
        '<script src="../ClientGlobalContext.js.aspx"></script>'
        '<link href="styles.css" /><a href="#top"></a><a href="javascript:void(0)"></a>'
        '<a href="/WebResources/new_/page.htm"></a>'
        '<script>var u = "$webresource:" + name;</script>'
    )
    records = [
        _web_resource("new_/page.htm", page, folder, "htm"),
        _record(
            "form",
            {"entity": "account", "form_type": 2, "name": "Broken"},
            folder,
            files=[("form.xml", "<form><formLibraries>")],
        ),
    ]
    manifest = inv.Inventory(
        inv.SCHEMA_VERSION,
        inv.Source("cloud", ORG, None, "9.2.0.0000"),
        "2026-11-01T10:00:00Z",
        "1.88.0",
        inv.Scope("org", ()),
        "published",
        tuple(records),
        {k: inv.KindCoverage("complete", 1, 1, False, ()) for k in ("web_resource", "form")},
        (),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")
    (tmp_path / "repo").mkdir()
    result = build_packages(folder, map_source(folder, tmp_path / "repo"))
    unknowns = sorted(
        (e.source.split(":")[0], e.reason, e.reference)
        for p in result.packages
        for e in p.edges
        if e.target is None
    )
    assert unknowns == [
        ("form", "unreadable", records[1].artifacts[0].path),
        ("web_resource", "dynamic", '$webresource:" +'),
        ("web_resource", "external_url", "https://cdn.example.net/lib.js"),
        ("web_resource", "not_in_inventory", "new_/styles.css"),
    ]


def _write(folder: Path, records: list[inv.Record]) -> Path:
    manifest = inv.Inventory(
        inv.SCHEMA_VERSION,
        inv.Source("cloud", ORG, None, "9.2.0.0000"),
        "2026-11-01T10:00:00Z",
        "1.88.0",
        inv.Scope("org", ()),
        "published",
        tuple(records),
        {r.kind: inv.KindCoverage("complete", 1, 1, False, ()) for r in records},
        (),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")
    return folder


def test_unknown_edge_outside_every_package_is_listed(tmp_path):
    folder = tmp_path / "inv"
    system = _record(
        "web_resource",
        {"name": "msdyn_/nav.js", "web_resource_type": 3},
        folder,
        origin="system",
        files=[("content.js", "Xrm.Navigation.openWebResource(page + '.htm');\n")],
    )
    dependency = {
        "dependent_kind": "entity",
        "dependent_key": "new_project",
        "required_kind": "web_resource",
        "required_key": "new_/gone.js",
        "dependency_type": 2,
    }
    records = [
        _record("entity", {"logical_name": "new_project"}, folder),
        system,
        _record("dependency", dependency, folder, origin="unknown"),
    ]
    (tmp_path / "repo").mkdir()
    result = build_packages(_write(folder, records), map_source(folder, tmp_path / "repo"))
    assert result.packages == ()
    assert result.summary()["unattached_unknowns"] == 2
    assert [(e["from"], e["reason"]) for e in result.index()["unattached_unknowns"]] == [
        ("entity:new_project", "not_in_inventory"),
        ("web_resource:msdyn_/nav.js", "dynamic"),
    ]


def test_form_handler_fields_merge_with_form_xml(tmp_path):
    folder = tmp_path / "inv"
    handler = {
        "event": "onload",
        "attribute": None,
        "function": "Shared.init",
        "library": "new_/shared.js",
        "enabled": True,
    }
    xml = (
        '<form><formLibraries><Library name="new_/shared.js" /></formLibraries>'
        '<events><event name="onload"><Handlers>'
        '<Handler functionName="Shared.init" libraryName="new_/shared.js" />'
        "</Handlers></event></events></form>"
    )
    form = _record(
        "form",
        {
            "entity": "account",
            "form_type": 2,
            "name": "Main",
            "libraries": ["new_/shared.js", "new_/fields.js"],
            "handlers": [handler],
        },
        folder,
        files=[("form.xml", xml)],
    )
    records = [_web_resource("new_/shared.js", "var Shared = {};\n", folder), form]
    (tmp_path / "repo").mkdir()
    result = build_packages(_write(folder, records), map_source(folder, tmp_path / "repo"))
    (package,) = result.packages
    (script,) = [r for r in _dicts(package.verification) if r["kind"] == "form_script"]
    del handler["enabled"]
    assert script["details"]["handlers"] == [handler]
    assert [(e.reason, e.reference) for e in package.edges if e.target is None] == [
        ("not_in_inventory", "new_/fields.js")
    ]


def test_handler_library_case_and_empty_scenario_behavior(tmp_path):
    folder = tmp_path / "inv"
    xml = (
        '<form><formLibraries><Library name="New_/Shared.js" /></formLibraries>'
        '<events><event name="onsave"><Handlers>'
        '<Handler functionName="Shared.save" libraryName="NEW_/shared.JS" />'
        "</Handlers></event></events></form>"
    )
    form = _record(
        "form",
        {"entity": "account", "form_type": 2, "name": "Main"},
        folder,
        files=[("form.xml", xml)],
    )
    records = [_web_resource("new_/shared.js", "var Shared = {};\n", folder), form]
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "shared.js").write_text("var Shared = {};\n", encoding="utf-8")
    scenarios = tmp_path / "scenarios.json"
    seeds = ["form:account|2|Main"]
    scenarios.write_text(json.dumps({"scenarios": [{"name": "Save", "seeds": seeds}]}))
    result = build_packages(
        _write(folder, records), map_source(folder, tmp_path / "repo"), scenarios
    )
    (package,) = result.packages
    (script,) = [r for r in _dicts(package.verification) if r["kind"] == "form_script"]
    assert [h["function"] for h in script["details"]["handlers"]] == ["Shared.save"]
    assert _dicts(package.expected_behavior) == [
        {"scenario": "Save", "references": [], "packages": [package.id]},
        {"status": "unknown", "reason": "scenario 'Save' names no expected behavior"},
    ]
