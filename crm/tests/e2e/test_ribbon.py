# pyright: basic
"""E2E tests for ribbon verbs: export / list / add-button + remove."""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


def _add_entity_to_solution(backend, solution: str, entity_logical: str) -> None:
    """Add the entity component to a solution so ribbon edits can target it.

    Uses the entity's MetadataId as the objectid for component type=1 (Entity).
    Silently skips if the entity is already in the solution (duplicate add is a
    no-op on Dataverse and causes a benign error on on-prem that we absorb).
    """
    from crm.core import metadata as meta_mod
    from crm.core import solution as sol_mod

    info = meta_mod.entity_info(backend, entity_logical)
    metadata_id = info.get("MetadataId")
    if not metadata_id:
        pytest.skip(
            f"MetadataId not returned for {entity_logical!r}; "
            "cannot add entity to solution"
        )
    try:
        sol_mod.add_solution_component(
            backend, solution=solution,
            component_type=1, component_id=metadata_id,
            add_required_components=False,
        )
    except Exception:
        # If it's already in the solution the server returns an error; absorb it.
        pass


# ── ribbon export ─────────────────────────────────────────────────────────────


@covers("ribbon export")
def test_ribbon_export_account(cli, tmp_path):
    """Export the composed ribbon XML for 'account' to a file; assert ok + XML."""
    out_file = tmp_path / "account_ribbon.xml"
    result = cli([
        "--json", "ribbon", "export", "account",
        "--output", str(out_file),
    ])
    assert result.returncode == 0, (
        f"ribbon export failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    assert env["data"].get("entity") == "account"

    assert out_file.exists(), f"output file not written: {out_file}"
    xml_text = out_file.read_text(encoding="utf-8")
    assert xml_text.lstrip().startswith("<"), (
        f"exported content does not look like XML: {xml_text[:80]!r}"
    )

    # With no --output, --json must still emit a valid {ok,data} envelope
    # carrying the full ribbon XML in data (issue #349), not bare XML.
    r_stdout = cli(["--json", "ribbon", "export", "account"])
    assert r_stdout.returncode == 0, (
        f"ribbon export (stdout) failed:\n{r_stdout.stderr}\nstdout: {r_stdout.stdout}"
    )
    env_stdout = json.loads(r_stdout.stdout)
    assert env_stdout["ok"], env_stdout
    assert env_stdout["data"].get("entity") == "account"
    assert env_stdout["data"].get("ribbonxml", "").lstrip().startswith("<")


@covers("ribbon export")
def test_ribbon_export_application(cli):
    """--application exports the app-wide ribbon (RetrieveApplicationRibbon) — a
    different Web API function and JSON shape than the per-entity path."""
    r = cli(["--json", "ribbon", "export", "--application"])
    assert r.returncode == 0, (
        f"ribbon export --application failed:\n{r.stderr}\nstdout: {r.stdout}"
    )
    env = json.loads(r.stdout)
    assert env["ok"], env
    assert env["data"].get("application") is True
    assert env["data"].get("ribbonxml", "").lstrip().startswith("<")


# ── ribbon list ───────────────────────────────────────────────────────────────


@covers("ribbon list")
def test_ribbon_list_ephemeral(cli, backend, ephemeral_entity, ephemeral_solution):
    """List custom ribbon buttons in ephemeral_solution for ephemeral_entity.

    The entity must be in the solution for ribbon list to read its RibbonDiffXml.
    A freshly-created solution has no custom buttons so we only assert structure.
    """
    _add_entity_to_solution(backend, ephemeral_solution, ephemeral_entity)

    result = cli([
        "--json", "ribbon", "list", ephemeral_entity,
        "--solution", ephemeral_solution,
    ])
    assert result.returncode == 0, (
        f"ribbon list failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    items = env["data"]
    assert isinstance(items, list), f"expected list, got {type(items)}: {env}"


# ── ribbon add-button + ribbon remove (lifecycle) ─────────────────────────────


@covers("ribbon add-button", "ribbon remove")
@pytest.mark.slow
def test_ribbon_add_and_remove_button(
    cli, backend, ephemeral_entity, ephemeral_solution, unique, request, tmp_path
):
    """Add a JS command-bar button to ephemeral_entity in ephemeral_solution, verify
    it appears in `ribbon list`, then remove it and verify it is gone.

    A minimal JS web resource is created for the test and deleted in a finalizer.
    The entity is added to the solution (idempotent) before testing ribbon writes.
    Both `ribbon add-button` and `ribbon remove` are covered in one lifecycle.
    """
    _add_entity_to_solution(backend, ephemeral_solution, ephemeral_entity)

    wr_name = f"new_e2erb_{unique}.js"
    js_func = "ns.e2eRibbonTest"
    button_label = f"E2ERibbon{unique}"

    # ── CREATE WEBRESOURCE ────────────────────────────────────────────────────
    js_src = tmp_path / f"{unique}.js"
    js_src.write_bytes(b"// e2e ribbon test")
    wr_result = cli([
        "--json", "webresource", "create",
        "--name", wr_name,
        "--file", str(js_src),
        "--display-name", f"E2E Ribbon WR {unique}",
    ])
    assert wr_result.returncode == 0, (
        f"webresource create failed:\n{wr_result.stderr}\nstdout: {wr_result.stdout}"
    )
    wr_env = json.loads(wr_result.stdout)
    assert wr_env["ok"], wr_env
    wr_id = wr_env["data"].get("webresourceid")
    assert wr_id, f"webresourceid missing from create response: {wr_env['data']}"

    def _cleanup_wr():
        try:
            backend.delete(f"webresourceset({wr_id})")
        except Exception:
            pass

    request.addfinalizer(_cleanup_wr)

    # ── ADD-BUTTON ────────────────────────────────────────────────────────────
    add_result = cli([
        "--json", "ribbon", "add-button", ephemeral_entity,
        "--solution", ephemeral_solution,
        "--label", button_label,
        "--location", "form",
        "--webresource", wr_name,
        "--function", js_func,
        "--param", "PrimaryControl",
    ])
    assert add_result.returncode == 0, (
        f"ribbon add-button failed:\n{add_result.stderr}\nstdout: {add_result.stdout}"
    )
    add_env = json.loads(add_result.stdout)
    assert add_env["ok"], add_env
    add_data = add_env["data"]
    actual_button_id = add_data.get("button_id")
    assert actual_button_id, f"button_id missing from add-button response: {add_data}"

    # ── VERIFY BUTTON EXISTS ──────────────────────────────────────────────────
    list_result = cli([
        "--json", "ribbon", "list", ephemeral_entity,
        "--solution", ephemeral_solution,
    ])
    assert list_result.returncode == 0, list_result.stderr
    list_env = json.loads(list_result.stdout)
    assert list_env["ok"], list_env
    button_ids = [b.get("button_id") for b in list_env["data"]]
    assert actual_button_id in button_ids, (
        f"added button {actual_button_id!r} not found in ribbon list: {button_ids}"
    )

    # ── REMOVE ────────────────────────────────────────────────────────────────
    remove_result = cli([
        "--json", "ribbon", "remove", ephemeral_entity,
        "--solution", ephemeral_solution,
        "--button-id", actual_button_id,
        "--yes",
    ])
    assert remove_result.returncode == 0, (
        f"ribbon remove failed:\n{remove_result.stderr}\nstdout: {remove_result.stdout}"
    )
    remove_env = json.loads(remove_result.stdout)
    assert remove_env["ok"], remove_env
    assert remove_env["data"].get("removed") == actual_button_id, (
        f"expected removed={actual_button_id!r}: {remove_env['data']}"
    )

    # ── VERIFY BUTTON GONE ────────────────────────────────────────────────────
    list_after = cli([
        "--json", "ribbon", "list", ephemeral_entity,
        "--solution", ephemeral_solution,
    ])
    assert list_after.returncode == 0, list_after.stderr
    list_after_env = json.loads(list_after.stdout)
    assert list_after_env["ok"], list_after_env
    button_ids_after = [b.get("button_id") for b in list_after_env["data"]]
    assert actual_button_id not in button_ids_after, (
        f"button {actual_button_id!r} still present after remove: {button_ids_after}"
    )
