"""`assess` reads an unmapped plug-in assembly's binary for its target framework and SDK references.

The fixture assemblies are built from `fixtures/assemblies/src` (see its README); the SDK they
reference is a stub named `Microsoft.Xrm.Sdk`, not Microsoft's.
"""

# pyright: basic

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from crm.core import inventory as inv
from crm.core import inventory_assess as assess
from crm.core import inventory_readiness as readiness
from crm.core.inventory_assembly import AssemblyMetadata, read_assembly
from crm.tests.test_inventory_assess import ORG, _record
from crm.tests.test_inventory_convert_csproj import World

RULE = "plugin-binary-sdk-net462"
ASSEMBLIES = Path(__file__).parent / "fixtures" / "assemblies"


def _dll(name: str) -> bytes:
    return (ASSEMBLIES / name).read_bytes()


def _assembly(name: str, folder: Path, payload: bytes | None) -> inv.Record:
    files = [] if payload is None else [("content.dll", payload, False)]
    fields = {"name": name, "source_type": 0, "isolation_mode": 2}
    return _record("plugin_assembly", fields, folder, files=files)


@pytest.fixture
def inventory(tmp_path: Path) -> Path:
    folder = tmp_path / "inv"
    folder.mkdir()
    records = [
        _assembly("Contoso.Legacy", folder, _dll("net40-sdk5.dll")),
        _assembly("Contoso.Current", folder, _dll("net462-sdk9.dll")),
        _assembly("Contoso.Ancient", folder, _dll("net35.dll")),
        _assembly("Contoso.Absent", folder, None),
        _assembly("Contoso.Garbled", folder, b"MZ not a portable executable"),
    ]
    manifest = inv.Inventory(
        schema_version=inv.SCHEMA_VERSION,
        source=inv.Source("crm2011", ORG, None, "5.0.9690.4376"),
        extracted_at="2026-11-01T10:00:00Z",
        cli_version="1.120.0",
        scope=inv.Scope("org", ()),
        publication="published",
        records=tuple(records),
        coverage={"plugin_assembly": inv.KindCoverage("complete", 1, len(records), False, ())},
        gaps=(),
    )
    (folder / inv.MANIFEST_NAME).write_text(inv.dump_manifest(manifest), encoding="utf-8")
    return folder


def _binary(result: assess.AssessResult, key: str) -> list[assess.Finding]:
    return [f for f in result.findings if f.key == key and f.rule == RULE]


def test_read_assembly_returns_the_framework_and_references() -> None:
    meta = read_assembly(_dll("net40-sdk5.dll"))
    assert not isinstance(meta, str)
    assert meta.target_framework == ".NETFramework,Version=v4.0"
    assert ("Microsoft.Xrm.Sdk", "5.0.0.0") in meta.references


def test_read_assembly_names_why_a_payload_is_not_an_assembly() -> None:
    why = read_assembly(b"MZ not a portable executable")
    assert isinstance(why, str) and "not a readable .NET assembly" in why


def test_a_truncated_assembly_is_a_reason_and_logs_nothing(capfd) -> None:
    import logging

    handlers = list(logging.getLogger().handlers)
    why = read_assembly(_dll("net40-sdk5.dll")[:1919])
    assert isinstance(why, str) and "not a readable .NET assembly" in why
    assert logging.getLogger().handlers == handlers
    assert capfd.readouterr().err == ""


@pytest.mark.parametrize("target", assess.TARGETS)
def test_an_old_assembly_is_deprecated_naming_what_was_read(inventory: Path, target: str) -> None:
    (hit,) = _binary(assess.assess(inventory, target), "Contoso.Legacy")
    assert hit.cls == "deprecated"
    assert isinstance(hit.evidence, assess.ProjectEvidence)
    assert hit.evidence.target_framework_version == ".NETFramework,Version=v4.0"
    assert hit.evidence.sdk_references == ("Microsoft.Xrm.Sdk 5.0.0.0",)
    assert hit.evidence.fired == ("framework", "sdk_references")
    assert hit.evidence.path.startswith("artifacts/plugin_assembly/")


def test_an_assembly_with_no_framework_attribute_is_below_the_floor(inventory: Path) -> None:
    (hit,) = _binary(assess.assess(inventory, "online"), "Contoso.Ancient")
    assert isinstance(hit.evidence, assess.ProjectEvidence)
    assert hit.cls == "deprecated" and hit.evidence.fired == ("framework",)
    assert hit.evidence.target_framework_version is None


