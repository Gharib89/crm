"""`crm inventory map-source`: match deployed components to an application repository."""

# pyright: basic

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory as inv
from crm.core.inventory_source_map import map_source

REPO = Path(__file__).parent / "fixtures" / "inventory" / "source-repo"
ORG = "https://crm.contoso.example/Contoso"

#: The minified build of the fixture repo's lookup.js: one line past the minified mark.
MINIFIED = (
    "function lookup(n){var t=n.getFormContext();return t}" + "var _pad=" + "0," * 250 + "0;\n"
)


def _record(kind: str, fields: dict, folder: Path, *, origin="custom", files=()) -> inv.Record:
    key = inv.component_key(kind, fields)
    artifacts = []
    for name, data in files:
        path = inv.artifact_path(kind, key, name)
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_bytes(data)
        hashes = inv.artifact_hashes(data, text=True)
        artifacts.append(inv.Artifact(path, "text", hashes.sha256, hashes.normalized_sha256))
    return inv.Record(kind, key, None, None, origin, False, "published", fields, tuple(artifacts))


def _web_resource(name: str, content: str, folder: Path, *, code=3, origin="custom"):
    ext = {1: "htm", 3: "js"}[code]
    return _record(
        "web_resource",
        {"name": name, "web_resource_type": code},
        folder,
        origin=origin,
        files=[(f"content.{ext}", content.encode("utf-8"))],
    )


def _plugin_type(assembly: str, type_name: str, folder: Path, *, activity=False):
    return _record(
        "plugin_type",
        {"assembly": assembly, "type_name": type_name, "workflow_activity": activity},
        folder,
    )


