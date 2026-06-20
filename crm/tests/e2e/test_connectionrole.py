# pyright: basic
"""E2E tests for connectionrole verbs: create, scope, match.

Works on both targets (connection roles exist on on-prem v9.x and cloud). The
lifecycle test builds two throwaway roles, scopes one to `account` (present on
every org), pairs them, then deletes both — leaving the org as it found it.
"""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


@covers("connectionrole create", "connectionrole scope", "connectionrole match")
@pytest.mark.slow
def test_connectionrole_lifecycle(backend, cli, request, unique):
    """Create two roles, scope one to account, match them, then clean up."""
    created: list[str] = []

    def _cleanup():
        # Deleting a role cascades its object-type-codes and the matching
        # association rows, so removing both roles restores org state.
        for rid in created:
            try:
                backend.delete(f"connectionroles({rid})")
            except Exception:
                pass

    request.addfinalizer(_cleanup)

    # create role A (with a category)
    result = cli([
        "--json", "connectionrole", "create",
        "--name", f"E2E Role A {unique}", "--category", "business",
    ])
    assert result.returncode == 0, (
        f"connectionrole create A failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    data_a = json.loads(result.stdout)["data"]
    assert data_a.get("created") is True, data_a
    role_a = data_a.get("connectionroleid")
    assert role_a, f"connectionroleid missing: {data_a}"
    created.append(role_a)

    # create role B
    result = cli([
        "--json", "connectionrole", "create", "--name", f"E2E Role B {unique}",
    ])
    assert result.returncode == 0, (
        f"connectionrole create B failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    role_b = json.loads(result.stdout)["data"].get("connectionroleid")
    assert role_b, "connectionroleid for B missing"
    created.append(role_b)

    # scope role A to the account entity
    result = cli([
        "--json", "connectionrole", "scope", role_a, "--entity", "account",
    ])
    assert result.returncode == 0, (
        f"connectionrole scope failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    scoped = json.loads(result.stdout)["data"]
    assert scoped.get("created") is True, scoped
    assert scoped.get("connectionroleobjecttypecodeid"), scoped

    # match role A with role B
    result = cli([
        "--json", "connectionrole", "match", role_a, role_b,
    ])
    assert result.returncode == 0, (
        f"connectionrole match failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    matched = json.loads(result.stdout)["data"]
    assert matched.get("matched") is True, matched
    assert matched.get("role_a") == role_a, matched
    assert matched.get("role_b") == role_b, matched
