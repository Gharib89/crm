"""`crm inventory assess --source-map --repo`: the plug-in source rules (ADR 0034, #1192)."""

# pyright: basic

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

from crm.core import inventory as inv
from crm.core import inventory_assess as assess
from crm.core.inventory_source_map import dump_source_map, map_source
from crm.tests.test_inventory_convert_csproj import World

SQL, FILES, REGISTRY = (
    "plugin-source-sql-client",
    "plugin-source-filesystem",
    "plugin-source-registry",
)
RULES = (SQL, FILES, REGISTRY)
FOLDER = "src/Contoso.Plugins"

SANDBOX_BREAKS = """using System.Data.SqlClient;
using System.IO;
using Microsoft.Win32;

public class P
{
    public void Execute()
    {
        var c = new SqlConnection("Server=.");
        var t = File.ReadAllText("a.txt");
        var k = Registry.LocalMachine.OpenSubKey("Software");
    }
}
"""


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _write(world: World, name: str, text: str | bytes, folder: str = FOLDER) -> Path:
    path = world.repo / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text if isinstance(text, bytes) else text.encode())
    return path


def _assess(world: World, target: str = "online"):
    return assess.assess(world.inventory, target, source_map=world.source_map, repo=world.repo)


def _hits(result, key: str = "Contoso.Plugins") -> list:
    return [
        f
        for f in result.findings
        if f.key == key and f.rule in RULES and not (f.reason or "").startswith("not run")
    ]


def _by_rule(result) -> dict[str, list]:
    return {r: [f for f in _hits(result) if f.rule == r] for r in RULES}


def test_a_sql_file_and_registry_use_each_yield_a_blocker_pointing_at_the_use(
    world: World,
) -> None:
    _write(world, "P.cs", SANDBOX_BREAKS)
    found = _by_rule(_assess(world))
    for rule, site in ((SQL, (9, 21)), (FILES, (10, 17)), (REGISTRY, (11, 17))):
        (hit,) = found[rule]
        assert (hit.cls, hit.reason) == ("blocker", None)
        assert hit.evidence.path == f"{FOLDER}/P.cs"
        assert hit.evidence.locations == (site,)
        assert hit.evidence.match_count == 1


def test_the_evidence_hash_is_the_normalized_file(world: World) -> None:
    _write(
        world, "P.cs", SANDBOX_BREAKS.replace("\n", "\r\n").encode().join([b"\xef\xbb\xbf", b""])
    )
    (hit,) = _by_rule(_assess(world))[FILES]
    assert hit.evidence.sha256 == hashlib.sha256(SANDBOX_BREAKS.encode()).hexdigest()
    assert hit.evidence.locations == ((10, 17),)


def test_one_finding_per_file_per_rule_counts_every_use(world: World) -> None:
    _write(
        world,
        "A.cs",
        'using System.IO;\nclass A { void M() { File.Delete("a"); File.Delete("b"); } }\n',
    )
    _write(world, "B.cs", 'using System.IO;\nclass B { void M() { Directory.Delete("a"); } }\n')
    hits = _by_rule(_assess(world))[FILES]
    assert [(h.evidence.path, h.evidence.match_count) for h in hits] == [
        (f"{FOLDER}/A.cs", 2),
        (f"{FOLDER}/B.cs", 1),
    ]


@pytest.mark.parametrize(
    "source",
    [
        'using static System.IO.File;\nclass P { void M() { ReadAllText("a"); } }\n',
        'using IOFile = System.IO.File;\nclass P { void M() { IOFile.Exists("a"); } }\n',
        'class P { void M() { System.IO.File.Exists("a"); } }\n',
    ],
    ids=["using-static", "alias", "qualified"],
)
def test_an_imported_alias_or_qualified_use_is_flagged(world: World, source: str) -> None:
    _write(world, "P.cs", source)
    assert len(_by_rule(_assess(world))[FILES]) == 1


def test_the_microsoft_data_sql_client_is_flagged_too(world: World) -> None:
    _write(
        world,
        "P.cs",
        "using Microsoft.Data.SqlClient;\nclass P { void M() { new SqlCommand(); } }\n",
    )
    assert len(_by_rule(_assess(world))[SQL]) == 1