def _write_inventory(folder: Path) -> Path:
    """A #1048 manifest-v1 inventory whose components exercise every map-source status."""
    folder.mkdir(parents=True, exist_ok=True)
    project_js = (
        "var Contoso = Contoso || {};\n"
        "Contoso.Project = { onLoad: function () { return true; } };\n"
    )
    dashboard = (REPO / "WebResources/new_/html/dashboard.html").read_text(encoding="utf-8")
    form_xml = (
        '<form><formLibraries><Library name="new_/scripts/project.js" /></formLibraries></form>'
    )
    sitemap_xml = (
        '<SiteMap><SubArea Id="dash" Url="$webresource:new_/html/dashboard.html" /></SiteMap>'
    )
    records = [
        _web_resource("new_/scripts/project.js", project_js, folder),
        _web_resource("new_/scripts/validation.js", "function validate() { return 0; }\n", folder),
        _web_resource("new_/scripts/lookup.js", MINIFIED, folder),
        _web_resource("new_/util.js", "function util() { return 3; }\n", folder),
        _web_resource("new_/app.js", '"use strict";\nfunction start() { }\n', folder),
        _web_resource("new_/missing.js", "function missing() {}\n", folder),
        _web_resource("new_/html/dashboard.html", dashboard, folder, code=1),
        _web_resource("msdyn_/system.js", "function system() {}\n", folder, origin="system"),
        _record(
            "form",
            {"entity": "new_project", "form_type": 2, "name": "Information"},
            folder,
            files=[("form.xml", form_xml.encode("utf-8"))],
        ),
        _record(
            "sitemap", {"unique_name": None}, folder, files=[("sitemap.xml", sitemap_xml.encode())]
        ),
        _record("plugin_assembly", {"name": "Contoso.Plugins"}, folder),
        _record("plugin_assembly", {"name": "Contoso.Workflows"}, folder),
        _record("plugin_assembly", {"name": "Contoso.Missing"}, folder),
        _plugin_type("Contoso.Plugins", "Contoso.Plugins.ProjectCreate", folder),
        _plugin_type("Contoso.Plugins", "Contoso.Plugins.AccountUpdate", folder),
        _plugin_type("Contoso.Plugins", "Contoso.Plugins.Removed", folder),
        _plugin_type("Contoso.Plugins", "Contoso.Plugins.Copied", folder),
        _plugin_type(
            "Contoso.Workflows", "Contoso.Workflows.ApproveActivity", folder, activity=True
        ),
        _plugin_type("Contoso.Missing", "Contoso.Missing.Gone", folder),
    ]
    counts: dict[str, int] = {}
    for record in records:
        counts[record.kind] = counts.get(record.kind, 0) + 1
    manifest = inv.Inventory(
        schema_version=inv.SCHEMA_VERSION,
        source=inv.Source("onprem", ORG, None, "9.1.0.0000"),
        extracted_at="2026-11-01T10:00:00Z",
        cli_version="1.87.0",
        scope=inv.Scope("org", ()),
        publication="published",
        records=tuple(records),
        coverage={k: inv.KindCoverage("complete", 1, n, False, ()) for k, n in counts.items()},
        gaps=(),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")
    return folder


@pytest.fixture
def inventory_dir(tmp_path: Path) -> Path:
    return _write_inventory(tmp_path / "inventory")


def _entry(source_map, kind: str, key: str):
    (found,) = [c for c in source_map.components if (c.kind, c.key) == (kind, key)]
    return found


def test_web_resource_statuses(inventory_dir: Path) -> None:
    source_map = map_source(inventory_dir, REPO)

    project = _entry(source_map, "web_resource", "new_/scripts/project.js")
    # The repo copy has a BOM and CRLF: the #1048 normalization makes the hashes agree.
    assert (project.status, project.files) == (
        "matched",
        ("WebResources/new_/scripts/project.js",),
    )
    stale = _entry(source_map, "web_resource", "new_/scripts/validation.js")
    assert (stale.status, stale.files) == ("stale", ("WebResources/new_/scripts/validation.js",))
    ambiguous = _entry(source_map, "web_resource", "new_/util.js")
    assert (ambiguous.status, ambiguous.files) == (
        "ambiguous",
        ("src/common/util.js", "src/legacy/util.js"),
    )
    missing = _entry(source_map, "web_resource", "new_/missing.js")
    assert (missing.status, missing.files) == ("missing_source", ())
    minified = _entry(source_map, "web_resource", "new_/scripts/lookup.js")
    assert minified.status == "candidate"
    assert minified.files == ("WebResources/new_/scripts/lookup.js",)
    assert minified.reason is not None and "minified" in minified.reason
    generated = _entry(source_map, "web_resource", "new_/app.js")
    assert (generated.status, generated.files) == ("candidate", ("src/app.ts",))
    assert generated.reason is not None and "generated" in generated.reason
    assert _entry(source_map, "web_resource", "new_/html/dashboard.html").status == "matched"
    # A system web resource has no source in an application repository: not mapped.
    assert [c for c in source_map.components if c.key == "msdyn_/system.js"] == []


def test_plugin_statuses_cover_both_namespace_forms_and_a_project_without_assembly_name(
    inventory_dir: Path,
) -> None:
    source_map = map_source(inventory_dir, REPO)

    block = _entry(source_map, "plugin_type", "Contoso.Plugins|Contoso.Plugins.ProjectCreate")
    # A partial class split across two files of one project is still one match.
    assert (block.status, block.files, block.projects) == (
        "matched",
        ("Plugins/ProjectCreate.Helpers.cs", "Plugins/ProjectCreate.cs"),
        ("Plugins/Contoso.Plugins.csproj",),
    )
    # Two full (not partial) copies in one project cannot both build: ambiguous.
    copied = _entry(source_map, "plugin_type", "Contoso.Plugins|Contoso.Plugins.Copied")
    assert (copied.status, copied.files, copied.projects) == (
        "ambiguous",
        ("Plugins/Archive/Copied.cs", "Plugins/Copied.cs"),
        ("Plugins/Contoso.Plugins.csproj",),
    )
    assert copied.reason == (
        "Contoso.Plugins.Copied is declared in several files of a project building Contoso.Plugins"
    )
    # File-scoped namespace, in a project with no AssemblyName: a custom workflow activity.
    activity = _entry(
        source_map, "plugin_type", "Contoso.Workflows|Contoso.Workflows.ApproveActivity"
    )
    assert (activity.status, activity.files, activity.projects) == (
        "matched",
        ("Workflows/ApproveActivity.cs",),
        ("Workflows/Contoso.Workflows.csproj",),
    )
    assert _entry(source_map, "plugin_assembly", "Contoso.Workflows").status == "matched"
    # Declared, but in a project that builds another assembly: never a match.
    moved = _entry(source_map, "plugin_type", "Contoso.Plugins|Contoso.Plugins.AccountUpdate")
    assert (moved.status, moved.files, moved.projects) == (
        "candidate",
        ("Tools/AccountUpdate.cs",),
        ("Tools/Contoso.Tools.csproj",),
    )
    assert moved.reason == (
        "Contoso.Plugins.AccountUpdate is declared in projects building Contoso.Tools, "
        "not Contoso.Plugins"
    )
    # The assembly's project exists, the type does not.
    removed = _entry(source_map, "plugin_type", "Contoso.Plugins|Contoso.Plugins.Removed")
    assert (removed.status, removed.projects) == ("candidate", ("Plugins/Contoso.Plugins.csproj",))
    assert _entry(source_map, "plugin_assembly", "Contoso.Missing").status == "missing_source"
    gone = _entry(source_map, "plugin_type", "Contoso.Missing|Contoso.Missing.Gone")
    assert gone.status == "missing_source"


def test_shared_external_and_unmatched_source(inventory_dir: Path) -> None:
    source_map = map_source(inventory_dir, REPO)

    # The form library and the dashboard's script tag both load project.js.
    project = _entry(source_map, "web_resource", "new_/scripts/project.js")
    assert project.consumers == (
        "form:new_project|2|Information",
        "web_resource:new_/html/dashboard.html",
    )
    assert project.shared
    dashboard = _entry(source_map, "web_resource", "new_/html/dashboard.html")
    assert (dashboard.consumers, dashboard.shared) == (("sitemap:default",), False)
    # One project path spelled in another letter case is the same project; a library
    # outside the checkout is named by its project file.
    plugin_projects = ("Plugins/Contoso.Plugins.csproj", "Workflows/Contoso.Workflows.csproj")
    assert [(p.project, p.assembly, p.referenced_by) for p in source_map.shared_projects] == [
        ("../External/Contoso.External.csproj", "Contoso.External", plugin_projects),
        ("Shared/Contoso.Shared.csproj", "Contoso.Shared", plugin_projects),
    ]
    assert [(e.kind, e.name, e.version, e.used_by) for e in source_map.external] == [
        (
            "nuget",
            "Microsoft.CrmSdk.CoreAssemblies",
            "9.0.2.56",
            ("Plugins/Contoso.Plugins.csproj",),
        ),
        ("script", "https://cdn.example.com/charts.js", None, ("new_/html/dashboard.html",)),
    ]
    # A plug-in class is found beside a mapped one and through the repo's own base
    # class; the abstract base is not deployable. NuGet's packages folder beside the
    # solution is not scanned, a JS workspace folder named packages is. An interface
    # is never registered, and a .resx inside a C# project is a build input.
    assert [(u.path, u.kind, u.type_name) for u in source_map.unmatched_source] == [
        ("Plugins/AccountMerge.cs", "plugin_type", "Contoso.Plugins.AccountMerge"),
        ("Plugins/ProjectCreate.cs", "plugin_type", "Contoso.Plugins.ProjectCreateLegacy"),
        ("Plugins/Unregistered.cs", "plugin_type", "Contoso.Plugins.Unregistered"),
        ("src/orphan.js", "web_resource", None),
        ("web/packages/forms/widget.js", "web_resource", None),
    ]
    summary = source_map.summary()
    assert summary["counts"] == {
        "web_resource": {
            "matched": 2,
            "stale": 1,
            "ambiguous": 1,
            "candidate": 2,
            "missing_source": 1,
        },
        "plugin_assembly": {"matched": 2, "missing_source": 1},
        "plugin_type": {"matched": 2, "ambiguous": 1, "candidate": 2, "missing_source": 1},
    }
    assert summary["shared"] == {"web_resources": 1, "projects": 2}
    assert (summary["external"], summary["unmatched_source"]) == (2, 5)
    assert summary["schema_version"] == 1
    assert summary["inventory"]["org_url"] == ORG


def test_cli_writes_a_deterministic_map_and_touches_no_org(inventory_dir: Path, tmp_path) -> None:
    runner = CliRunner()
    outs = [tmp_path / "one.json", tmp_path / "two.json"]
    for out in outs:
        args = ["--json", "inventory", "map-source", str(inventory_dir), "--repo", str(REPO)]
        result = runner.invoke(cli, [*args, "--out", str(out)])
        assert result.exit_code == 0, result.output
    assert outs[0].read_bytes() == outs[1].read_bytes()
    envelope = json.loads(result.output)
    assert envelope["ok"] is True
    assert envelope["data"]["counts"]["plugin_assembly"] == {"matched": 2, "missing_source": 1}
    assert envelope["data"]["out"] == str(outs[1])
    full = json.loads(outs[0].read_text(encoding="utf-8"))
    assert full["schema_version"] == 1
    assert len(full["components"]) == 16
    assert full["unmatched_source"][3] == {
        "path": "src/orphan.js",
        "kind": "web_resource",
        "type_name": None,
    }


def test_cli_refuses_an_unreadable_inventory(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        cli, ["--json", "inventory", "map-source", str(tmp_path), "--repo", str(REPO)]
    )
    assert result.exit_code == 1
    assert "manifest.json" in json.loads(result.output)["error"]
