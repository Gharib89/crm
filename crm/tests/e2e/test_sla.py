# pyright: basic
"""E2E tests for sla verbs: activate.

Strategy:
  1. Try to create a minimal draft SLA (entity: `slas`, primaryentitytypecode
     set to a common entity like 'contact') via the backend directly.
  2. Run `sla activate` against it.
  3. Delete the SLA in a finalizer.

SLA creation via the Web API may be blocked on some orgs (e.g. if Case
Management / SLA feature is not provisioned).  The test probes the POST and
skips at runtime if the org rejects it.

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
