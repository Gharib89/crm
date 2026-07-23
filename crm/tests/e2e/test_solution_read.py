# pyright: basic
"""E2E tests for solution READ verbs:
list / info / components / dependencies / validate / layer-conflicts.
"""

from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


@covers("solution list")
def test_solution_list_returns_non_empty(cli):
    """Every org has system solutions — list must return at least one."""
    result = cli(["--json", "solution", "list"])
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert isinstance(env["data"], list)
    assert len(env["data"]) > 0, "expected at least one system solution"


@covers("solution info")
def test_solution_info_ephemeral(cli, ephemeral_solution):
    """Solution info <name> returns the throwaway solution's own uniquename."""
    result = cli(["--json", "solution", "info", ephemeral_solution])
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert isinstance(env["data"], dict)
    assert env["data"]["uniquename"].lower() == ephemeral_solution.lower()


@covers("solution components")
def test_solution_components_ephemeral(cli, ephemeral_solution):
    """Components of an empty throwaway solution — assert structure, not content."""
    result = cli(["--json", "solution", "components", ephemeral_solution])
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert isinstance(env["data"], list)
    # The throwaway solution may be empty; that is fine — structure is the contract.


@covers("solution components")
def test_solution_components_resolve(cli, backend, ephemeral_solution, ephemeral_entity):
    """`components --resolve` enriches each row with a friendly name + behavior label.

    Add the session entity (type 1) to the throwaway solution, resolve, and assert
    the entity's row carries the resolved LogicalName plus the enrichment keys, then
    remove the component again so the shared module solution stays empty for the
    other read tests. Exercises the live objectid → name batch-resolution path.
    """
    from crm.core import metadata as meta_mod
    from crm.core import solution as sol_mod

    try:
        info = meta_mod.entity_info(backend, ephemeral_entity)
    except Exception as exc:
        pytest.skip(f"could not resolve entity MetadataId: {exc}")
    metadata_id = info.get("MetadataId")
    if not isinstance(metadata_id, str):
        pytest.skip("entity MetadataId not returned; cannot add component")

    sol_mod.add_solution_component(
        backend,
        solution=ephemeral_solution,
        component_type=1,
        component_id=metadata_id,
        add_required_components=False,
    )
    try:
        result = cli(["--json", "solution", "components", ephemeral_solution, "--resolve"])
        assert result.returncode == 0, result.stderr
        env = json.loads(result.stdout)
        assert env["ok"], env
        assert isinstance(env["data"], list)
        row = next(
            (
                r
                for r in env["data"]
                if r.get("componenttype") == 1
                and str(r.get("objectid", "")).lower() == metadata_id.lower()
            ),
            None,
        )
        assert row is not None, f"entity component not found in resolved output: {env['data']}"
        # Every enriched row carries these keys; the entity resolves to its LogicalName.
        assert "rootcomponentbehaviorname" in row, row
        assert row.get("name") == ephemeral_entity, row
    finally:
        sol_mod.remove_solution_component(
            backend,
            solution=ephemeral_solution,
            component_type=1,
            component_id=metadata_id,
        )


@covers("solution dependencies")
def test_solution_dependencies_ephemeral(cli, ephemeral_solution):
    """Dependencies for the throwaway solution — assert ok + list structure."""
    result = cli(["--json", "solution", "dependencies", ephemeral_solution])
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env
    # dependencies returns {solution, count, blockers} under data
    assert isinstance(env["data"], dict)
    assert "count" in env["data"] or "blockers" in env["data"]


@covers("solution validate")
def test_solution_validate_exported_zip(cli, backend, ephemeral_solution, tmp_path):
    """Export the throwaway solution to a zip, then validate it offline."""
    from crm.core import solution as sol_mod

    zip_path = tmp_path / f"{ephemeral_solution}.zip"
    try:
        sol_mod.export_solution(backend, ephemeral_solution, zip_path)
    except Exception as exc:
        pytest.skip(f"export failed, cannot validate: {exc}")

    result = cli(["--json", "solution", "validate", str(zip_path)])
    # validate exits 0 when valid, non-zero on error-severity findings; an empty
    # unmanaged solution should always be valid.
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert env["data"].get("valid") is True


