"""`crm inventory convert --repo`: the plug-in project retarget lands in the ledger (ADR 0034)."""

# pyright: basic

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory as inv
from crm.core import inventory_assess as assess
from crm.core.inventory_convert import CSPROJ_TRANSFORM, LEDGER_NAME, convert
from crm.core.inventory_packages import build_packages, write_packages
from crm.core.inventory_source_map import dump_source_map, map_source, read_source_map
from crm.tests.test_inventory_assess import _assembly, _record
from crm.tests.test_inventory_csproj import OLD_STYLE

ORG = "https://crm.example.test/Org"
CLIENT_REF = (
    '<Reference Include="Microsoft.Xrm.Client, Version=5.0.0.0">'
    "<HintPath>..\\sdk\\bin\\Microsoft.Xrm.Client.dll</HintPath></Reference>"
)


def _project(name: str, *, extra_reference: str = "") -> str:
    text = OLD_STYLE.replace("Contoso.Plugins", name)
    return text.replace(
        '<Reference Include="System" />', extra_reference + '<Reference Include="System" />'
    )


def _type(assembly: str, folder: Path):
    return _record(
        "plugin_type",
        {"assembly": assembly, "type_name": f"{assembly}.Create", "workflow_activity": False},
        folder,
    )


class World:
    """An inventory of three assemblies against a repository holding two of their projects."""

    def __init__(self, root: Path) -> None:
        self.inventory = root / "inv"
        self.inventory.mkdir()
        records = [
            _assembly("Contoso.Plugins", self.inventory, source_type=0, isolation_mode=2),
            _assembly("Contoso.Client", self.inventory, source_type=0, isolation_mode=2),
            _assembly("Contoso.Missing", self.inventory, source_type=0, isolation_mode=2),
            _type("Contoso.Plugins", self.inventory),
            _type("Contoso.Client", self.inventory),
        ]
        counts: dict[str, int] = {}
        for r in records:
            counts[r.kind] = counts.get(r.kind, 0) + 1
        manifest = inv.Inventory(
            schema_version=inv.SCHEMA_VERSION,
            source=inv.Source("crm2011", ORG, None, "5.0.9690.4376"),
            extracted_at="2026-11-01T10:00:00Z",
            cli_version="1.107.0",
            scope=inv.Scope("org", ()),
            publication="published",
            records=tuple(records),
            coverage={k: inv.KindCoverage("complete", 1, n, False, ()) for k, n in counts.items()},
            gaps=(),
        )
        (self.inventory / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), "utf-8")
        self.repo = root / "repo"
        for name, text in (
            ("Contoso.Plugins", _project("Contoso.Plugins")),
            ("Contoso.Client", _project("Contoso.Client", extra_reference=CLIENT_REF)),
        ):
            folder = self.repo / "src" / name
            folder.mkdir(parents=True)
            (folder / f"{name}.csproj").write_text(text, encoding="utf-8", newline="")
        self.source_map = root / "source-map.json"
        self.source_map.write_text(dump_source_map(map_source(self.inventory, self.repo)), "utf-8")
        self.findings = root / "findings.json"
        self.findings.write_text(
            assess.dump_findings(assess.assess(self.inventory, "online")), encoding="utf-8"
        )
        self.packages = root / "packages"
        write_packages(
            build_packages(
                self.inventory,
                read_source_map(self.source_map),
                None,
                assess.read_findings(self.findings),
            ),
            self.packages,
        )
        self.out = root / "out"

    def run(self, **kw):
        return convert(
            self.inventory,
            self.findings,
            self.packages,
            self.out,
            source_map_path=self.source_map,
            repo=self.repo,
            **kw,
        )


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _entries(out: Path, key: str) -> list[dict]:
    ledger = json.loads((out / LEDGER_NAME).read_text(encoding="utf-8"))
    return [e for e in ledger["entries"] if e["kind"] == "plugin_assembly" and e["key"] == key]


