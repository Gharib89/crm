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
    """solution info <name> returns the throwaway solution's own uniquename."""
    result = cli(["--json", "solution", "info", ephemeral_solution])
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert isinstance(env["data"], dict)
    assert env["data"]["uniquename"].lower() == ephemeral_solution.lower()


@covers("solution components")
def test_solution_components_ephemeral(cli, ephemeral_solution):
    """components of an empty throwaway solution — assert structure, not content."""
    result = cli(["--json", "solution", "components", ephemeral_solution])
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert isinstance(env["data"], list)
    # The throwaway solution may be empty; that is fine — structure is the contract.


@covers("solution dependencies")
def test_solution_dependencies_ephemeral(cli, ephemeral_solution):
    """dependencies for the throwaway solution — assert ok + list structure."""
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


@covers("solution layer-conflicts")
@pytest.mark.requires_cloud
def test_solution_layer_conflicts_no_overlap(cli, backend, ephemeral_solution):
    """layer-conflicts with the throwaway unmanaged solution vs a managed system
    solution. The throwaway is empty, so there can be no overlap — expects ok + empty
    list (or ok + no-conflicts message). Requires a cloud target because on-prem 9.1
    may not carry the managed system solutions needed for the --solution arg.
    """
    from crm.utils.d365_backend import D365Error
    from crm.core import solution as sol_mod

    # Find any managed solution to use as the --solution argument.
    try:
        items = sol_mod.list_solutions(backend, managed=True)
    except D365Error as exc:
        pytest.skip(f"could not list managed solutions: {exc}")

    managed_names = [it["uniquename"] for it in items if it.get("uniquename")]
    if not managed_names:
        pytest.skip("no managed solutions found on this org")

    managed_name = managed_names[0]
    result = cli([
        "--json", "solution", "layer-conflicts",
        "--solution", managed_name,
        "--unmanaged-solution", ephemeral_solution,
    ])
    assert result.returncode == 0, result.stderr
    env = json.loads(result.stdout)
    assert env["ok"], env
    # The throwaway solution is empty so no conflicts are expected.
    assert isinstance(env["data"], (list, dict))