@covers("solution validate")
def test_solution_validate_against_org_version_ok(cli, backend, ephemeral_solution, tmp_path):
    """Validate --against-org runs the #325 package-version compatibility check.

    A solution exported FROM this org necessarily carries a package version <= the
    org's own version, so the check is a no-op pass here (the on-prem leg is where
    a cloud-exported v9.2 package would trip the v9.1 ceiling, 0x80048068). This
    leg proves the check runs live and does not false-reject the equal/older case.
    """
    from crm.core import solution as sol_mod

    zip_path = tmp_path / f"{ephemeral_solution}_against.zip"
    try:
        sol_mod.export_solution(backend, ephemeral_solution, zip_path)
    except Exception as exc:
        pytest.skip(f"export failed, cannot validate: {exc}")

    result = cli(["--json", "solution", "validate", str(zip_path), "--against-org"])
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert "package-version" in env["data"].get("checks_run", [])
    assert env["data"].get("valid") is True
    # the self-exported package is not newer than its own org → no error finding
    assert not [
        f
        for f in env["data"].get("findings", [])
        if f["check"] == "package-version" and f["severity"] == "error"
    ]


@covers("solution missing-components")
def test_solution_missing_components_self_exported(cli, backend, ephemeral_solution, tmp_path):
    """Export the throwaway solution then check its missing components against the same
    org. A self-exported zip is guaranteed to need nothing from the exporting org —
    the result must be an empty list with ok=true and meta.count=0.
    """
    from crm.core import solution as sol_mod

    zip_path = tmp_path / f"{ephemeral_solution}_mc.zip"
    try:
        sol_mod.export_solution(backend, ephemeral_solution, zip_path)
    except Exception as exc:
        pytest.skip(f"export failed, cannot check missing-components: {exc}")

    result = cli(["--json", "solution", "missing-components", str(zip_path)])
    assert result.returncode == 0, (
        f"solution missing-components failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert isinstance(env["data"], list), (
        f"expected data to be a list, got: {type(env['data'])}: {env}"
    )
    # A solution exported from this org has no missing dependencies on this org.
    assert env["data"] == [], (
        f"expected no missing components for a self-exported solution, got: {env['data']}"
    )
    assert env.get("meta", {}).get("count") == 0, f"expected meta.count=0, got: {env.get('meta')}"


@covers("solution layer-conflicts")
@pytest.mark.requires_cloud
def test_solution_layer_conflicts_no_overlap(cli, backend, ephemeral_solution):
    """layer-conflicts with the throwaway unmanaged solution vs a managed system
    solution. The throwaway is empty, so there can be no overlap — expects ok + empty
    list (or ok + no-conflicts message). Requires a cloud target because on-prem 9.1
    may not carry the managed system solutions needed for the --solution arg.
    """
    from crm.core import solution as sol_mod
    from crm.utils.d365_backend import D365Error

    # Find any managed solution to use as the --solution argument.
    try:
        items = sol_mod.list_solutions(backend, managed=True)
    except D365Error as exc:
        pytest.skip(f"could not list managed solutions: {exc}")

    managed_names = [it["uniquename"] for it in items if it.get("uniquename")]
    if not managed_names:
        pytest.skip("no managed solutions found on this org")

    managed_name = managed_names[0]
    result = cli(
        [
            "--json",
            "solution",
            "layer-conflicts",
            "--solution",
            managed_name,
            "--unmanaged-solution",
            ephemeral_solution,
        ]
    )
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env
    # The throwaway solution is empty so no conflicts are expected.
    assert isinstance(env["data"], (list, dict))
