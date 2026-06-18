# pyright: basic
"""E2E tests for solution WRITE/lifecycle verbs:

add-component / remove-component / set-version / clone-as-patch /
publish / publish-all / import / import-result / job-status /
uninstall / extract / pack / job-cancel
"""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers

# Note: `solution extract`, `solution pack`, and `solution job-cancel` are recorded
# as un-testable in the central E2E_SKIP registry in crm/tests/e2e/coverage.py.


# ── add-component + remove-component ─────────────────────────────────────────


@covers("solution add-component", "solution remove-component")
def test_add_and_remove_component_lifecycle(
    cli, backend, ephemeral_solution, ephemeral_entity
):
    """Add the session entity to the module solution then remove it.

    Uses the entity's MetadataId (objectid) to call AddSolutionComponent (type=1)
    then verifies via `solution components`, and finally removes via
    RemoveSolutionComponent and confirms it's gone.
    """
    from crm.core import metadata as meta_mod
    from crm.core import solution as sol_mod

    # Resolve the entity's MetadataId — that's the objectid for type=1 (entity).
    try:
        info = meta_mod.entity_info(backend, ephemeral_entity)
    except Exception as exc:
        pytest.skip(f"could not resolve entity MetadataId: {exc}")

    metadata_id = info.get("MetadataId")
    if not metadata_id:
        pytest.skip("entity MetadataId not returned; cannot add component")

    # --- ADD ---
    result = cli([
        "--json", "solution", "add-component",
        "--solution", ephemeral_solution,
        "--type", "entity",
        "--id", metadata_id,
        "--no-add-required",
    ])
    assert result.returncode == 0, f"add-component failed:\n{result.stderr}"
    env = json.loads(result.stdout)
    assert env["ok"], env

    # Confirm the entity component now appears in the solution.
    comps = sol_mod.solution_components(backend, ephemeral_solution)
    entity_ids = {c["objectid"].lower() for c in comps if c["componenttype"] == 1}
    assert metadata_id.lower() in entity_ids, (
        f"entity {metadata_id} not found in solution components after add: "
        f"{entity_ids}"
    )

    # --- REMOVE ---
    # The RemoveSolutionComponent action expects the component's objectid (MetadataId
    # for entities) — the same value used for AddSolutionComponent — NOT the
    # solutioncomponentid PK of the solutioncomponent row.  This is the correct
    # value for the `--id` flag ("Component GUID (objectid)"), verified live on
    # both on-prem v9.1 and Dataverse v9.2.
    result = cli([
        "--json", "solution", "remove-component",
        "--solution", ephemeral_solution,
        "--type", "entity",
        "--id", metadata_id,
        "--yes",
    ])
    assert result.returncode == 0, f"remove-component failed:\n{result.stderr}"
    env = json.loads(result.stdout)
    assert env["ok"], env

    # Confirm removed.
    comps_after = sol_mod.solution_components(backend, ephemeral_solution)
    entity_ids_after = {
        c["objectid"].lower() for c in comps_after if c["componenttype"] == 1
    }
    assert metadata_id.lower() not in entity_ids_after, (
        f"entity {metadata_id} still present in solution components after remove"
    )


# ── set-version ───────────────────────────────────────────────────────────────


@covers("solution set-version")
def test_set_version(cli, backend, ephemeral_solution):
    """set-version updates the solution version; assert via solution info."""
    from crm.core import solution as sol_mod

    # Pick a version distinct from the default 1.0.0.0.
    new_version = "1.0.0.1"

    result = cli([
        "--json", "solution", "set-version", ephemeral_solution,
        "--version", new_version,
    ])
    assert result.returncode == 0, f"set-version failed:\n{result.stderr}"
    env = json.loads(result.stdout)
    assert env["ok"], env

    info = sol_mod.solution_info(backend, ephemeral_solution)
    assert info.get("version") == new_version, (
        f"expected version {new_version!r}, got {info.get('version')!r}"
    )