def test_a_current_assembly_gets_no_finding_from_the_rule(inventory: Path) -> None:
    assert _binary(assess.assess(inventory, "online"), "Contoso.Current") == []


@pytest.mark.parametrize(
    ("key", "reason"),
    [
        ("Contoso.Absent", "no assembly binary in the inventory"),
        ("Contoso.Garbled", "not a readable .NET assembly"),
    ],
)
def test_a_missing_or_unreadable_payload_is_unknown_with_a_reason(
    inventory: Path, key: str, reason: str
) -> None:
    (unknown,) = _binary(assess.assess(inventory, "online"), key)
    assert unknown.cls == "unknown" and reason in (unknown.reason or "")


def test_a_changed_payload_is_unknown(inventory: Path) -> None:
    dll = next((inventory / "artifacts" / "plugin_assembly").glob("contoso.legacy-*"))
    dll.write_bytes(_dll("net462-sdk9.dll"))
    (unknown,) = _binary(assess.assess(inventory, "online"), "Contoso.Legacy")
    assert unknown.cls == "unknown" and "changed since extraction" in (unknown.reason or "")


def test_the_cli_exits_as_before_on_an_unreadable_payload(inventory: Path, tmp_path: Path) -> None:
    from click.testing import CliRunner

    from crm.cli import cli

    out = tmp_path / "findings.json"
    res = CliRunner().invoke(
        cli,
        ["--json", "inventory", "assess", str(inventory), "--target", "online", "--out", str(out)],
    )
    assert res.exit_code == 0, res.output
    found = assess.read_findings(out)
    assert {f.key for f in found.findings if f.rule == RULE} == {
        "Contoso.Legacy",
        "Contoso.Ancient",
        "Contoso.Absent",
        "Contoso.Garbled",
    }


def test_a_mapped_assembly_is_judged_by_its_project_not_its_binary(tmp_path: Path) -> None:
    world = World(tmp_path)
    result = assess.assess(world.inventory, "online", source_map=world.source_map, repo=world.repo)
    for mapped in ("Contoso.Plugins", "Contoso.Client"):
        assert _binary(result, mapped) == []
        assert [f for f in result.findings if f.key == mapped and f.rule == "csproj-sdk-net462"]
    # The map does not match Contoso.Missing, so its binary is read (the World has none).
    assert [f.cls for f in _binary(result, "Contoso.Missing")] == ["unknown"]


def test_readiness_shows_a_hit_as_warn_and_leaves_the_project_gate(
    inventory: Path, tmp_path: Path
) -> None:
    path = tmp_path / "findings.json"
    path.write_text(assess.dump_findings(assess.assess(inventory, "online")), "utf-8")
    rows = {r.gate: r for r in readiness.readiness(path).rows}
    assert rows[RULE].status == "warn" and rows[RULE].components == 2
    assert "registered binary" in rows[RULE].note
    assert rows["csproj-sdk-net462"].status == "unknown"
    assert "--source-map and --repo" in rows["csproj-sdk-net462"].note


def test_the_detection_on_another_kind_is_refused() -> None:
    data = json.loads(assess.RULES_PATH.read_text(encoding="utf-8"))
    rule = next(r for r in data["rules"] if r["id"] == RULE)
    rule["kind"] = "web_resource"
    with pytest.raises(inv.InventoryError, match="plugin_binary applies to plugin_assembly only"):
        assess.parse_rules(data)


def test_dnfile_is_not_imported_until_an_assembly_is_read() -> None:
    code = (
        "import sys, crm, crm.cli, crm.commands.inventory, crm.core.inventory_assess;"
        "assert not [m for m in sys.modules if m.startswith(('dnfile', 'pefile'))], 'eager import'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize(
    ("framework", "cls"),
    [
        (".NETFramework,Version=v4.0,Profile=Client", "deprecated"),
        (".NETFramework,Version=v4.8,Profile=Client", None),
        (".NETStandard,Version=v2.0", "unknown"),
        (".NETFramework,Version=vNext", "unknown"),
    ],
)
def test_a_framework_value_is_read_past_its_profile_and_never_guessed_clear(
    inventory: Path, monkeypatch: pytest.MonkeyPatch, framework: str, cls: str | None
) -> None:
    meta = AssemblyMetadata(framework, (("mscorlib", "4.0.0.0"),))
    monkeypatch.setattr(assess, "read_assembly", lambda data: meta)
    found = _binary(assess.assess(inventory, "online"), "Contoso.Legacy")
    assert [f.cls for f in found] == ([cls] if cls else [])
    if cls == "unknown":
        assert framework in (found[0].reason or "")