@pytest.mark.parametrize(
    "source",
    [
        "using System.IO;\nclass P { }\n",
        'using System.IO;\nclass P { void M() { /* File.Delete("a"); new SqlConnection(); */ } }\n',
        'using System.IO;\nclass P { string s = "File.Delete(a) Registry.Foo()"; }\n',
        "using System.IO;\nclass File { public static void Delete(string p) {} }\n"
        'class P { void M() { File.Delete("a"); } }\n',
        'using System.IO;\nclass P { void M() { new MemoryStream(); Path.Combine("a", "b"); } }\n',
    ],
    ids=["unused-using", "comment", "string", "local-class", "path-and-memory-stream"],
)
def test_a_sandbox_safe_source_yields_no_finding_from_these_rules(
    world: World, source: str
) -> None:
    _write(world, "P.cs", source)
    result = _assess(world)
    assert _hits(result) == []


def test_the_on_prem_target_yields_no_finding_from_these_rules(world: World) -> None:
    _write(world, "P.cs", SANDBOX_BREAKS)
    assert _hits(_assess(world, "onprem-9.1")) == []


def test_an_unmapped_assembly_gets_no_source_finding(world: World) -> None:
    _write(world, "P.cs", SANDBOX_BREAKS, folder="src/Contoso.Missing")
    assert _hits(_assess(world), "Contoso.Missing") == []


def test_an_ambiguous_assembly_gets_no_source_finding(world: World) -> None:
    _write(world, "P.cs", SANDBOX_BREAKS)
    twin = world.repo / "src" / "Twin" / "Contoso.Plugins.csproj"
    twin.parent.mkdir()
    twin.write_text((world.repo / FOLDER / "Contoso.Plugins.csproj").read_text("utf-8"), "utf-8")
    world.source_map.write_text(
        dump_source_map(map_source(world.inventory, world.repo)), encoding="utf-8"
    )
    assert _hits(_assess(world)) == []


def test_a_file_with_a_syntax_error_is_unknown_and_the_other_files_still_report(
    world: World,
) -> None:
    _write(world, "Bad.cs", "using System.IO;\nclass B { void M( { File.Delete(p); }\n")
    _write(world, "P.cs", SANDBOX_BREAKS)
    result = _assess(world)
    unknown = [f for f in result.findings if f.key == "Contoso.Plugins" and f.cls == "unknown"]
    (bad,) = unknown
    assert (
        bad.rule is None and "Bad.cs" in (bad.reason or "") and "syntax error" in (bad.reason or "")
    )
    assert [f.cls for f in _by_rule(result)[FILES]].count("blocker") == 1


def test_a_file_that_is_not_utf8_is_unknown_with_a_reason(world: World) -> None:
    _write(world, "Latin.cs", b'class L { string s = "\xe9"; }\n')
    result = _assess(world)
    (latin,) = [f for f in result.findings if f.key == "Contoso.Plugins" and f.cls == "unknown"]
    assert "Latin.cs" in (latin.reason or "") and "UTF-8" in (latin.reason or "")


def test_a_file_that_cannot_be_read_is_unknown_with_a_reason(world: World) -> None:
    _write(world, "P.cs", SANDBOX_BREAKS).chmod(0o000)
    try:
        result = _assess(world)
        unknown = [f for f in result.findings if f.key == "Contoso.Plugins" and f.cls == "unknown"]
    finally:
        (world.repo / FOLDER / "P.cs").chmod(0o644)
    if not unknown:
        pytest.skip("the file stays readable (running as root)")
    assert all("unreadable" in (f.reason or "") for f in unknown)


def test_the_files_scanned_are_the_projects_own(world: World) -> None:
    _write(world, "P.cs", "class P { }\n")
    _write(world, "Sub/Deep.cs", 'class D { void M() { System.IO.File.Delete("a"); } }\n')
    _write(world, "bin/Debug/Gen.cs", 'class G { void M() { System.IO.File.Delete("a"); } }\n')
    _write(world, "obj/Gen.cs", 'class G { void M() { System.IO.File.Delete("a"); } }\n')
    _write(world, "Other/Other.csproj", "<Project/>")
    _write(world, "Other/O.cs", 'class O { void M() { System.IO.File.Delete("a"); } }\n')
    paths = [h.evidence.path for h in _by_rule(_assess(world))[FILES]]
    assert paths == [f"{FOLDER}/Sub/Deep.cs"]


def test_a_project_with_no_source_files_is_not_judged(world: World) -> None:
    assert _hits(_assess(world)) == []


