"""`crm inventory assess --source-map --repo`: the plug-in project rule (ADR 0034, #1171)."""

# pyright: basic

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

import jsonschema
import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.core import inventory as inv
from crm.core import inventory_assess as assess
from crm.core.inventory_csproj import detect_csproj, plan_csproj
from crm.tests.test_inventory_convert_csproj import World
from crm.tests.test_inventory_csproj import OLD_STYLE

RULE = "csproj-sdk-net462"
FLOOR_URL = (
    "https://learn.microsoft.com/power-apps/developer/data-platform/supported-customizations"
    "#support-for-net-framework-versions"
)
SDK_REFS = ("Microsoft.Crm.Sdk.Proxy", "Microsoft.Xrm.Sdk")
_SDK_REFERENCE = re.compile(
    r'    <Reference Include="Microsoft\.(Crm|Xrm)\.Sdk.*?</Reference>\n', re.S
)
#: A project on 4.6.2 that takes the SDK from the NuGet package, not from SDK 5.0 HintPaths.
PACKAGE_STYLE = _SDK_REFERENCE.sub("", OLD_STYLE.replace("v4.0", "v4.6.2")).replace(
    "  <ItemGroup>\n    <Compile",
    '  <ItemGroup>\n    <PackageReference Include="Microsoft.CrmSdk.CoreAssemblies"'
    ' Version="9.0.2.60" />\n  </ItemGroup>\n  <ItemGroup>\n    <Compile',
    1,
)


def _project(world: World, name: str = "Contoso.Plugins") -> Path:
    return world.repo / "src" / name / f"{name}.csproj"


def _hits(result, key: str = "Contoso.Plugins") -> list:
    return [f for f in result.findings if f.key == key and f.rule == RULE]


def _assess(world: World, **kw):
    return assess.assess(
        world.inventory, "online", source_map=world.source_map, repo=world.repo, **kw
    )


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


# ── the shared detector ─────────────────────────────────────────────────────


def test_detector_reads_a_below_floor_project_with_sdk_5_references() -> None:
    found = detect_csproj(OLD_STYLE)
    assert found.reason is None
    assert (found.declared, found.below_floor, found.sdk_references) == ("v4.0", True, SDK_REFS)


@pytest.mark.parametrize(
    ("version", "below"),
    [("v4.6.1", True), ("v4.5", True), ("v4.6.2", False), ("v4.7", False), ("v4.8", False)],
)
def test_detector_floor_is_4_6_2(version: str, below: bool) -> None:
    found = detect_csproj(PACKAGE_STYLE.replace("v4.6.2", version))
    assert found.below_floor is below and found.sdk_references == ()


def test_detector_counts_an_absent_framework_version_as_below() -> None:
    text = PACKAGE_STYLE.replace("<TargetFrameworkVersion>v4.6.2</TargetFrameworkVersion>", "")
    found = detect_csproj(text)
    assert (found.declared, found.below_floor) == (None, True)


@pytest.mark.parametrize(
    "empty", ["<TargetFrameworkVersion />", "<TargetFrameworkVersion></TargetFrameworkVersion>"]
)
def test_an_empty_framework_version_is_absent_for_detector_and_retarget(empty: str) -> None:
    text = PACKAGE_STYLE.replace("<TargetFrameworkVersion>v4.6.2</TargetFrameworkVersion>", empty)
    found = detect_csproj(text)
    assert (found.reason, found.declared, found.below_floor) == (None, None, True)
    plan = plan_csproj(text)
    assert plan.patched is not None and "<TargetFramework>net462</TargetFramework>" in plan.patched


def test_detector_finds_nothing_in_a_floor_project_with_package_references() -> None:
    found = detect_csproj(PACKAGE_STYLE)
    assert (found.reason, found.below_floor, found.sdk_references) == (None, False, ())