def test_ledger_records_the_project_patch_under_the_assembly_key_and_package(world: World) -> None:
    world.run()
    (entry,) = _entries(world.out, "Contoso.Plugins")
    assert entry["transform"] == entry["rule"] == CSPROJ_TRANSFORM
    assert entry["status"] == "patched" and entry["manual_review"] == []
    assert entry["repo_path"] == "src/Contoso.Plugins/Contoso.Plugins.csproj"
    packages = json.loads((world.packages / "index.json").read_text(encoding="utf-8"))["packages"]
    held = [p["id"] for p in packages if _holds(world.packages, p["id"], "Contoso.Plugins")]
    assert held and entry["package"] == held[0]
    patched = world.out / entry["files"]["patched"]
    assert patched.parent.name == entry["package"]
    root = ET.fromstring(patched.read_text(encoding="utf-8"))
    assert root.get("Sdk") == "Microsoft.NET.Sdk"
    assert (
        (world.out / entry["files"]["diff"])
        .read_text(encoding="utf-8")
        .startswith("--- a/src/Contoso.Plugins/Contoso.Plugins.csproj\n")
    )


def _holds(packages: Path, pid: str, key: str) -> bool:
    return f"plugin_assembly:{key}" in json.dumps(
        json.loads((packages / f"{pid}.json").read_text(encoding="utf-8"))
    )


def test_a_project_referencing_xrm_client_yields_a_manual_review_entry(world: World) -> None:
    world.run()
    (entry,) = _entries(world.out, "Contoso.Client")
    (review,) = entry["manual_review"]
    assert entry["status"] == "patched"
    assert review["rule"] == CSPROJ_TRANSFORM
    assert review["file"] == "src/Contoso.Client/Contoso.Client.csproj"
    assert "Microsoft.Xrm.Client" in review["reason"] and review["line"] is not None


def test_an_unmapped_assembly_has_no_entry(world: World) -> None:
    result = world.run()
    assert _entries(world.out, "Contoso.Missing") == []
    assert {e.key for e in result.entries if e.kind == "plugin_assembly"} == {
        "Contoso.Plugins",
        "Contoso.Client",
    }


def test_a_project_deleted_since_the_map_is_a_manual_review_entry(world: World) -> None:
    (world.repo / "src/Contoso.Plugins/Contoso.Plugins.csproj").unlink()
    world.run()
    (entry,) = _entries(world.out, "Contoso.Plugins")
    assert entry["status"] == "manual-review" and entry["files"] == {}
    assert "unreadable" in entry["manual_review"][0]["reason"]


def test_a_class_library_check_leaves_an_exe_untouched(world: World) -> None:
    path = world.repo / "src/Contoso.Plugins/Contoso.Plugins.csproj"
    path.write_text(path.read_text(encoding="utf-8").replace("Library", "Exe"), encoding="utf-8")
    world.run()
    (entry,) = _entries(world.out, "Contoso.Plugins")
    assert (
        entry["status"] == "manual-review"
        and "class library" in entry["manual_review"][0]["reason"]
    )
    assert not any(world.out.glob("*/*Contoso.Plugins*"))


def test_without_repo_no_project_is_read(world: World) -> None:
    convert(
        world.inventory, world.findings, world.packages, world.out, source_map_path=world.source_map
    )
    assert _entries(world.out, "Contoso.Plugins") == []


def test_repo_without_a_source_map_is_refused(world: World) -> None:
    with pytest.raises(inv.InventoryError, match="--repo needs --source-map"):
        convert(world.inventory, world.findings, world.packages, world.out, repo=world.repo)
    assert not world.out.exists()


def test_cli_takes_repo_and_counts_the_project_patches(world: World) -> None:
    args = ["--json", "inventory", "convert", str(world.inventory)]
    args += ["--findings", str(world.findings), "--packages", str(world.packages)]
    args += ["--source-map", str(world.source_map), "--repo", str(world.repo)]
    res = CliRunner().invoke(cli, args + ["--out", str(world.out)])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)["data"]
    assert data["patched"] == 2 and data["manual_review"] == 1


def test_a_non_utf8_project_is_a_manual_review_entry_not_a_traceback(world: World) -> None:
    path = world.repo / "src/Contoso.Plugins/Contoso.Plugins.csproj"
    path.write_bytes(path.read_bytes().replace(b"Contoso.snk", b"Contoso\xe9.snk"))
    world.run()
    (entry,) = _entries(world.out, "Contoso.Plugins")
    assert entry["status"] == "manual-review" and entry["files"] == {}
    assert "unreadable" in entry["manual_review"][0]["reason"]