def test_without_the_flags_the_rules_are_not_selected(world: World) -> None:
    _write(world, "P.cs", SANDBOX_BREAKS)
    plain = assess.assess(world.inventory, "online")
    assert _hits(plain) == []
    # The rules did not run, so the assembly is `unknown` once per rule, not `clear` (#1292).
    own = [f for f in plain.findings if f.key == "Contoso.Plugins"]
    assert {f.cls for f in own} == {"unknown"} and set(RULES) <= {f.rule for f in own}


def test_convert_writes_nothing_for_these_findings(world: World) -> None:
    _write(world, "P.cs", SANDBOX_BREAKS)
    before = {p: p.read_bytes() for p in world.repo.rglob("*.cs")}
    world.findings.write_text(assess.dump_findings(_assess(world)), encoding="utf-8")
    world.run()
    ledger = json.loads((world.out / "ledger.json").read_text(encoding="utf-8"))
    assert not {e["rule"] for e in ledger["entries"]} & set(RULES)
    assert not [p for p in world.out.rglob("*") if p.suffix == ".cs" or "plugin-source" in p.name]
    assert {p: p.read_bytes() for p in world.repo.rglob("*.cs")} == before


def test_the_findings_round_trip_and_validate(world: World, tmp_path: Path) -> None:
    _write(world, "P.cs", SANDBOX_BREAKS)
    result = _assess(world)
    path = tmp_path / "f.json"
    path.write_text(assess.dump_findings(result), encoding="utf-8")
    assert len(assess.read_findings(path).findings) == len(result.findings)
    schemas = Path(assess.__file__).parent.parent / "schemas"
    schema = json.loads((schemas / "compatibility-findings-v2.schema.json").read_text())
    jsonschema.validate(json.loads(path.read_text(encoding="utf-8")), schema)


# ── the rules ───────────────────────────────────────────────────────────────


def _rules() -> dict[str, assess.Rule]:
    return {r.id: r for r in assess.load_rules().rules}


@pytest.mark.parametrize(
    ("rule", "types"),
    [
        (SQL, {"System.Data.SqlClient": {"SqlConnection", "SqlCommand", "SqlDataAdapter"}}),
        (FILES, {"System.IO": {"File", "Directory", "FileInfo", "DirectoryInfo", "FileStream"}}),
        (REGISTRY, {"Microsoft.Win32": {"Registry", "RegistryKey"}}),
    ],
)
def test_the_packaged_rule_lists_the_issue_types(rule: str, types: dict[str, set[str]]) -> None:
    r = _rules()[rule]
    assert (r.kind, r.cls, r.targets, r.transform) == (
        "plugin_assembly",
        "blocker",
        ("online",),
        None,
    )
    assert any(s.startswith(assess.LEARN) for s in r.sources)
    assert isinstance(r.detection, assess.PluginSource)
    listed = {e.namespace: set(e.types) for e in r.detection.entries}
    assert all(listed.get(ns) == names for ns, names in types.items())


def test_the_rules_file_validates_with_the_source_detection() -> None:
    schemas = Path(assess.__file__).parent.parent / "schemas"
    schema = json.loads((schemas / "compatibility-rules-v1.schema.json").read_text())
    jsonschema.validate(json.loads(assess.RULES_PATH.read_text(encoding="utf-8")), schema)


def _data_with(**detection) -> dict:
    data = json.loads(assess.RULES_PATH.read_text(encoding="utf-8"))
    (rule,) = [r for r in data["rules"] if r["id"] == FILES]
    rule["detection"] = {"type": "plugin_source", **detection}
    return data


def test_a_source_detection_on_another_kind_is_refused() -> None:
    data = _data_with(entries=[{"namespace": "System.IO", "types": ["File"]}])
    (rule,) = [r for r in data["rules"] if r["id"] == FILES]
    rule["kind"] = "web_resource"
    with pytest.raises(inv.InventoryError, match="plugin_source applies to plugin_assembly only"):
        assess.parse_rules(data)


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [{"namespace": "System.IO"}],
        [{"namespace": "", "types": ["File"]}],
        [{"namespace": "System.IO", "types": []}],
    ],
    ids=["no-entries", "no-types", "empty-namespace", "empty-types"],
)
def test_a_source_detection_without_namespace_and_types_is_refused(entries: list) -> None:
    with pytest.raises(inv.InventoryError, match="plugin_source|entries|namespace|types"):
        assess.parse_rules(_data_with(entries=entries))