def test_detector_leaves_an_sdk_style_project_alone() -> None:
    sdk = '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup/></Project>'
    found = detect_csproj(sdk)
    assert (found.reason, found.below_floor, found.sdk_references) == (None, False, ())


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("<Project", "not well-formed XML"),
        ("<Other/>", "not an MSBuild project"),
        (OLD_STYLE.replace("v4.0", "four"), "is not a version"),
        (
            OLD_STYLE.replace("<TargetFrameworkVersion>", '<TargetFrameworkVersion Condition="x">'),
            "under a condition",
        ),
    ],
)
def test_detector_says_why_it_cannot_judge(text: str, reason: str) -> None:
    assert reason in (detect_csproj(text).reason or "")


_MSBUILD = "http://schemas.microsoft.com/developer/msbuild/2003"


def _project_with(framework: str, *references: str, before: str = "") -> str:
    items = "".join(f"    {ref}\n" for ref in references)
    return (
        f'<Project xmlns="{_MSBUILD}" xmlns:m="{_MSBUILD}">\n{before}'
        f"  <PropertyGroup>\n    <TargetFrameworkVersion>{framework}</TargetFrameworkVersion>\n"
        f"  </PropertyGroup>\n  <ItemGroup>\n{items}  </ItemGroup>\n</Project>\n"
    )


@pytest.mark.parametrize(
    ("text", "lines"),
    [
        pytest.param(
            _project_with("v4.6.2", '<!-- <Reference Include="Microsoft.Xrm.Sdk" /> -->'),
            [],
            id="a commented-out reference",
        ),
        pytest.param(
            _project_with(
                "v4.0",
                before="  <!--\n  <PropertyGroup>\n"
                "    <TargetFrameworkVersion>v4.5</TargetFrameworkVersion>\n"
                "  </PropertyGroup>\n  -->\n",
            ),
            [8],
            id="a commented-out framework declaration comes first",
        ),
        pytest.param(
            _project_with("v4.6.2", "<Reference Include='Microsoft.Xrm.Sdk, Version=5.0.0.0' />"),
            [6],
            id="a single-quoted include",
        ),
        pytest.param(
            _project_with("v4.6.2", '<m:Reference Include="Microsoft.Crm.Sdk.Proxy" />'),
            [6],
            id="a prefixed tag",
        ),
    ],
)
def test_detector_sites_agree_with_what_it_counts(text: str, lines: list[int]) -> None:
    found = detect_csproj(text)
    assert len(found.sites) == len(found.sdk_references) + found.below_floor
    assert [line for line, _ in found.sites] == lines


# ── assess reads the mapped projects ────────────────────────────────────────


def test_a_below_floor_project_with_sdk_references_is_a_blocker_naming_both(world: World) -> None:
    result = _assess(world)
    (hit,) = _hits(result)
    assert hit.cls == "blocker" and hit.reason is None
    assert hit.evidence.path == "src/Contoso.Plugins/Contoso.Plugins.csproj"
    assert hit.evidence.target_framework_version == "v4.0"
    assert hit.evidence.sdk_references == SDK_REFS
    assert hit.evidence.fired == ("framework", "sdk_references")


def test_the_finding_names_the_floor_source_under_rules_hit(world: World) -> None:
    body = json.loads(assess.dump_findings(_assess(world)))
    assert FLOOR_URL in body["rules_hit"][RULE]["sources"]
    assert body["rules_hit"][RULE]["class"] == "blocker"


def test_only_the_condition_that_fired_is_named(world: World) -> None:
    _project(world).write_text(OLD_STYLE.replace("v4.0", "v4.8"), encoding="utf-8")
    (sdk_only,) = _hits(_assess(world))
    assert sdk_only.evidence.fired == ("sdk_references",)
    _project(world).write_text(PACKAGE_STYLE.replace("v4.6.2", "v4.5"), encoding="utf-8")
    (framework_only,) = _hits(_assess(world))
    assert framework_only.evidence.fired == ("framework",)
    assert framework_only.evidence.sdk_references == ()


def test_an_absent_framework_version_counts_one_match_and_has_no_location(world: World) -> None:
    text = PACKAGE_STYLE.replace("<TargetFrameworkVersion>v4.6.2</TargetFrameworkVersion>", "")
    _project(world).write_text(text, encoding="utf-8")
    (hit,) = _hits(_assess(world))
    assert (hit.evidence.fired, hit.evidence.target_framework_version) == (("framework",), None)
    assert (hit.evidence.match_count, hit.evidence.locations) == (1, ())


