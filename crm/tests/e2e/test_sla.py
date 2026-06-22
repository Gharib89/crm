# pyright: basic
"""E2E tests for sla verbs: create, add-kpi, activate.

`sla activate` strategy:
  1. Try to create a minimal draft SLA (entity: `slas`, primaryentitytypecode
     set to a common entity like 'contact') via the backend directly.
  2. Run `sla activate` against it.
  3. Delete the SLA in a finalizer.

`sla create` + `sla add-kpi` strategy (one Customer-Service-provisioned
lifecycle, #511): ensure the target entity (`incident`) is SLA-enabled (a
publish-requiring metadata flip), create the SLA and attach a KPI item via the
CLI, assert both envelopes, then delete the SLA and restore IsSLAEnabled.

SLA creation via the Web API may be blocked on some orgs (e.g. if Case
Management / SLA feature is not provisioned).  The `activate` test probes the
POST and skips at runtime if the org rejects it; the create/add-kpi test probes
the `incident` entity metadata and skips with setup instructions when Customer
Service is absent (see ADR 0012 / #503).

The backing-workflow pattern (slaitem.workflowid) means a freshly created SLA
with no items has no workflows to activate, so `sla activate` proceeds directly
to flipping the SLA's statecode.  This still exercises the full code path in
crm.core.sla.activate_sla (fetch plan + sla PATCH).
"""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


def _create_draft_sla(backend, name: str) -> str | None:
    """POST a minimal draft SLA; return its slaid or None if the org rejects it."""
    from crm.utils.d365_backend import D365Error, as_dict
    body = {
        "name": name,
        "applicablefrom": "createdon",
        "slakpifailuretime": 1,
        "primaryentitytypecode": "contact",
    }
    try:
        result = as_dict(backend.post("slas", json_body=body))
    except D365Error:
        return None
    if result.get("_dry_run"):
        return None
    entity_id_url = result.get("_entity_id_url") or ""
    import re
    m = re.search(r"slas\(([0-9a-fA-F-]{36})\)", entity_id_url)
    return m.group(1) if m else None


# ── sla activate ──────────────────────────────────────────────────────────────


@covers("sla activate")
@pytest.mark.slow
def test_sla_activate(backend, cli, request, unique):
    """Create a throwaway draft SLA, activate it via CLI, clean up.

    Skips at runtime if SLA creation is not supported on this org (SLA feature
    not provisioned or Web API write blocked).  A newly created SLA with no
    sla items has no backing workflows; activate_sla proceeds directly to the
    statecode PATCH, which exercises the full command path.
    """
    sla_name = f"E2E SLA {unique}"
    sla_id: list[str] = []

    def _cleanup():
        if sla_id:
            try:
                backend.delete(f"slas({sla_id[0]})")
            except Exception:
                pass

    request.addfinalizer(_cleanup)

    created = _create_draft_sla(backend, sla_name)
    if created is None:
        pytest.skip(
            "SLA creation via the Web API is not supported on this org "
            "(SLA/Case Management feature not provisioned or POST blocked). "
            "Cannot exercise sla activate without a throwaway SLA."
        )
    sla_id.append(created)

    result = cli(["--json", "sla", "activate", created])
    assert result.returncode == 0, (
        f"sla activate failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], (
        f"sla activate returned ok=False: {env}"
    )
    data = env["data"]
    assert data.get("sla_activated") is True, (
        f"expected sla_activated=True: {data}"
    )
    assert data.get("sla_id") == created, (
        f"sla_id mismatch: {data}"
    )

    # Verify the SLA is now active via direct GET.
    from crm.utils.d365_backend import as_dict
    row = as_dict(backend.get(f"slas({created})", params={"$select": "statecode"}))
    assert row.get("statecode") == 1, (
        f"SLA statecode not 1 after activate; got {row.get('statecode')}"
    )


# ── sla create + sla add-kpi (CS-provisioned lifecycle) ─────────────────────


def _is_sla_enabled(md) -> bool:
    """Read the effective IsSLAEnabled flag from an EntityDefinitions row.

    It is a BooleanManagedProperty (a ``{"Value": bool}`` object) but tolerate a
    bare bool too — mirrors crm.core.sla._sla_enabled_value without importing a
    private symbol."""
    raw = md.get("IsSLAEnabled") if isinstance(md, dict) else None
    if isinstance(raw, dict):
        return bool(raw.get("Value"))
    return bool(raw)


