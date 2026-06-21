# pyright: basic
"""E2E tests for ribbon verbs: export / list / add-button + remove / hide-button."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET

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


# ── ribbon hide-button (hide an OOB button reversibly) ────────────────────────


def _composed_ribbon(cli, entity: str) -> ET.Element:
    """Export and parse the composed ribbon XML for ``entity``."""
    r = cli(["--json", "ribbon", "export", entity])
    assert r.returncode == 0, f"ribbon export failed:\n{r.stderr}\n{r.stdout}"
    return ET.fromstring(json.loads(r.stdout)["data"]["ribbonxml"])


def _pick_oob_button(root: ET.Element) -> tuple[str, str]:
    """Return (button_id, command) for an OOB button carrying an Mscrm.* command."""
    for btn in root.iter("Button"):
        bid, cmd = btn.get("Id"), btn.get("Command")
        if bid and cmd and cmd.startswith("Mscrm."):
            return bid, cmd
    pytest.skip("no OOB button with an Mscrm.* command found in composed ribbon")


@covers("ribbon hide-button")
@pytest.mark.slow
def test_ribbon_hide_button_display_rule(
    cli, backend, ephemeral_entity, ephemeral_solution
):
    """Hide an OOB button on ephemeral_entity via the reversible display-rule method,
    then re-read the composed ribbon (RetrieveEntityRibbon) and assert the target's
    command now carries the two always-false platform DisplayRules — asserting the
    parsed value, since customizations.xml is reserialized on the round-trip (T3)."""
    _add_entity_to_solution(backend, ephemeral_solution, ephemeral_entity)

    before = _composed_ribbon(cli, ephemeral_entity)
    target_id, command_id = _pick_oob_button(before)

    hide = cli([
        "--json", "ribbon", "hide-button", ephemeral_entity,
        "--solution", ephemeral_solution,
        "--target-id", target_id,
    ])
    assert hide.returncode == 0, (
        f"ribbon hide-button failed:\n{hide.stderr}\nstdout: {hide.stdout}"
    )
    env = json.loads(hide.stdout)
    assert env["ok"], env
    assert env["data"].get("hidden") == target_id, env["data"]
    assert env["data"].get("method") == "display-rule", env["data"]

    # T3: the composed ribbon's overridden command carries both false DisplayRules.
    after = _composed_ribbon(cli, ephemeral_entity)
    cdef = after.find(f".//CommandDefinition[@Id='{command_id}']")
    assert cdef is not None, f"command {command_id!r} absent from composed ribbon"
    rule_ids = {r.get("Id") for r in cdef.findall("DisplayRules/DisplayRule")}
    assert {"Mscrm.HideOnModern", "Mscrm.ShowOnlyOnModern"} <= rule_ids, (
        f"expected the two always-false rules on {command_id!r}, got {rule_ids}"
    )


@covers("ribbon hide-button")
@pytest.mark.slow
def test_ribbon_hide_button_hide_action_removes_element(
    cli, backend, ephemeral_entity, ephemeral_solution
):
    """Hide an OOB button via the one-way hide-action method, then re-read the
    composed ribbon and assert the element is gone — HideCustomAction removes the
    element from ribbon processing (T3: target absent). Irreversible, but the
    ephemeral entity is torn down after, so the hide does not outlive the test."""
    _add_entity_to_solution(backend, ephemeral_solution, ephemeral_entity)

    before = _composed_ribbon(cli, ephemeral_entity)
    target_id, _ = _pick_oob_button(before)

    hide = cli([
        "--json", "ribbon", "hide-button", ephemeral_entity,
        "--solution", ephemeral_solution,
        "--target-id", target_id,
        "--method", "hide-action", "--yes",
    ])
    assert hide.returncode == 0, (
        f"ribbon hide-button (hide-action) failed:\n{hide.stderr}\nstdout: {hide.stdout}"
    )
    env = json.loads(hide.stdout)
    assert env["ok"], env
    assert env["data"].get("method") == "hide-action", env["data"]

    # T3: the hidden control is absent from the composed ribbon.
    after = _composed_ribbon(cli, ephemeral_entity)
    ids_after = {b.get("Id") for b in after.iter("Button")}
    assert target_id not in ids_after, (
        f"button {target_id!r} still present after hide-action"
    )
# ── ribbon set-rules + add-custom-rule (lifecycle) ────────────────────────────


@covers("ribbon set-rules", "ribbon add-custom-rule")
@pytest.mark.slow
def test_ribbon_set_rules_and_add_custom_rule(
    cli, backend, ephemeral_entity, ephemeral_solution, unique, request, tmp_path
):
    """Add a custom button, then set platform rules on its command and attach a
    custom JS enable rule — verifying both new verbs survive the live
    export → import → publish round-trip and land in the exported ribbon (T3).
    """
    _add_entity_to_solution(backend, ephemeral_solution, ephemeral_entity)

    wr_name = f"new_e2err_{unique}.js"
    js_func = "ns.e2eRuleTest"
    button_label = f"E2ERule{unique}"

    js_src = tmp_path / f"{unique}.js"
    js_src.write_bytes(b"// e2e rule test")
    wr_result = cli([
        "--json", "webresource", "create",
        "--name", wr_name, "--file", str(js_src),
        "--display-name", f"E2E Rule WR {unique}",
    ])
    assert wr_result.returncode == 0, (
        f"webresource create failed:\n{wr_result.stderr}\nstdout: {wr_result.stdout}"
    )
    wr_env = json.loads(wr_result.stdout)
    assert wr_env["ok"], wr_env
    wr_id = wr_env["data"].get("webresourceid")
    assert wr_id, f"webresourceid missing: {wr_env['data']}"

    def _cleanup_wr():
        try:
            backend.delete(f"webresourceset({wr_id})")
        except Exception:
            pass

    request.addfinalizer(_cleanup_wr)

    # ── ADD-BUTTON (creates the CommandDefinition the rules attach to) ─────────
    add_result = cli([
        "--json", "ribbon", "add-button", ephemeral_entity,
        "--solution", ephemeral_solution,
        "--label", button_label, "--location", "form",
        "--webresource", wr_name, "--function", js_func,
        "--param", "PrimaryControl",
    ])
    assert add_result.returncode == 0, (
        f"ribbon add-button failed:\n{add_result.stderr}\nstdout: {add_result.stdout}"
    )
    add_env = json.loads(add_result.stdout)
    assert add_env["ok"], add_env
    button_id = add_env["data"]["button_id"]
    # the CommandDefinition id shares the button's base, suffixed .Command
    command_id = button_id[: -len(".CustomAction")] + ".Command"

    # ── SET-RULES ─────────────────────────────────────────────────────────────
    set_result = cli([
        "--json", "ribbon", "set-rules", ephemeral_entity,
        "--solution", ephemeral_solution,
        "--command-id", command_id,
        "--enable-rule", "Mscrm.SelectionCountExactlyOne",
        "--display-rule", "Mscrm.HideOnModern",
    ])
    assert set_result.returncode == 0, (
        f"ribbon set-rules failed:\n{set_result.stderr}\nstdout: {set_result.stdout}"
    )
    set_env = json.loads(set_result.stdout)
    assert set_env["ok"], set_env
    assert set_env["data"]["enable_rules"] == ["Mscrm.SelectionCountExactlyOne"]

    # ── ADD-CUSTOM-RULE ───────────────────────────────────────────────────────
    rule_result = cli([
        "--json", "ribbon", "add-custom-rule", ephemeral_entity,
        "--solution", ephemeral_solution,
        "--command-id", command_id,
        "--webresource", wr_name, "--function", js_func,
    ])
    assert rule_result.returncode == 0, (
        f"ribbon add-custom-rule failed:\n{rule_result.stderr}\nstdout: {rule_result.stdout}"
    )
    rule_env = json.loads(rule_result.stdout)
    assert rule_env["ok"], rule_env
    rule_id = rule_env["data"]["rule_id"]
    assert rule_id, f"rule_id missing: {rule_env['data']}"

    # ── VERIFY (T3): parse the exported ribbon and assert the command's rule
    #    set matches EXACTLY, in order — no drop, no reorder (asserted by value) ─
    out_file = tmp_path / "ribbon.xml"
    export_result = cli([
        "--json", "ribbon", "export", ephemeral_entity, "--output", str(out_file),
    ])
    assert export_result.returncode == 0, (
        f"ribbon export failed:\n{export_result.stderr}\nstdout: {export_result.stdout}"
    )
    root = ET.fromstring(out_file.read_text(encoding="utf-8"))
    cdef = next((c for c in root.iter("CommandDefinition")
                 if c.get("Id") == command_id), None)
    assert cdef is not None, f"command {command_id!r} absent from exported ribbon"
    enable_refs = [e.get("Id") for e in cdef.findall("EnableRules/EnableRule")]
    display_refs = [d.get("Id") for d in cdef.findall("DisplayRules/DisplayRule")]
    # set-rules set enable=[Mscrm.SelectionCountExactlyOne]; add-custom-rule then
    # appended the custom rule reference — exact ordered set, no drop/reorder.
    assert enable_refs == ["Mscrm.SelectionCountExactlyOne", rule_id], enable_refs
    assert display_refs == ["Mscrm.HideOnModern"], display_refs
