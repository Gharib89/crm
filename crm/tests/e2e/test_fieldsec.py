# pyright: basic
"""E2E tests for fieldsec verbs: list, create-profile, assign, unassign, get, add-permission.

`fieldsec add-permission` is exercised against a throwaway custom entity whose
custom attribute is field-secured (`IsSecured=true` + publish) — the server
rejects creating a `fieldpermission` on an unsecured column. Teardown deletes
the custom entity, whose cascade drops the secured attribute and the permission
in one shot (far cleaner than un-securing a standard attribute).
"""

from __future__ import annotations

import json

import pytest

from crm.core.fieldsec import PERM_ALLOWED, PERM_NOT_ALLOWED
from crm.tests.e2e.coverage import covers


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


@covers("fieldsec create-profile", "fieldsec assign", "fieldsec get")
@pytest.mark.slow
def test_fieldsec_lifecycle(backend, cli, request, unique, ephemeral_solution):
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

    result = cli(
        [
            "--json",
            "fieldsec",
            "create-profile",
            name,
            "--description",
            "e2e",
            "--solution",
            ephemeral_solution,
        ]
    )
    assert result.returncode == 0, (
        f"create-profile failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    data = json.loads(result.stdout)["data"]
    assert data.get("created") is True, data
    pid = data.get("fieldsecurityprofileid")
    assert pid, f"fieldsecurityprofileid missing: {data}"
    created_id.append(pid)

    result = cli(["--json", "fieldsec", "assign", pid, "--user", user_id])
    assert result.returncode == 0, f"assign failed:\n{result.stderr}\nstdout: {result.stdout}"
    assigned = json.loads(result.stdout)["data"]
    assert assigned.get("assigned") is True, assigned
    assert assigned.get("principal_type") == "user", assigned

    result = cli(["--json", "fieldsec", "get", pid])
    assert result.returncode == 0, f"get failed:\n{result.stderr}\nstdout: {result.stdout}"
    got = json.loads(result.stdout)["data"]
    assert got.get("name") == name, got
    assert isinstance(got.get("permissions"), list), got


@covers("fieldsec unassign")
@pytest.mark.slow
def test_fieldsec_assign_unassign_team(
    backend, cli, request, unique, throwaway_team, ephemeral_solution
):
    """Assign a throwaway profile to a throwaway team, then remove it with
    `unassign`, checking the team's `teamprofiles_association` each time.
    """
    team_id = throwaway_team
    created = cli(
        [
            "--json",
            "fieldsec",
            "create-profile",
            f"E2E FieldSec Team {unique}",
            "--solution",
            ephemeral_solution,
        ]
    )
    assert created.returncode == 0, f"create-profile failed:\n{created.stderr}"
    pid = json.loads(created.stdout)["data"]["fieldsecurityprofileid"]
    assert pid, created.stdout

    def _cleanup():
        try:
            backend.delete(f"fieldsecurityprofiles({pid})")
        except Exception:
            pass

    request.addfinalizer(_cleanup)

    def _team_profile_ids():
        rows = backend.get(
            f"teams({team_id})/teamprofiles_association",
            params={"$select": "fieldsecurityprofileid"},
        ).get("value", [])
        return {r.get("fieldsecurityprofileid", "").lower() for r in rows}

    result = cli(["--json", "fieldsec", "assign", pid, "--team", team_id])
    assert result.returncode == 0, f"assign failed:\n{result.stderr}\nstdout: {result.stdout}"
    assert pid.lower() in _team_profile_ids()

    dry = cli(["--json", "--dry-run", "fieldsec", "unassign", pid, "--team", team_id])
    assert dry.returncode == 0, f"unassign --dry-run failed:\n{dry.stderr}"
    assert json.loads(dry.stdout)["data"]["would_unassign"] is True
    assert pid.lower() in _team_profile_ids(), "dry-run removed the profile"

    result = cli(["--json", "fieldsec", "unassign", pid, "--team", team_id, "--yes"])
    assert result.returncode == 0, f"unassign failed:\n{result.stderr}\nstdout: {result.stdout}"
    assert json.loads(result.stdout)["data"]["unassigned"] is True
    assert pid.lower() not in _team_profile_ids()


@covers("fieldsec add-permission")
@pytest.mark.slow
def test_fieldsec_add_permission_on_secured_custom_attribute(
    backend, cli, request, unique, ephemeral_solution
):
    """Grant a column permission on a field-secured custom attribute.

    The server rejects a `fieldpermission` unless the target column is secured
    (error 0x8004f508 "… is NOT secured …"), so the test stands up its own
    throwaway entity + secured custom column rather than touching a standard one.
    Teardown deletes the entity (one cascade drops the column + permission) and
    the independent profile, since the profile is a sibling record the entity
    does not own.

    `add_attribute` does not expose IsSecured, so the column is created by
    POSTing the StringAttributeMetadata directly with the flag set, then
    published so the metadata change goes live.
    """
    from crm.core import metadata as meta_mod
    from crm.core import solution as sol_mod

    schema = f"new_E2EFS{unique}"
    entity_logical = schema.lower()
    attr_schema = f"new_Secret{unique}"
    attr_logical = attr_schema.lower()

    created_entity: list[str] = []
    created_profile: list[str] = []

    def _cleanup():
        if created_entity:
            try:
                meta_mod.delete_entity(backend, created_entity[0])
            except Exception:
                pass
        if created_profile:
            try:
                backend.delete(f"fieldsecurityprofiles({created_profile[0]})")
            except Exception:
                pass

    request.addfinalizer(_cleanup)

    ent = meta_mod.create_entity(backend, schema_name=schema, display_name=f"E2E FS {unique}")
    created_entity.append(ent["logical_name"])

    backend.post(
        f"EntityDefinitions(LogicalName='{entity_logical}')/Attributes",
        json_body={
            "@odata.type": "Microsoft.Dynamics.CRM.StringAttributeMetadata",
            "SchemaName": attr_schema,
            "LogicalName": attr_logical,
            "DisplayName": meta_mod.label("Secret"),
            "RequiredLevel": {"Value": "None"},
            "MaxLength": 100,
            "FormatName": {"Value": "Text"},
            "IsSecured": True,
        },
    )
    sol_mod.publish_all(backend)

    result = cli(
        [
            "--json",
            "fieldsec",
            "create-profile",
            f"E2E FS {unique}",
            "--solution",
            ephemeral_solution,
        ]
    )
    assert result.returncode == 0, (
        f"create-profile failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    pid = json.loads(result.stdout)["data"].get("fieldsecurityprofileid")
    assert pid, f"fieldsecurityprofileid missing: {result.stdout}"
    created_profile.append(pid)

    result = cli(
        [
            "--json",
            "fieldsec",
            "add-permission",
            pid,
            entity_logical,
            attr_logical,
            "--read",
            "--update",
            "--solution",
            ephemeral_solution,
        ]
    )
    assert result.returncode == 0, (
        f"add-permission failed:\n{result.stderr}\nstdout: {result.stdout}"
    )
    data = json.loads(result.stdout)["data"]
    assert data.get("created") is True, data
    perm_id = data.get("fieldpermissionid")
    assert perm_id, f"fieldpermissionid missing: {data}"
    assert data.get("entity") == entity_logical, data
    assert data.get("attribute") == attr_logical, data
    assert data.get("canread") == PERM_ALLOWED, data
    assert data.get("canupdate") == PERM_ALLOWED, data
    assert data.get("cancreate") == PERM_NOT_ALLOWED, data

    persisted = backend.get(
        f"fieldpermissions({perm_id})",
        params={"$select": "entityname,attributelogicalname,canread,canupdate,cancreate"},
    )
    assert persisted.get("entityname") == entity_logical, persisted
    assert persisted.get("attributelogicalname") == attr_logical, persisted
    assert persisted.get("canread") == PERM_ALLOWED, persisted
    assert persisted.get("canupdate") == PERM_ALLOWED, persisted
    assert persisted.get("cancreate") == PERM_NOT_ALLOWED, persisted