# ── clone-as-patch ────────────────────────────────────────────────────────────


@covers("solution clone-as-patch")
def test_clone_as_patch(cli, backend, ephemeral_solution):
    """Clone the module solution into a patch; verify patch is created, then delete."""
    from crm.core import solution as sol_mod
    from crm.utils.d365_backend import D365Error

    patch_sol_id: str | None = None
    try:
        result = cli([
            "--json", "solution", "clone-as-patch",
            "--solution", ephemeral_solution,
        ])
        assert result.returncode == 0, (
            f"clone-as-patch failed:\n{result.stderr}\nstdout: {result.stdout}"
        )
        env = json.loads(result.stdout)
        assert env["ok"], env
        data = env["data"]
        assert data.get("cloned") is True
        patch_sol_id = data.get("patch_solutionid")
        assert patch_sol_id, f"patch_solutionid missing from response: {data}"
        # Verify a solution with this id exists on the org.
        sols = sol_mod.list_solutions(backend)
        sol_ids = {s["solutionid"] for s in sols if s.get("solutionid")}
        assert patch_sol_id in sol_ids, (
            f"patch solution {patch_sol_id} not found in org solutions after clone"
        )
    finally:
        # Always clean up the patch — don't rely on ephemeral_solution teardown
        # (the parent fixture uses force=True but a dangling patch would block it).
        if patch_sol_id:
            try:
                backend.delete(f"solutions({patch_sol_id})")
            except D365Error:
                pass


# ── publish-all ───────────────────────────────────────────────────────────────


@covers("solution publish-all")
@pytest.mark.slow
def test_publish_all(cli):
    """publish-all calls PublishAllXml and should succeed."""
    result = cli(["--json", "solution", "publish-all"])
    assert result.returncode == 0, f"publish-all failed:\n{result.stderr}"
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert env["data"].get("published") or env["data"].get("action") == "PublishAllXml"


# ── publish (PublishXml) ──────────────────────────────────────────────────────


@covers("solution publish")
@pytest.mark.slow
def test_publish_entity(cli, ephemeral_entity):
    """publish --xml publishes a single entity; assert ok."""
    xml_payload = (
        f"<importexportxml>"
        f"<entities><entity>{ephemeral_entity}</entity></entities>"
        f"</importexportxml>"
    )
    result = cli([
        "--json", "solution", "publish",
        "--xml", xml_payload,
    ])
    assert result.returncode == 0, f"publish failed:\n{result.stderr}"
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert env["data"].get("published") or env["data"].get("action") == "PublishXml"


# ── import + import-result + job-status ──────────────────────────────────────


