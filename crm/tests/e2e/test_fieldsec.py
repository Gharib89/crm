# pyright: basic
"""E2E tests for fieldsec verbs: list, create-profile, assign, get.

`fieldsec add-permission` is intentionally not e2e-tested here — see the
`E2E_SKIP` entry in coverage.py for the reason (it needs a field-secured
attribute, which is heavy, org-stateful metadata setup). Its happy path is
covered by the wire-level unit tests in crm/tests/test_fieldsec.py.
"""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


# ── fieldsec list ─────────────────────────────────────────────────────────────


@covers("fieldsec list")
def test_fieldsec_list(cli):
    """Every org ships at least the System Administrator profile; assert shape."""
    result = cli(["--json", "fieldsec", "list"])
    assert result.returncode == 0, (
        f"fieldsec list failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    items = env["data"]
    assert isinstance(items, list), f"expected list, got {type(items)}: {env}"
    assert len(items) > 0, "fieldsec list returned empty list"
    assert "fieldsecurityprofileid" in items[0], items[0]
    assert "name" in items[0], items[0]


# ── fieldsec create-profile / assign / get lifecycle ──────────────────────────


@covers("fieldsec create-profile", "fieldsec assign", "fieldsec get")
@pytest.mark.slow
def test_fieldsec_lifecycle(backend, cli, request, unique):
    """Create a profile, assign it to the calling user, read it back, clean up."""
    from crm.utils.d365_backend import as_dict

    name = f"E2E FieldSec {unique}"
    me = as_dict(backend.get("WhoAmI"))
    user_id = me.get("UserId")
    assert user_id, f"WhoAmI returned no UserId: {me}"

    created_id: list[str] = []

    def _cleanup():
        if created_id:
            try:
                backend.delete(f"fieldsecurityprofiles({created_id[0]})")
            except Exception:
                pass

    request.addfinalizer(_cleanup)

    # create-profile
    result = cli([
        "--json", "fieldsec", "create-profile", name, "--description", "e2e",
    ])
    assert result.returncode == 0, (
        f"create-profile failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    data = json.loads(result.stdout)["data"]
    assert data.get("created") is True, data
    pid = data.get("fieldsecurityprofileid")
    assert pid, f"fieldsecurityprofileid missing: {data}"
    created_id.append(pid)

    # assign to the calling user
    result = cli(["--json", "fieldsec", "assign", pid, "--user", user_id])
    assert result.returncode == 0, (
        f"assign failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    assigned = json.loads(result.stdout)["data"]
    assert assigned.get("assigned") is True, assigned
    assert assigned.get("principal_type") == "user", assigned

    # get — profile fields + a permissions list (empty here)
    result = cli(["--json", "fieldsec", "get", pid])
    assert result.returncode == 0, (
        f"get failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    got = json.loads(result.stdout)["data"]
    assert got.get("name") == name, got
    assert isinstance(got.get("permissions"), list), got
