# pyright: basic
"""E2E tests for security verbs: list-roles / list-user-roles /
list-team-roles / user-privileges / assign-role / unassign-role.
"""

from __future__ import annotations

import json

import pytest

from crm.tests.e2e.coverage import covers


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
    first = items[0]
    assert "roleid" in first, f"roleid missing from first role: {first}"
    assert "name" in first, f"name missing from first role: {first}"

    search_term = first["name"][:4]
    filtered = cli(["--json", "security", "list-roles", "--name-contains", search_term])
    assert filtered.returncode == 0, (
        f"security list-roles --name-contains failed:\n{filtered.stderr}"
    )
    fenv = json.loads(filtered.stdout)
    assert fenv["ok"], fenv
    fitems = fenv["data"]
    assert len(fitems) > 0, (
        f"--name-contains {search_term!r} returned empty — expected at least one match"
    )
    for r in fitems:
        assert search_term.lower() in r.get("name", "").lower(), (
            f"role {r.get('name')!r} does not contain {search_term!r}"
        )


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


@covers("security assign-role", "security unassign-role")
def test_assign_role_to_throwaway_team(cli, backend, throwaway_team):
    """Assign a role to a throwaway owner team, assert it appears, then remove it
    with `unassign-role` and assert it is gone.

    A throwaway team keeps the service account's permissions untouched. Picks
    the first role the org returns.
    """
    team_id = throwaway_team
    role_rows = backend.get(
        "roles",
        params={"$select": "roleid,name", "$orderby": "name", "$top": "1"},
    ).get("value", [])
    if not role_rows:
        pytest.skip("no roles found on org; cannot test assign-role")
    role_id = role_rows[0]["roleid"]

    def _team_role_ids():
        listed = cli(["--json", "security", "list-team-roles", team_id])
        assert listed.returncode == 0, f"list-team-roles failed:\n{listed.stderr}"
        return {r.get("roleid", "").lower() for r in json.loads(listed.stdout)["data"]}

    result = cli(["--json", "security", "assign-role", role_id, "--to-team", team_id, "--yes"])
    assert result.returncode == 0, (
        f"security assign-role failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    assert role_id.lower() in _team_role_ids()

    dry = cli(["--json", "--dry-run", "security", "unassign-role", role_id, "--from-team", team_id])
    assert dry.returncode == 0, f"unassign-role --dry-run failed:\n{dry.stderr}"
    assert json.loads(dry.stdout)["data"]["method"] == "DELETE"
    assert role_id.lower() in _team_role_ids(), "dry-run removed the role"

    result = cli(["--json", "security", "unassign-role", role_id, "--from-team", team_id, "--yes"])
    assert result.returncode == 0, (
        f"security unassign-role failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    assert role_id.lower() not in _team_role_ids()


def _role_privilege_ids(backend, role_id):
    """Set of lower-case privilegeids currently granted to a role."""
    resp = backend.get(
        f"roles({role_id})/roleprivileges_association",
        params={"$select": "privilegeid"},
    )
    return {r.get("privilegeid", "").lower() for r in resp.get("value", [])}


@covers("security create-role", "security set-role-privileges")
def test_create_role_and_set_privileges_roundtrip(
    cli, backend, unique, ephemeral_solution, request
):
    """Create a throwaway role, add then replace its privileges, verifying each
    grant lands, and confirm --dry-run previews without writing. The role is
    deleted in a finalizer regardless of outcome — fully reversible.

    Asserts presence of the targeted privilege after each call (the server may
    cascade dependent privileges, so absence-of-others is not asserted).
    """

    def _priv_id(name):
        resp = backend.get(
            "privileges",
            params={"$select": "privilegeid", "$filter": f"name eq '{name}'"},
        )
        rows = resp.get("value", [])
        assert rows, f"privilege {name!r} not found on org"
        return rows[0]["privilegeid"].lower()

    read_id = _priv_id("prvReadAccount")
    write_id = _priv_id("prvWriteAccount")

    role_name = f"e2e_role_{unique}"
    created = cli(
        [
            "--json",
            "security",
            "create-role",
            role_name,
            "--solution",
            ephemeral_solution,
            "--yes",
        ]
    )
    assert created.returncode == 0, (
        f"security create-role failed:\n{created.stderr}\nstdout: {created.stdout}"
    )
    role_id = json.loads(created.stdout)["data"]["roleid"]
    assert role_id, "create-role did not return a roleid"

    request.addfinalizer(lambda: _safe_delete(backend, f"roles({role_id})"))

    added = cli(
        [
            "--json",
            "security",
            "set-role-privileges",
            role_id,
            "--access",
            "read",
            "--entities",
            "account",
            "--depth",
            "global",
            "--add",
            "--yes",
        ]
    )
    assert added.returncode == 0, (
        f"set-role-privileges --add failed:\n{added.stderr}\nstdout: {added.stdout}"
    )
    add_env = json.loads(added.stdout)
    assert add_env["ok"], add_env
    assert add_env["data"]["mode"] == "add"
    assert read_id in _role_privilege_ids(backend, role_id), (
        "prvReadAccount not on role after --add"
    )

    before = _role_privilege_ids(backend, role_id)
    dry = cli(
        [
            "--json",
            "--dry-run",
            "security",
            "set-role-privileges",
            role_id,
            "--access",
            "write",
            "--entities",
            "account",
            "--depth",
            "global",
            "--add",
        ]
    )
    assert dry.returncode == 0, f"dry-run failed:\n{dry.stderr}\nstdout: {dry.stdout}"
    dry_env = json.loads(dry.stdout)
    assert dry_env["meta"].get("dry_run") is True, dry_env
    assert _role_privilege_ids(backend, role_id) == before, "dry-run mutated the role's privileges"

    replaced = cli(
        [
            "--json",
            "security",
            "set-role-privileges",
            role_id,
            "--access",
            "write",
            "--entities",
            "account",
            "--depth",
            "global",
            "--replace",
            "--yes",
        ]
    )
    assert replaced.returncode == 0, (
        f"set-role-privileges --replace failed:\n{replaced.stderr}\nstdout: {replaced.stdout}"
    )
    rep_env = json.loads(replaced.stdout)
    assert rep_env["ok"], rep_env
    assert rep_env["data"]["mode"] == "replace"
    assert write_id in _role_privilege_ids(backend, role_id), (
        "prvWriteAccount not on role after --replace"
    )


@covers("security create-role")
def test_create_role_into_solution(cli, backend, unique, ephemeral_solution, request):
    """Create a role with --solution and assert it lands in that solution as a
    component (componenttype 20 = Role). The role is deleted in a finalizer.

    Verifies the MSCRM.SolutionUniqueName create-header mechanism end-to-end: a
    role record is a solution component, so the header alone adds it — no
    separate AddSolutionComponent call.
    """
    sol_resp = backend.get(
        "solutions",
        params={
            "$select": "solutionid",
            "$filter": f"uniquename eq '{ephemeral_solution}'",
            "$top": "1",
        },
    )
    sol_rows = sol_resp.get("value", [])
    assert sol_rows, f"could not resolve solutionid for {ephemeral_solution!r}"
    solution_id = sol_rows[0]["solutionid"].lower()

    role_name = f"e2e_solrole_{unique}"
    created = cli(
        [
            "--json",
            "security",
            "create-role",
            role_name,
            "--solution",
            ephemeral_solution,
            "--yes",
        ]
    )
    assert created.returncode == 0, (
        f"create-role --solution failed:\n{created.stderr}\nstdout: {created.stdout}"
    )
    role_id = json.loads(created.stdout)["data"]["roleid"]
    assert role_id, "create-role did not return a roleid"

    request.addfinalizer(lambda: _safe_delete(backend, f"roles({role_id})"))

    comps = backend.get(
        "solutioncomponents",
        params={
            "$select": "objectid,_solutionid_value,componenttype",
            "$filter": f"objectid eq {role_id} and componenttype eq 20",
        },
    )
    owning = {r.get("_solutionid_value", "").lower() for r in comps.get("value", [])}
    assert solution_id in owning, (
        f"role {role_id} not a component of solution {ephemeral_solution!r} "
        f"({solution_id}); found in solutions: {owning}"
    )


def _safe_delete(backend, path):
    try:
        backend.delete(path)
    except Exception:
        pass


@covers("security grant", "security revoke", "security list-access")
def test_share_record_with_team_roundtrip(cli, backend, unique, request, throwaway_team):
    """Share a throwaway account with a throwaway team, then unshare it.

    Creates a disposable account (the shared record) and uses an owner team (the
    principal), grants the team Read+Write, asserts list-access reflects the
    share, revokes it, and asserts the share is gone. Both records are deleted
    after the test regardless of outcome. Fully reversible — no live data mutated.
    """
    team_id = throwaway_team
    account = backend.post(
        "accounts",
        json_body={"name": f"e2e_share_{unique}"},
        extra_headers={"Prefer": "return=representation"},
    )
    account_id = account.get("accountid")
    assert account_id, f"account creation did not return accountid: {account}"

    request.addfinalizer(lambda: _safe_delete(backend, f"accounts({account_id})"))

    granted = cli(
        [
            "--json",
            "security",
            "grant",
            "accounts",
            account_id,
            "--to",
            f"team:{team_id}",
            "--rights",
            "Read,Write",
            "--yes",
        ]
    )
    assert granted.returncode == 0, (
        f"security grant failed:\n{granted.stderr}\nstdout: {granted.stdout}"
    )
    assert json.loads(granted.stdout)["ok"]

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

    revoked = cli(
        [
            "--json",
            "security",
            "revoke",
            "accounts",
            account_id,
            "--from",
            f"team:{team_id}",
            "--yes",
        ]
    )
    assert revoked.returncode == 0, (
        f"security revoke failed:\n{revoked.stderr}\nstdout: {revoked.stdout}"
    )
    assert json.loads(revoked.stdout)["ok"]

    after = cli(["--json", "security", "list-access", "accounts", account_id])
    assert after.returncode == 0, after.stderr
    after_ids = {s.get("principalId", "").lower() for s in json.loads(after.stdout)["data"]}
    assert team_id.lower() not in after_ids, (
        f"team {team_id} still shared after revoke: {after_ids}"
    )