@covers("solution import", "solution import-result", "solution job-status")
@pytest.mark.slow
def test_import_round_trip_and_result_and_job_status(
    cli, backend, ephemeral_solution, tmp_path
):
    """Export the module solution to a zip, re-import it, then check import-result
    and job-status on the returned ids.

    On targets where the async import returns no async_operation_id (e.g. sync
    fallback on older on-prem), job-status is skipped at runtime.
    """
    from crm.core import solution as sol_mod

    zip_path = tmp_path / f"{ephemeral_solution}_reimport.zip"

    # Export first (using core directly — export is already covered elsewhere).
    try:
        sol_mod.export_solution(backend, ephemeral_solution, zip_path)
    except Exception as exc:
        pytest.skip(f"export failed, cannot run import round-trip: {exc}")

    assert zip_path.exists(), "export did not produce a zip file"

    # Re-import.
    result = cli([
        "--json", "solution", "import",
        str(zip_path),
        "--yes",
        "--quiet",
    ])
    assert result.returncode == 0, (
        f"import failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    data = env["data"]
    assert data.get("status") == "succeeded", f"import status not succeeded: {data}"

    # --skip-dependency-check must be accepted and still succeed. The freshly
    # exported solution has no product-update dependency block, so the flag is a
    # no-op here; this proves the option wires through to ImportSolution live (#376).
    result_skip = cli([
        "--json", "solution", "import",
        str(zip_path),
        "--skip-dependency-check",
        "--yes",
        "--quiet",
    ])
    assert result_skip.returncode == 0, (
        f"import --skip-dependency-check failed:\n{result_skip.stderr}\n"
        f"stdout: {result_skip.stdout}"
    )
    env_skip = json.loads(result_skip.stdout)
    assert env_skip["ok"], env_skip
    assert env_skip["data"].get("status") == "succeeded", env_skip

    import_job_id = data.get("import_job_id")
    async_op_id = data.get("async_operation_id")

    # --- import-result ---
    assert import_job_id, f"import_job_id missing from import response: {data}"
    result2 = cli(["--json", "solution", "import-result", import_job_id])
    assert result2.returncode == 0, (
        f"import-result failed:\n{result2.stderr}\nstdout: {result2.stdout}"
    )
    env2 = json.loads(result2.stdout)
    assert env2["ok"], env2
    data2 = env2["data"]
    assert data2.get("import_job_id") == import_job_id

    # --- job-status ---
    # async_operation_id is None on on-prem sync-fallback path; skip gracefully.
    if not async_op_id:
        pytest.skip(
            "async_operation_id not returned (sync import fallback on this target); "
            "job-status covered by import_result path above"
        )

    result3 = cli(["--json", "solution", "job-status", async_op_id])
    assert result3.returncode == 0, (
        f"job-status failed:\n{result3.stderr}\nstdout: {result3.stdout}"
    )
    env3 = json.loads(result3.stdout)
    assert env3["ok"], env3
    row = env3["data"]
    # The async operation id in the response should match what we passed.
    assert (
        row.get("asyncoperationid", "").lower() == async_op_id.lower()
        or row.get("AsyncOperationId", "").lower() == async_op_id.lower()
    ), f"asyncoperationid mismatch in job-status response: {row}"


# ── uninstall ─────────────────────────────────────────────────────────────────


@covers("solution uninstall")
def test_uninstall_throwaway_solution(cli, backend):
    """Create a dedicated throwaway solution and uninstall it via CLI.

    Does NOT use ephemeral_solution (that is module-scoped and auto-cleans);
    creates a second throwaway so uninstall is genuinely exercised here.
    """
    import uuid as _uuid
    from crm.core import solution as sol_mod
    from crm.utils.d365_backend import D365Error

    suffix = _uuid.uuid4().hex[:8]
    prefix = f"e2eu{suffix[:3]}"
    pub_name = f"new_e2eupub_{suffix}"
    sol_name = f"new_e2eusol_{suffix}"
    pub_id: str | None = None
    sol_created = False

    try:
        pub = sol_mod.create_publisher(
            backend, name=pub_name, prefix=prefix,
            option_value_prefix=10000 + (int(suffix, 16) % 90000),
        )
        pub_id = pub.get("publisherid")
        sol_mod.create_solution(backend, name=sol_name, publisher_unique_name=pub_name)
        sol_created = True

        # Uninstall via CLI.
        result = cli([
            "--json", "solution", "uninstall",
            "--solution", sol_name,
            "--yes",
        ])
        assert result.returncode == 0, (
            f"uninstall failed:\n{result.stderr}\nstdout: {result.stdout}"
        )
        env = json.loads(result.stdout)
        assert env["ok"], env
        assert env["data"].get("uninstalled") is True

        # Confirm gone.
        try:
            sol_mod.solution_info(backend, sol_name)
            pytest.fail(f"solution {sol_name!r} still exists after uninstall")
        except D365Error as exc:
            assert "not found" in str(exc).lower(), (
                f"unexpected error checking for deleted solution: {exc}"
            )
        sol_created = False  # successfully uninstalled; no need to clean up
    finally:
        if sol_created:
            try:
                sol_mod.uninstall_solution(backend, sol_name, force=True)
            except Exception:
                pass
        if pub_id:
            try:
                backend.delete(f"publishers({pub_id})")
            except Exception:
                pass
