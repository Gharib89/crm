# pyright: basic
"""E2E tests for view verbs: create."""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


def _resolve_otc(backend, logical: str) -> int:
    """Fetch the ObjectTypeCode for a standard entity; skip if unavailable."""
    from crm.utils.d365_backend import as_dict, D365Error
    try:
        rb = as_dict(backend.get(
            f"EntityDefinitions(LogicalName='{logical}')",
            params={"$select": "ObjectTypeCode"},
        ))
    except D365Error as exc:
        pytest.skip(f"Could not resolve OTC for {logical!r}: {exc}")
    otc = rb.get("ObjectTypeCode")
    if not isinstance(otc, int) or otc <= 0:
        pytest.skip(f"OTC for {logical!r} not available: {rb}")
    return otc


# ── view create ───────────────────────────────────────────────────────────────


@covers("view create")
@pytest.mark.slow
def test_view_create_on_contact(backend, cli, request, unique):
    """Create a public system view on 'contact', assert created, clean up."""
    view_name = f"E2E View {unique}"
    otc = _resolve_otc(backend, "contact")

    created_id: list[str] = []

    def _cleanup():
        if created_id:
            try:
                backend.delete(f"savedqueries({created_id[0]})")
            except Exception:
                pass

    request.addfinalizer(_cleanup)

    result = cli([
        "--json", "view", "create", "contact",
        "--name", view_name,
        "--otc", str(otc),
        "--column", "contactid",
        "--column", "firstname",
        "--column", "lastname",
        "--no-publish",
    ])
    assert result.returncode == 0, (
        f"view create failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    data = env["data"]
    assert data.get("created") is True, f"expected created=True: {data}"
    sqid = data.get("savedqueryid")
    assert sqid, f"savedqueryid missing from response: {data}"
    created_id.append(sqid)

    # Verify the record exists via direct GET.
    from crm.utils.d365_backend import as_dict
    rb = as_dict(backend.get(
        f"savedqueries({sqid})",
        params={"$select": "name,savedqueryid"},
    ))
    assert rb.get("name") == view_name, (
        f"view name mismatch: expected {view_name!r}, got {rb.get('name')!r}"
    )