# Per-KPI condition fragments mirror the wire-level unit tests
# (crm/tests/test_sla.py): a "when applicable" filter and a "success" filter on
# the SLA's primary entity.
_KPI_APPLICABLE_WHEN = (
    '<fetch><entity name="incident"><filter>'
    '<condition attribute="prioritycode" operator="eq" value="1"/>'
    "</filter></entity></fetch>"
)
_KPI_SUCCESS = (
    '<fetch><entity name="incident"><filter>'
    '<condition attribute="statecode" operator="eq" value="1"/>'
    "</filter></entity></fetch>"
)


@covers("sla create", "sla add-kpi")
@pytest.mark.requires_cloud
@pytest.mark.slow
def test_sla_create_and_add_kpi_lifecycle(backend, cli, request, unique):
    """`sla create` then `sla add-kpi` against a CS-provisioned org.

    Ensures the target entity (`incident`) is SLA-enabled (a publish-requiring
    metadata flip), creates an SLA via the CLI, attaches a KPI item with valid
    per-KPI FetchXML conditions, asserts both envelopes, then tears down — deletes
    the SLA (its items cascade) and restores the entity's original IsSLAEnabled.

    Skips with setup instructions when Customer Service is not provisioned (the
    `incident` entity is absent — SLAs need the `slas` table CS installs), so it
    runs only on a `--profile agent-cs-trial` leg and skips on the general cloud
    org. See ADR 0012 / #503.
    """
    from crm.core import metadata_update
    from crm.utils.d365_backend import D365Error

    entity = "incident"
    # Probe the target entity's metadata: a 404 (entity absent) is the clean
    # "CS-not-provisioned" signal (no `incident`, hence no `slas` table to POST).
    # Re-raise any other D365Error (auth/throttle/transport) so a real breakage
    # surfaces instead of being masked as a skip.
    try:
        md = backend.get(
            f"EntityDefinitions(LogicalName='{entity}')",
            params={"$select": "LogicalName,IsSLAEnabled"},
        )
    except D365Error as exc:
        if getattr(exc, "status", None) != 404:
            raise
        pytest.skip(
            "Customer Service is not provisioned on this org (the `incident` "
            "entity is absent), so SLAs cannot be created. Run against a "
            "CS-provisioned trial via --profile agent-cs-trial (ADR 0012 / #503)."
        )

    # `incident` is SLA-enabled by default on a CS org, so the flip is usually a
    # no-op; enable + publish only when it is not, and restore only what we
    # changed so the org is left as found.
    if not _is_sla_enabled(md):
        metadata_update.update_entity(
            backend, entity, is_sla_enabled=True, publish=True)

        def _restore_sla_flag():
            try:
                metadata_update.update_entity(
                    backend, entity, is_sla_enabled=False, publish=True)
            except Exception:
                pass

        request.addfinalizer(_restore_sla_flag)

    sla_id_box: list[str] = []

    def _cleanup_sla():
        if sla_id_box:
            try:
                backend.delete(f"slas({sla_id_box[0]})")
            except Exception:
                pass

    # Registered after the flag-restore finalizer so it runs first (LIFO): delete
    # the SLA before restoring the entity flag.
    request.addfinalizer(_cleanup_sla)

    sla_name = f"E2E SLA {unique}"
    created = cli(
        ["--json", "sla", "create", "--name", sla_name, "--entity", entity,
         "--applicable-from", "createdon"],
        check=False,
    )
    # Check the exit code before parsing so a non-JSON failure surfaces stderr
    # rather than a JSONDecodeError (matches `sla activate` above).
    assert created.returncode == 0, (
        f"sla create failed:\n{created.stderr}\n{created.stdout}"
    )
    env = json.loads(created.stdout)
    assert env["ok"], f"sla create returned ok=False: {env}"
    data = env["data"]
    assert data["created"] is True, data
    sla_id = data["slaid"]
    assert sla_id, f"no slaid returned: {data}"
    sla_id_box.append(sla_id)
    assert data["entity"] == entity, data
    # The test pre-enabled IsSLAEnabled, so the command's own ensure-step is a
    # no-op — proves the precondition held before create.
    assert data["sla_enabled"] == "already", data

    added = cli(
        ["--json", "sla", "add-kpi", "--sla", sla_id, "--kpi", "resolvebykpiid",
         "--applicable-when", _KPI_APPLICABLE_WHEN,
         "--success-criteria", _KPI_SUCCESS],
        check=False,
    )
    assert added.returncode == 0, (
        f"sla add-kpi failed:\n{added.stderr}\n{added.stdout}"
    )
    env = json.loads(added.stdout)
    assert env["ok"], f"sla add-kpi returned ok=False: {env}"
    item = env["data"]
    assert item["created"] is True, item
    assert item["slaitemid"], f"no slaitemid returned: {item}"
    assert item["sla_id"] == sla_id, item
    # --name defaults to --kpi.
    assert item["name"] == "resolvebykpiid", item
