# pyright: basic
"""E2E tests for plugin commands."""

from __future__ import annotations

import os
import time
import warnings

import pytest

from crm.tests.e2e.coverage import covers


@pytest.mark.requires_onprem
@covers("plugin register-image", "plugin unregister-image")
def test_image_register_read_unregister_roundtrip(backend, request):
    """register_image -> read back -> unregister_image on a live org.

    Mocked tests cannot catch @odata.bind key casing (the #159 lesson), so
    the POST must hit a real org. Attaches a pre-image to any existing
    unmanaged Update-message step (pre-images are valid in every stage) and
    removes it again; skips when the org has no such step.

    Gated to on-prem: on Dataverse online the first unmanaged Update step may
    target a custom entity without a `name` attribute (the pre-image filter uses
    `attributes="name"`), while on-prem has a stable suitable step. The verb stays
    covered there, which the coverage gate's union model accepts.
    """
    from crm.core import plugin as plugin_mod

    msg = backend.get(
        "sdkmessages", params={"$filter": "name eq 'Update'", "$select": "sdkmessageid"}
    )
    msg_rows = msg.get("value", [])
    assert msg_rows, "Update sdkmessage missing from org"
    msg_id = msg_rows[0]["sdkmessageid"]
    steps = backend.get(
        "sdkmessageprocessingsteps",
        params={
            "$filter": (f"_sdkmessageid_value eq {msg_id} and ismanaged eq false"),
            "$select": "sdkmessageprocessingstepid",
            "$top": "1",
        },
    )
    step_rows = steps.get("value", [])
    if not step_rows:
        pytest.skip("No unmanaged Update-message plug-in step on this org")
    step_id = step_rows[0]["sdkmessageprocessingstepid"]

    out = plugin_mod.register_image(
        backend, step=step_id, image_type="pre", alias=f"e2eimg{os.getpid()}", attributes="name"
    )
    assert out["created"] is True
    iid = out["sdkmessageprocessingstepimageid"]
    assert iid, f"no image id parsed: {out}"

    def _cleanup():
        try:
            backend.delete(f"sdkmessageprocessingstepimages({iid})")
        except Exception:
            pass

    request.addfinalizer(_cleanup)

    got = backend.get(
        f"sdkmessageprocessingstepimages({iid})",
        params={"$select": "name,entityalias,imagetype,messagepropertyname,attributes"},
    )
    assert got["imagetype"] == 0
    assert got["messagepropertyname"] == "Target"
    assert got["attributes"] == "name"
    assert got["entityalias"] == f"e2eimg{os.getpid()}"

    deleted = plugin_mod.unregister_image(backend, iid)
    assert deleted["deleted"] is True


@covers("plugin register-webhook", "plugin set-step-state")
def test_webhook_register_and_bind_step_roundtrip(backend, request):
    """register_webhook -> register a step bound to it -> read back -> cleanup.

    The serviceendpoint create is self-contained (no signed assembly needed),
    so this runs on any reachable org. It also exercises register_step's
    service-endpoint binding path (eventhandler_serviceendpoint) — a mock
    can't validate the @odata.bind key casing (the #159 lesson), so the POST
    must hit a real org. There is no CLI delete verb for a service endpoint,
    so cleanup is via the backend in a finalizer.

    The service endpoint reads back as contract 8 (Webhook) and authtype 4
    (Webhook Key). Passing `secure_configuration` exercises the deep insert: the
    secure config is a separate related record created and bound to the step in
    the same POST, and a mock cannot validate the nav-property casing (the #159
    lesson). The secret is write-only, so it is never echoed in the return value,
    yet the step's secure-config lookup is readable and confirms the link.
    """
    from crm.core import plugin as plugin_mod

    name = f"e2e webhook {os.getpid()}"
    hook = plugin_mod.register_webhook(
        backend,
        name=name,
        url="https://example.com/e2e-hook",
        auth="webhookkey",
        auth_value="e2e-secret",
    )
    assert hook["created"] is True
    se_id = hook["serviceendpointid"]
    assert se_id, f"no serviceendpointid parsed: {hook}"

    state = {"step_id": None}

    def _cleanup():
        if state["step_id"]:
            try:
                backend.delete(f"sdkmessageprocessingsteps({state['step_id']})")
            except Exception:
                pass
        try:
            backend.delete(f"serviceendpoints({se_id})")
        except Exception:
            pass

    request.addfinalizer(_cleanup)

    got = backend.get(f"serviceendpoints({se_id})", params={"$select": "url,contract,authtype"})
    assert got["contract"] == 8
    assert got["authtype"] == 4
    assert got["url"] == "https://example.com/e2e-hook"

    step = plugin_mod.register_step(
        backend,
        message="Create",
        entity="account",
        service_endpoint=name,
        name=f"e2e webhook step {os.getpid()}",
        secure_configuration="e2e-secret-cfg",
    )
    assert step["created"] is True
    assert "e2e-secret-cfg" not in str(step)
    state["step_id"] = step["sdkmessageprocessingstepid"]
    assert state["step_id"], f"no step id parsed: {step}"

    bound = backend.get(
        f"sdkmessageprocessingsteps({state['step_id']})",
        params={
            "$expand": "eventhandler_serviceendpoint($select=serviceendpointid)",
            "$select": "_sdkmessageprocessingstepsecureconfigid_value",
        },
    )
    handler = bound.get("eventhandler_serviceendpoint") or {}
    assert handler.get("serviceendpointid", "").lower() == se_id.lower()
    assert bound.get("_sdkmessageprocessingstepsecureconfigid_value"), (
        f"secure-config record not linked to step: {bound}"
    )

    disabled = plugin_mod.set_step_state(backend, step=state["step_id"], enable=False)
    assert disabled["enabled"] is False

    enabled = plugin_mod.set_step_state(backend, step=state["step_id"], enable=True)
    assert enabled["enabled"] is True