def test_a_floor_project_with_package_references_yields_no_finding(world: World) -> None:
    _project(world).write_text(PACKAGE_STYLE, encoding="utf-8")
    result = _assess(world)
    assert _hits(result) == []
    assert {f.cls for f in result.findings if f.key == "Contoso.Plugins"} == {"clear"}


def test_the_evidence_hash_is_the_normalized_project_and_locates_each_site(world: World) -> None:
    path = _project(world)
    path.write_bytes(path.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))
    (hit,) = _hits(_assess(world))
    lines = OLD_STYLE.lstrip("\ufeff").splitlines()
    want = [i for i, ln in enumerate(lines, 1) if "TargetFrameworkVersion" in ln]
    want += [i for i, ln in enumerate(lines, 1) if 'Include="Microsoft.' in ln]
    assert sorted(line for line, _ in hit.evidence.locations) == sorted(want)
    assert hit.evidence.match_count == 3
    assert (
        hit.evidence.sha256
        == hashlib.sha256(
            path.read_bytes().replace(b"\r\n", b"\n").lstrip(b"\xef\xbb\xbf")
        ).hexdigest()
    )


def test_an_unreadable_project_is_unknown_with_a_reason(world: World) -> None:
    _project(world).unlink()
    (unknown,) = _hits(_assess(world))
    assert (unknown.cls, unknown.evidence) == ("unknown", None)
    assert unknown.reason.startswith("project src/Contoso.Plugins/Contoso.Plugins.csproj")
    assert "unreadable" in unknown.reason


def test_a_non_xml_project_is_unknown_with_a_reason(world: World) -> None:
    _project(world).write_text("this is not xml", encoding="utf-8")
    (unknown,) = _hits(_assess(world))
    assert unknown.cls == "unknown" and "not well-formed XML" in unknown.reason


def test_an_unmapped_assembly_gets_no_project_finding(world: World) -> None:
    result = _assess(world)
    assert _hits(result, "Contoso.Missing") == []
    assert {f.reason for f in result.findings if f.key == "Contoso.Missing"} <= {None}


def test_the_finding_rule_equals_the_convert_ledger_rule(world: World) -> None:
    (hit,) = _hits(_assess(world))
    world.run()
    ledger = json.loads((world.out / "ledger.json").read_text(encoding="utf-8"))
    (entry,) = [e for e in ledger["entries"] if e["key"] == "Contoso.Plugins"]
    assert entry["rule"] == hit.rule == RULE


def test_convert_ignores_the_project_finding(world: World) -> None:
    world.findings.write_text(assess.dump_findings(_assess(world)), encoding="utf-8")
    world.run()
    assert (world.out / "ledger.json").is_file()


# ── nothing changes without the two flags ───────────────────────────────────


def _without_rule(rules: assess.Rules) -> assess.Rules:
    return replace(rules, rules=tuple(r for r in rules.rules if r.id != RULE))


def test_without_the_flags_the_findings_are_what_they_were(world: World) -> None:
    packaged = assess.load_rules()
    new = assess.assess(world.inventory, "online")
    old = assess.assess(world.inventory, "online", rules=_without_rule(packaged))
    assert [f.to_dict() for f in new.findings] == [f.to_dict() for f in old.findings]
    assert new.summary()["counts"] == old.summary()["counts"]
    assert not any(f.rule == RULE for f in new.findings)


def test_the_rule_is_in_the_packaged_file_with_both_targets_and_two_sources() -> None:
    (rule,) = [r for r in assess.load_rules().rules if r.id == RULE]
    assert (rule.kind, rule.cls, rule.targets) == (
        "plugin_assembly",
        "blocker",
        ("onprem-9.1", "online"),
    )
    assert FLOOR_URL in rule.sources and len(rule.sources) >= 2
    assert rule.transform is None


def test_the_on_prem_target_judges_projects_too(world: World) -> None:
    result = assess.assess(
        world.inventory, "onprem-9.1", source_map=world.source_map, repo=world.repo
    )
    assert len(_hits(result)) == 1


def test_repo_without_a_source_map_is_refused(world: World) -> None:
    with pytest.raises(inv.InventoryError, match="--source-map and --repo go together"):
        assess.assess(world.inventory, "online", repo=world.repo)


