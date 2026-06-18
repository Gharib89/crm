# pyright: basic
"""E2E tests for plugin commands."""
from __future__ import annotations

import os

import pytest

from crm.tests.e2e.coverage import covers


# Cloud's first unmanaged Update step may target a custom entity without a 'name'
# attribute (the pre-image filter below uses `attributes="name"`); on-prem crmworx has
# a stable suitable step. Gate to on-prem — the verb stays covered there, which the
# gate's union model accepts.
@pytest.mark.requires_onprem
@covers("plugin register-image", "plugin unregister-image")
def test_image_register_read_unregister_roundtrip(backend, request):
    """register_image -> read back -> unregister_image on a live org.

    Mocked tests cannot catch @odata.bind key casing (the #159 lesson), so
    the POST must hit a real org. Attaches a pre-image to any existing
    unmanaged Update-message step (pre-images are valid in every stage) and
    removes it again; skips when the org has no such step.
    """
    from crm.core import plugin as plugin_mod

    msg = backend.get("sdkmessages", params={
        "$filter": "name eq 'Update'", "$select": "sdkmessageid"})
    msg_rows = msg.get("value", [])
    assert msg_rows, "Update sdkmessage missing from org"
    msg_id = msg_rows[0]["sdkmessageid"]
    steps = backend.get("sdkmessageprocessingsteps", params={
        "$filter": (f"_sdkmessageid_value eq {msg_id} "
                    "and ismanaged eq false"),
        "$select": "sdkmessageprocessingstepid", "$top": "1"})
    step_rows = steps.get("value", [])
    if not step_rows:
        pytest.skip("No unmanaged Update-message plug-in step on this org")
    step_id = step_rows[0]["sdkmessageprocessingstepid"]

    out = plugin_mod.register_image(
        backend, step=step_id, image_type="pre",
        alias=f"e2eimg{os.getpid()}", attributes="name")
    assert out["created"] is True
    iid = out["sdkmessageprocessingstepimageid"]
    assert iid, f"no image id parsed: {out}"

    def _cleanup():
        # Safety net for mid-test failure; the happy path already deleted.
        try:
            backend.delete(f"sdkmessageprocessingstepimages({iid})")
        except Exception:
            pass
    request.addfinalizer(_cleanup)

    got = backend.get(
        f"sdkmessageprocessingstepimages({iid})",
        params={"$select": "name,entityalias,imagetype,"
                           "messagepropertyname,attributes"})
    assert got["imagetype"] == 0
    assert got["messagepropertyname"] == "Target"
    assert got["attributes"] == "name"
    assert got["entityalias"] == f"e2eimg{os.getpid()}"

    deleted = plugin_mod.unregister_image(backend, iid)
    assert deleted["deleted"] is True


@covers("plugin register-webhook")
def test_webhook_register_and_bind_step_roundtrip(backend, request):
    """register_webhook -> register a step bound to it -> read back -> cleanup.

    The serviceendpoint create is self-contained (no signed assembly needed),
    so this runs on any reachable org. It also exercises register_step's
    service-endpoint binding path (eventhandler_serviceendpoint) — a mock
    can't validate the @odata.bind key casing (the #159 lesson), so the POST
    must hit a real org. There is no CLI delete verb for a service endpoint,
    so cleanup is via the backend in a finalizer.
    """
    from crm.core import plugin as plugin_mod

    name = f"e2e webhook {os.getpid()}"
    hook = plugin_mod.register_webhook(
        backend, name=name, url="https://example.com/e2e-hook",
        auth="webhookkey", auth_value="e2e-secret")
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

    got = backend.get(f"serviceendpoints({se_id})",
                      params={"$select": "url,contract,authtype"})
    assert got["contract"] == 8          # Webhook
    assert got["authtype"] == 4          # Webhook Key
    assert got["url"] == "https://example.com/e2e-hook"

    step = plugin_mod.register_step(
        backend, message="Create", entity="account",
        service_endpoint=name, name=f"e2e webhook step {os.getpid()}")
    assert step["created"] is True
    state["step_id"] = step["sdkmessageprocessingstepid"]
    assert state["step_id"], f"no step id parsed: {step}"

    bound = backend.get(
        f"sdkmessageprocessingsteps({state['step_id']})",
        params={"$expand": "eventhandler_serviceendpoint($select=serviceendpointid)"})
    handler = bound.get("eventhandler_serviceendpoint") or {}
    assert handler.get("serviceendpointid", "").lower() == se_id.lower()


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