@covers("plugin list-types")
def test_plugin_list_types_returns_list(cli):
    """list-types returns a list (possibly empty on orgs with no custom assemblies).

    The prior skip claimed a registered assembly was required for the listing
    to be meaningful — but the command is valid and correctly shaped with zero
    results. Asserting structure (not non-empty) makes the test org-agnostic.
    """
    import json as _json

    result = cli(["--json", "plugin", "list-types"])
    data = _json.loads(result.stdout)
    assert data["ok"] is True, f"plugin list-types failed: {data}"
    assert isinstance(data["data"], list), (
        f"expected data to be a list, got {type(data['data'])}: {data}"
    )


@covers("plugin list-types")
def test_plugin_list_types_in_use_never_calls_an_absent_record_unused(cli):
    """Every row is labelled; a type is unused only when every step is disabled or absent."""
    import json as _json

    plain = _json.loads(cli(["--json", "plugin", "list-types"]).stdout)["data"]
    rows = _json.loads(cli(["--json", "plugin", "list-types", "--in-use"]).stdout)["data"]
    assert len(rows) <= len(plain)
    assert {r["usage"]["state"] for r in rows} <= {"used", "unknown"}
    assert all(r["usage"]["reason"] for r in rows if r["usage"]["state"] == "unknown")


@covers(
    "plugin register-assembly",
    "plugin register-type",
    "plugin unregister-step",
    "plugin unregister-assembly",
)
def test_assembly_register_step_unregister_lifecycle(
    cli, plugin_assembly, backend, request, ephemeral_solution
):
    """register-assembly -> register-type -> register-step -> unregister-* lifecycle.

    The `plugin_assembly` fixture builds a signed no-op IPlugin from committed C#
    source; this drives the whole assembly lifecycle through the CLI and asserts
    each command's emit envelope. No step ever fires — a step is registered against
    the no-op plug-in type and removed again, so registration is proven without
    executing plug-in code. The assembly is registered in the sandbox (isolation
    mode 2, which the cloud target mandates); the public key token comes from the
    built assembly, so it matches the content the platform validates. The org is
    left clean: the test unregisters the step then the assembly, with a finalizer
    safety net for a mid-test failure. The finalizer reads a mutable holder so it
    no-ops once the happy path has unregistered the assembly (it never
    double-deletes on success), and it surfaces a genuine leak as a warning, as the
    `ephemeral_solution` fixture does.

    The plug-in type is registered explicitly: unlike the Plug-in Registration
    Tool, which reflects the assembly client-side and creates plugintype rows, a
    raw Web API upload of the assembly content does not auto-register plug-in
    types and `register-assembly` does no reflection, so the type must exist
    before a step can bind to it. The platform cascade-deletes it when the
    assembly is unregistered, so it needs no separate teardown. Both friendlyname
    and name default to the type name when their flags are omitted, because a null
    name yields an empty classic-designer label (issue #866); the persisted row is
    checked for the defaulted name. The test then polls until the type is
    queryable before register-step resolves it by name, guarding a
    read-after-write lag that would otherwise surface as register-step 404ing.

    The step is registered with `--secure-configuration` to exercise the deep
    insert: the secure config is a separate related record linked from the step,
    created and bound in the same POST. The value is write-only (never returned or
    echoed in the command output), so success is proven by the step's
    secure-config lookup being populated, not by reading the secret back.
    """
    import json as _json

    asm = plugin_assembly

    result = cli(
        [
            "--json",
            "plugin",
            "register-assembly",
            asm.dll,
            "--name",
            asm.assembly_name,
            "--public-key-token",
            asm.public_key_token,
            "--version",
            "1.0.0.0",
            "--isolation-mode",
            "sandbox",
            "--solution",
            ephemeral_solution,
        ],
        check=False,
    )
    assert result.returncode == 0, f"register-assembly failed: {result.stderr}\n{result.stdout}"
    data = _json.loads(result.stdout)
    assert data["ok"] is True, f"register-assembly not ok: {data}"
    assert data["data"]["created"] is True
    assembly_id = data["data"]["pluginassemblyid"]
    assert assembly_id, f"no pluginassemblyid parsed: {data}"
    row = backend.get(f"pluginassemblies({assembly_id})", params={"$select": "name"})
    assert data["data"]["name"] == row["name"], f"reported name != stored name: {data}"

    state = {"assembly_id": assembly_id}

    def _cleanup():
        if not state["assembly_id"]:
            return
        result = cli(
            ["--json", "plugin", "unregister-assembly", state["assembly_id"], "--yes"], check=False
        )
        if result.returncode != 0:
            warnings.warn(
                f"e2e cleanup of plugin assembly {state['assembly_id']} failed: {result.stderr}",
                stacklevel=2,
            )

    request.addfinalizer(_cleanup)

    type_result = cli(
        [
            "--json",
            "plugin",
            "register-type",
            "--assembly",
            asm.assembly_name,
            "--type",
            asm.type_name,
            "--solution",
            ephemeral_solution,
        ],
        check=False,
    )
    assert type_result.returncode == 0, (
        f"register-type failed: {type_result.stderr}\n{type_result.stdout}"
    )
    type_data = _json.loads(type_result.stdout)
    assert type_data["ok"] is True, f"register-type not ok: {type_data}"
    assert type_data["data"]["created"] is True
    assert type_data["data"]["plugintypeid"], f"no plugintypeid parsed: {type_data}"
    assert type_data["data"]["friendlyname"] == asm.type_name
    assert type_data["data"]["name"] == asm.type_name

    type_filter = f"typename eq '{asm.type_name}' and _pluginassemblyid_value eq {assembly_id}"
    for _ in range(10):
        rows = backend.get(
            "plugintypes",
            params={"$filter": type_filter, "$select": "plugintypeid,name"},
        ).get("value", [])
        if rows:
            assert rows[0]["name"] == asm.type_name
            break
        time.sleep(2)
    else:
        pytest.fail(f"seeded plug-in type {asm.type_name!r} not queryable after create")

    step_result = cli(
        [
            "--json",
            "plugin",
            "register-step",
            "--message",
            "Create",
            "--entity",
            "account",
            "--plugin-type",
            asm.type_name,
            "--assembly",
            asm.assembly_name,
            "--name",
            f"{asm.assembly_name} create step",
            "--configuration",
            "unsecure-cfg",
            "--secure-configuration",
            "e2e-secret-cfg",
            "--solution",
            ephemeral_solution,
        ],
        check=False,
    )
    assert step_result.returncode == 0, (
        f"register-step failed: {step_result.stderr}\n{step_result.stdout}"
    )
    step_data = _json.loads(step_result.stdout)
    assert step_data["ok"] is True, f"register-step not ok: {step_data}"
    assert step_data["data"]["created"] is True
    step_id = step_data["data"]["sdkmessageprocessingstepid"]
    assert step_id, f"no step id parsed: {step_data}"
    assert "e2e-secret-cfg" not in step_result.stdout
    step_row = backend.get(
        f"sdkmessageprocessingsteps({step_id})",
        params={"$select": "_sdkmessageprocessingstepsecureconfigid_value"},
    )
    assert step_row.get("_sdkmessageprocessingstepsecureconfigid_value"), (
        f"secure-config record not linked to step: {step_row}"
    )

    unstep_result = cli(["--json", "plugin", "unregister-step", step_id, "--yes"], check=False)
    assert unstep_result.returncode == 0, (
        f"unregister-step failed: {unstep_result.stderr}\n{unstep_result.stdout}"
    )
    unstep = _json.loads(unstep_result.stdout)
    assert unstep["ok"] is True, f"unregister-step not ok: {unstep}"
    assert unstep["data"]["deleted"] is True

    unasm_result = cli(
        ["--json", "plugin", "unregister-assembly", assembly_id, "--yes"], check=False
    )
    assert unasm_result.returncode == 0, (
        f"unregister-assembly failed: {unasm_result.stderr}\n{unasm_result.stdout}"
    )
    unasm = _json.loads(unasm_result.stdout)
    assert unasm["ok"] is True, f"unregister-assembly not ok: {unasm}"
    assert unasm["data"]["deleted"] is True
    assert unasm["data"]["steps_deleted"] == 0
    state["assembly_id"] = None
