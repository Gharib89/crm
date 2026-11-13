"""A plug-in assembly whose project rules never ran is `unknown`, not `clear` (#1292)."""

# pyright: basic

from __future__ import annotations

from pathlib import Path

import pytest

from crm.core import inventory_assess as assess
from crm.core import inventory_readiness as readiness
from crm.tests.test_inventory_convert_csproj import World

PROJECT_RULES = {
    r.id
    for r in assess.load_rules().rules
    if isinstance(r.detection, assess.PluginProject | assess.PluginSource) and "online" in r.targets
}


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _of(result, key: str) -> list:
    return [f for f in result.findings if f.key == key and f.kind == "plugin_assembly"]


def _not_run(result, key: str) -> list:
    return [f for f in _of(result, key) if f.cls == "unknown" and f.rule in PROJECT_RULES]


def test_no_source_map_leaves_no_assembly_clear_and_names_the_missing_input(world: World) -> None:
    result = assess.assess(world.inventory, "online")
    for key in ("Contoso.Plugins", "Contoso.Client", "Contoso.Missing"):
        assert [f for f in _of(result, key) if f.cls == "clear"] == []
        not_run = _not_run(result, key)
        assert {f.rule for f in not_run} == PROJECT_RULES
        assert all("--source-map" in f.reason and "--repo" in f.reason for f in not_run)


def test_a_source_map_gives_the_unknown_only_to_the_assembly_it_does_not_match(
    world: World,
) -> None:
    result = assess.assess(world.inventory, "online", source_map=world.source_map, repo=world.repo)
    assert _not_run(result, "Contoso.Plugins") == []
    assert _not_run(result, "Contoso.Client") == []
    assert {f.rule for f in _not_run(result, "Contoso.Missing")} == PROJECT_RULES
    assert [f for f in _of(result, "Contoso.Missing") if f.cls == "clear"] == []


def test_readiness_counts_the_unjudged_assemblies_on_each_not_run_gate(
    world: World, tmp_path: Path
) -> None:
    path = tmp_path / "findings.json"
    path.write_text(assess.dump_findings(assess.assess(world.inventory, "online")), "utf-8")
    rows = {r.gate: r for r in readiness.readiness(path).rows}
    for gate in PROJECT_RULES:
        assert (rows[gate].status, rows[gate].components) == ("unknown", 3)
