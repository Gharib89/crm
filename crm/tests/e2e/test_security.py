# pyright: basic
"""E2E tests for security verbs: list-roles / list-user-roles /
list-team-roles / user-privileges / assign-role.
"""
from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


# ── list-roles ────────────────────────────────────────────────────────────────


@covers("security list-roles")
def test_list_roles(cli):
    """Every D365 org ships with built-in security roles; the list must be non-empty."""
    result = cli(["--json", "security", "list-roles"])
    assert result.returncode == 0, (
        f"security list-roles failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    items = env["data"]
    assert isinstance(items, list), f"expected list, got {type(items)}: {env}"
    assert len(items) > 0, "security list-roles returned empty list — no roles on org"
    # Each role has at minimum a name and roleid.
    first = items[0]
    assert "roleid" in first, f"roleid missing from first role: {first}"
    assert "name" in first, f"name missing from first role: {first}"


# ── list-user-roles ───────────────────────────────────────────────────────────


@covers("security list-user-roles")
def test_list_user_roles(cli, backend):
    """Fetch the current user's id via WhoAmI and list their roles.

    A service-principal / system user always has at least one role on a sane org;
    the test asserts structure (ok + list) rather than non-empty so it stays green
    on orgs where the service account has no explicit roles beyond system defaults.
    """
    whoami = backend.get("WhoAmI")
    user_id = whoami.get("UserId")
    assert user_id, f"WhoAmI did not return UserId: {whoami}"

    result = cli(["--json", "security", "list-user-roles", user_id])
    assert result.returncode == 0, (
        f"security list-user-roles failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    items = env["data"]
    assert isinstance(items, list), f"expected list, got {type(items)}: {env}"


# ── user-privileges ───────────────────────────────────────────────────────────


@covers("security user-privileges")
def test_user_privileges(cli, backend):
    """Fetch the current user's id via WhoAmI and list their effective privileges.

    The service account always resolves to a non-empty privilege set on a sane
    org (it holds at least one role), so this asserts non-empty plus the shape
    RetrieveUserPrivileges returns (PrivilegeId / PrivilegeName / Depth).
    """
    whoami = backend.get("WhoAmI")
    user_id = whoami.get("UserId")
    assert user_id, f"WhoAmI did not return UserId: {whoami}"

    result = cli(["--json", "security", "user-privileges", user_id])
    assert result.returncode == 0, (
        f"security user-privileges failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    items = env["data"]
    assert isinstance(items, list), f"expected list, got {type(items)}: {env}"
    assert len(items) > 0, "user-privileges returned empty set for the service user"
    first = items[0]
    assert "PrivilegeId" in first, f"PrivilegeId missing from first privilege: {first}"
    assert "Depth" in first, f"Depth missing from first privilege: {first}"


# ── list-team-roles ───────────────────────────────────────────────────────────


@covers("security list-team-roles")
def test_list_team_roles(cli, backend):
    """Find any team on the org and list its roles.

    Teams always exist (the default team is created automatically for every
    business unit), but if somehow none are returned the test is runtime-skipped
    rather than failed.
    """
    resp = backend.get("teams", params={"$top": "1", "$select": "teamid,name"})
    rows = resp.get("value", [])
    if not rows:
        pytest.skip("no teams found on this org; cannot test list-team-roles")
    team_id = rows[0]["teamid"]

    result = cli(["--json", "security", "list-team-roles", team_id])
    assert result.returncode == 0, (
        f"security list-team-roles failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env
    items = env["data"]
    assert isinstance(items, list), f"expected list, got {type(items)}: {env}"


# ── assign-role ───────────────────────────────────────────────────────────────


@covers("security assign-role")
def test_assign_role_to_throwaway_team(cli, backend, unique, request):
    """Create a throwaway owner team, assign a role to it, assert the role appears,
    then unassign and delete the team in a finalizer.

    Using a throwaway team (instead of the current user) avoids mutating the
    service account's effective permissions and is fully reversible. The team is
    deleted in a finalizer regardless of test outcome; deleting the team also
    removes its role associations automatically.

    Picks the first role returned by list-roles (guaranteed non-empty on any org).
    """
    from crm.core import entity as entity_mod

    # Find the default business unit (required for team creation).
    bu_resp = backend.get(
        "businessunits",
        params={"$filter": "parentbusinessunitid eq null", "$select": "businessunitid", "$top": "1"},
    )
    bu_rows = bu_resp.get("value", [])
    if not bu_rows:
        pytest.skip("could not locate root business unit; cannot create throwaway team")
    bu_id = bu_rows[0]["businessunitid"]

    # Pick any role to assign (first alphabetically).
    roles_resp = backend.get(
        "roles",
        params={"$select": "roleid,name", "$orderby": "name", "$top": "1"},
    )
    role_rows = roles_resp.get("value", [])
    if not role_rows:
        pytest.skip("no roles found on org; cannot test assign-role")
    role_id = role_rows[0]["roleid"]
    role_name = role_rows[0].get("name", role_id)

    # Create a throwaway team (teamtype=0 = Owner).
    team_name = f"e2e_sec_{unique}"
    created = backend.post(
        "teams",
        json_body={
            "name": team_name,
            "teamtype": 0,
            "businessunitid@odata.bind": f"/businessunits({bu_id})",
        },
        extra_headers={"Prefer": "return=representation"},
    )
    team_id = created.get("teamid")
    assert team_id, f"team creation did not return teamid: {created}"

    def _cleanup():
        # Deleting the team removes role associations automatically; use direct
        # backend delete as the CLI entity delete verb may not be available without
        # a --yes flag in this context.
        try:
            backend.delete(f"teams({team_id})")
        except Exception:
            pass

    request.addfinalizer(_cleanup)

    # ── ASSIGN via CLI ────────────────────────────────────────────────────────
    result = cli([
        "--json", "security", "assign-role", role_id,
        "--to-team", team_id,
        "--yes",
    ])
    assert result.returncode == 0, (
        f"security assign-role (to-team) failed:\n{result.stderr}"
        f"\nstdout: {result.stdout}"
        f"\nrole={role_name!r} team={team_id!r}"
    )
    env = json.loads(result.stdout)
    assert env["ok"], env

    # Confirm the role now appears on the team (via backend, not CLI).
    assigned = backend.get(
        f"teams({team_id})/teamroles_association",
        params={"$select": "roleid", "$filter": f"roleid eq {role_id}"},
    )
    assigned_ids = {r.get("roleid", "").lower() for r in assigned.get("value", [])}
    assert role_id.lower() in assigned_ids, (
        f"role {role_id} ({role_name!r}) not found in team roles after assign: "
        f"{assigned_ids}"
    )

    # ── UNASSIGN via backend (no CLI unassign verb exists yet) ────────────────
    entity_mod.disassociate(
        backend,
        "teams",
        team_id,
        "teamroles_association",
        related_set="roles",
        related_id=role_id,
    )

    # Confirm removed.
    after = backend.get(
        f"teams({team_id})/teamroles_association",
        params={"$select": "roleid", "$filter": f"roleid eq {role_id}"},
    )
    remaining = {r.get("roleid", "").lower() for r in after.get("value", [])}
    assert role_id.lower() not in remaining, (
        f"role {role_id} still present on team after unassign: {remaining}"
    )


# ── grant / revoke / list-access (record sharing) ───────────────────────────


@covers("security grant", "security revoke", "security list-access")
def test_share_record_with_team_roundtrip(cli, backend, unique, request):
    """Share a throwaway account with a throwaway team, then unshare it.

    Creates a disposable account (the shared record) and an owner team (the
    principal), grants the team Read+Write, asserts list-access reflects the
    share, revokes it, and asserts the share is gone. Both records are deleted in
    a finalizer regardless of outcome. Fully reversible — no live data mutated.
    """
    # Root business unit (required to create an owner team).
    bu_resp = backend.get(
        "businessunits",
        params={"$filter": "parentbusinessunitid eq null",
                "$select": "businessunitid", "$top": "1"},
    )
    bu_rows = bu_resp.get("value", [])
    if not bu_rows:
        pytest.skip("could not locate root business unit; cannot create throwaway team")
    bu_id = bu_rows[0]["businessunitid"]

    account = backend.post(
        "accounts",
        json_body={"name": f"e2e_share_{unique}"},
        extra_headers={"Prefer": "return=representation"},
    )
    account_id = account.get("accountid")
    assert account_id, f"account creation did not return accountid: {account}"

    team = backend.post(
        "teams",
        json_body={
            "name": f"e2e_share_team_{unique}",
            "teamtype": 0,
            "businessunitid@odata.bind": f"/businessunits({bu_id})",
        },
        extra_headers={"Prefer": "return=representation"},
    )
    team_id = team.get("teamid")
    assert team_id, f"team creation did not return teamid: {team}"

    def _cleanup():
        for path in (f"accounts({account_id})", f"teams({team_id})"):
            try:
                backend.delete(path)
            except Exception:
                pass

    request.addfinalizer(_cleanup)

    # ── GRANT ──────────────────────────────────────────────────────────────
    granted = cli([
        "--json", "security", "grant", "accounts", account_id,
        "--to", f"team:{team_id}", "--rights", "Read,Write", "--yes",
    ])
    assert granted.returncode == 0, (
        f"security grant failed:\n{granted.stderr}\nstdout: {granted.stdout}"
    )
    assert json.loads(granted.stdout)["ok"]

    # ── LIST-ACCESS shows the team ───────────────────────────────────────────
    listed = cli(["--json", "security", "list-access", "accounts", account_id])
    assert listed.returncode == 0, (
        f"security list-access failed:\n{listed.stderr}\nstdout: {listed.stdout}"
    )
    shares = json.loads(listed.stdout)["data"]
    shared_ids = {s.get("principalId", "").lower() for s in shares}
    assert team_id.lower() in shared_ids, (
        f"team {team_id} not present in shares after grant: {shares}"
    )
    team_share = next(s for s in shares if s.get("principalId", "").lower() == team_id.lower())
    assert "ReadAccess" in team_share["accessMask"], team_share
    assert "WriteAccess" in team_share["accessMask"], team_share

    # ── REVOKE ──────────────────────────────────────────────────────────────
    revoked = cli([
        "--json", "security", "revoke", "accounts", account_id,
        "--from", f"team:{team_id}", "--yes",
    ])
    assert revoked.returncode == 0, (
        f"security revoke failed:\n{revoked.stderr}\nstdout: {revoked.stdout}"
    )
    assert json.loads(revoked.stdout)["ok"]

    # ── LIST-ACCESS no longer shows the team ─────────────────────────────────
    after = cli(["--json", "security", "list-access", "accounts", account_id])
    assert after.returncode == 0, after.stderr
    after_ids = {s.get("principalId", "").lower() for s in json.loads(after.stdout)["data"]}
    assert team_id.lower() not in after_ids, (
        f"team {team_id} still shared after revoke: {after_ids}"
    )