def test_a_source_map_from_another_inventory_is_refused(world: World, tmp_path: Path) -> None:
    other = json.loads(world.source_map.read_text(encoding="utf-8"))
    other["inventory"]["manifest_sha256"] = "0" * 64
    bad = tmp_path / "other-map.json"
    bad.write_text(json.dumps(other), encoding="utf-8")
    with pytest.raises(inv.InventoryError, match="another inventory"):
        assess.assess(world.inventory, "online", source_map=bad, repo=world.repo)


# ── the format ──────────────────────────────────────────────────────────────


def test_project_findings_round_trip_and_validate(world: World, tmp_path: Path) -> None:
    result = _assess(world)
    text = assess.dump_findings(result)
    schemas = Path(assess.__file__).parent.parent / "schemas"
    jsonschema.validate(
        json.loads(text),
        json.loads((schemas / "compatibility-findings-v1.schema.json").read_text()),
    )
    path = tmp_path / "findings.json"
    path.write_text(text, encoding="utf-8")
    read = assess.read_findings(path)
    (hit,) = [f for f in read.findings if f.rule == RULE and f.key == "Contoso.Plugins"]
    assert hit.evidence == _hits(result)[0].evidence
    assert FLOOR_URL in read.sources[RULE]


def test_an_older_reader_sees_the_project_evidence_as_content_evidence(world: World) -> None:
    (hit,) = _hits(_assess(world))
    data = hit.evidence.to_dict()
    assert {"path", "sha256", "locations", "match_count"} <= set(data)
    assert "field" not in data and "registrations" not in data


def test_the_rules_file_validates_with_the_project_detection() -> None:
    schemas = Path(assess.__file__).parent.parent / "schemas"
    schema = json.loads((schemas / "compatibility-rules-v1.schema.json").read_text())
    jsonschema.validate(json.loads(assess.RULES_PATH.read_text(encoding="utf-8")), schema)


def test_a_project_detection_on_another_kind_is_refused() -> None:
    data = json.loads(assess.RULES_PATH.read_text(encoding="utf-8"))
    (rule,) = [r for r in data["rules"] if r["id"] == RULE]
    rule["kind"] = "web_resource"
    with pytest.raises(inv.InventoryError, match="plugin_project applies to plugin_assembly only"):
        assess.parse_rules(data)


# ── the command ─────────────────────────────────────────────────────────────


def _cli(world: World, *extra: str, out: Path):
    args = ["--json", "inventory", "assess", str(world.inventory), "--target", "online"]
    return CliRunner().invoke(cli, [*args, *extra, "--out", str(out)])


def test_cli_takes_source_map_and_repo(world: World, tmp_path: Path) -> None:
    out = tmp_path / "f.json"
    res = _cli(world, "--source-map", str(world.source_map), "--repo", str(world.repo), out=out)
    assert res.exit_code == 0, res.output
    findings = json.loads(out.read_text(encoding="utf-8"))["findings"]
    assert {f["key"] for f in findings if f["rule"] == RULE} == {
        "Contoso.Plugins",
        "Contoso.Client",
    }


@pytest.mark.parametrize("flag", ["--repo", "--source-map"])
def test_cli_one_flag_without_the_other_is_a_usage_error(
    world: World, tmp_path: Path, flag: str
) -> None:
    value = str(world.repo if flag == "--repo" else world.source_map)
    res = _cli(world, flag, value, out=tmp_path / "f.json")
    assert res.exit_code == 2, res.output
    assert not (tmp_path / "f.json").exists()


def test_cli_refuses_a_source_map_from_another_inventory(world: World, tmp_path: Path) -> None:
    other = json.loads(world.source_map.read_text(encoding="utf-8"))
    other["inventory"]["manifest_sha256"] = "0" * 64
    bad = tmp_path / "other-map.json"
    bad.write_text(json.dumps(other), encoding="utf-8")
    res = _cli(world, "--source-map", str(bad), "--repo", str(world.repo), out=tmp_path / "f.json")
    assert res.exit_code == 1 and "another inventory" in json.loads(res.output)["error"]
