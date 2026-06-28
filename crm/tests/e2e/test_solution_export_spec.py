# pyright: basic
"""E2E test for `crm solution export-spec` (#613).

Adds a custom entity to a throwaway unmanaged solution, then projects the whole
solution into a merged apply-consumable spec and asserts the entity round-trips
into the spec. Pure-GET projection plus one AddSolutionComponent setup call — runs
on both the on-prem (NTLM) and Dataverse-online targets.
"""
from __future__ import annotations

import json

import yaml

from crm.tests.e2e.coverage import covers


@covers("solution export-spec")
def test_export_spec_projects_solution_entity(
    cli, backend, ephemeral_solution, ephemeral_entity, tmp_path
):
    from crm.core import metadata as meta_mod
    from crm.core import solution as sol_mod

    # Put the session entity into this module's throwaway solution (componenttype 1).
    info = meta_mod.entity_info(backend, ephemeral_entity)
    sol_mod.add_solution_component(
        backend,
        solution=ephemeral_solution,
        component_id=info["MetadataId"],
        component_type=1,
    )

    # 1) Summary envelope (no -o): the added entity is projected; skipped is a list.
    result = cli(["--json", "solution", "export-spec", ephemeral_solution])
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env
    data = env["data"]
    assert data["solution"] == ephemeral_solution
    assert isinstance(data["skipped"], list)
    # schema_name preserves case; ephemeral_entity is the lower-case logical name.
    assert any(ephemeral_entity.lower() == str(s).lower() for s in data["entities"]), (
        f"expected {ephemeral_entity!r} among projected entities, got {data['entities']}"
    )

    # 2) Bare YAML (-o): apply-ready spec — solution dict, no skipped key.
    out = tmp_path / "spec.yaml"
    r2 = cli(["--json", "solution", "export-spec", ephemeral_solution, "-o", str(out)])
    assert r2.returncode == 0, r2.stderr
    written = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert written["solution"] == {"unique_name": ephemeral_solution}
    assert "skipped" not in written
    assert any(
        ephemeral_entity.lower() == str(e.get("schema_name", "")).lower()
        for e in written["entities"]
    ), f"expected {ephemeral_entity!r} in the YAML spec entities"
