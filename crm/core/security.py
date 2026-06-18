"""Security role listing and assignment via the D365 Web API.

Every public function returns a plain dict or list of dicts — callers are
responsible for formatting.
"""

from __future__ import annotations

import json
from typing import Any, cast

from crm.core import entity as entity_mod
from crm.core import entity_names
from crm.utils.d365_backend import D365Backend, D365Error, as_dict, normalize_guid

# ── Constants ────────────────────────────────────────────────────────────

_ROLES_SET = "roles"
_USER_ROLES_NAV = "systemuserroles_association"
_TEAM_ROLES_NAV = "teamroles_association"
_USER_PRIVILEGES_FN = "Microsoft.Dynamics.CRM.RetrieveUserPrivileges"

# POA record-sharing operations (unbound Web API action/function names).
_GRANT_ACCESS_ACTION = "GrantAccess"
_REVOKE_ACCESS_ACTION = "RevokeAccess"
_SHARED_PRINCIPALS_FN = "RetrieveSharedPrincipalsAndAccess"

# Friendly access-right name → AccessRights enum member sent in an AccessMask.
# The friendly names are the documented CLI surface; the enum members are what
# the Web API expects.
_ACCESS_RIGHTS: dict[str, str] = {
    "read": "ReadAccess",
    "write": "WriteAccess",
    "append": "AppendAccess",
    "appendto": "AppendToAccess",
    "create": "CreateAccess",
    "delete": "DeleteAccess",
    "share": "ShareAccess",
    "assign": "AssignAccess",
}

# Principal-type discriminator → (logical name, primary-id attribute). These three
# entity types are the only valid sharing principals (user / team / organization),
# and their primary keys are stable system columns.
_PRINCIPAL_TYPES: dict[str, tuple[str, str]] = {
    "user": ("systemuser", "systemuserid"),
    "team": ("team", "teamid"),
    "org": ("organization", "organizationid"),
}

# ── Reads ────────────────────────────────────────────────────────────────


def list_roles(
    backend: D365Backend,
    *,
    business_unit: str | None = None,
    name_contains: str | None = None,
) -> list[dict[str, Any]]:
    """List security roles, optionally filtered server-side.

    ``business_unit`` (GUID) scopes to a single business unit.
    ``name_contains`` adds an OData ``contains(name,'…')`` clause.
    Both filters compose (AND-joined).
    """
    params: dict[str, str] = {
        "$select": "roleid,name,_businessunitid_value",
        "$orderby": "name",
    }
    filters: list[str] = []
    if business_unit is not None:
        normalized_bu = normalize_guid(business_unit)
        if normalized_bu is None:
            raise D365Error(f"business_unit must be a GUID; got {business_unit!r}")
        filters.append(f"_businessunitid_value eq {normalized_bu}")
    if name_contains is not None:
        escaped = name_contains.replace("'", "''")
        filters.append(f"contains(name,'{escaped}')")
    if filters:
        params["$filter"] = " and ".join(filters)
    return backend.get_collection(_ROLES_SET, params=params)


def list_user_roles(
    backend: D365Backend,
    user_id: str,
) -> list[dict[str, Any]]:
    """List the security roles assigned to a system user."""
    path = f"{entity_mod.build_record_path('systemusers', user_id)}/{_USER_ROLES_NAV}"
    params: dict[str, str] = {
        "$select": "roleid,name",
        "$orderby": "name",
    }
    return backend.get_collection(path, params=params)


def list_team_roles(
    backend: D365Backend,
    team_id: str,
) -> list[dict[str, Any]]:
    """List the security roles assigned to a team."""
    path = f"{entity_mod.build_record_path('teams', team_id)}/{_TEAM_ROLES_NAV}"
    params: dict[str, str] = {
        "$select": "roleid,name",
        "$orderby": "name",
    }
    return backend.get_collection(path, params=params)


def list_user_privileges(
    backend: D365Backend,
    user_id: str,
) -> list[dict[str, Any]]:
    """Retrieve a system user's effective privileges via RetrieveUserPrivileges.

    Returns the resolved RolePrivileges set — privileges from the user's own
    security roles plus those inherited from team membership. Per the Web API
    contract, team-inherited privileges are reported at Basic (user) depth only;
    the per-privilege RetrieveUserPrivilegeByPrivilegeId/Name messages are needed
    for the full inherited depth (out of scope here).
    """
    path = f"{entity_mod.build_record_path('systemusers', user_id)}/{_USER_PRIVILEGES_FN}"
    result = as_dict(backend.get(path))
    privileges: list[dict[str, Any]] = result.get("RolePrivileges", [])
    return privileges


# ── Writes ───────────────────────────────────────────────────────────────


def _assign_role(
    backend: D365Backend,
    target_set: str,
    target_id: str,
    nav: str,
    role_id: str,
    *,
    caller_id: str | None,
    caller_object_id: str | None,
    suppress_duplicate_detection: bool | None,
    bypass_custom_plugin_execution: bool | None,
) -> dict[str, Any]:
    return entity_mod.associate(
        backend,
        target_set,
        target_id,
        nav,
        _ROLES_SET,
        role_id,
        caller_id=caller_id,
        caller_object_id=caller_object_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )


def assign_role_to_user(
    backend: D365Backend,
    user_id: str,
    role_id: str,
    *,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """Assign a security role to a system user."""
    return _assign_role(
        backend,
        "systemusers",
        user_id,
        _USER_ROLES_NAV,
        role_id,
        caller_id=caller_id,
        caller_object_id=caller_object_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )


def assign_role_to_team(
    backend: D365Backend,
    team_id: str,
    role_id: str,
    *,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """Assign a security role to a team."""
    return _assign_role(
        backend,
        "teams",
        team_id,
        _TEAM_ROLES_NAV,
        role_id,
        caller_id=caller_id,
        caller_object_id=caller_object_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )


# ── Record sharing (POA) ───────────────────────────────────────────────────


def _access_mask(rights: str) -> str:
    """Translate a comma-separated friendly rights list into an AccessMask.

    Accepts the friendly names (``Read``, ``Write``, ``AppendTo`` …) case-
    insensitively, and tolerates the full enum spelling (``ReadAccess``). Returns
    the comma-joined ``AccessRights`` enum members in the order given, de-duped.
    Raises :class:`D365Error` on an unknown right or an empty list.
    """
    members: list[str] = []
    for raw in rights.split(","):
        token = raw.strip()
        if not token:
            continue
        key = token.lower()
        if key.endswith("access"):
            key = key[: -len("access")]
        member = _ACCESS_RIGHTS.get(key)
        if member is None:
            valid = ", ".join(m[: -len("Access")] for m in _ACCESS_RIGHTS.values())
            raise D365Error(f"unknown access right {token!r}; valid rights: {valid}")
        if member not in members:
            members.append(member)
    if not members:
        raise D365Error("at least one access right is required (e.g. Read,Write)")
    return ", ".join(members)


def _principal_ref(principal_type: str, principal_id: str) -> dict[str, str]:
    """Build the EntityReference dict for a sharing principal (user/team/org)."""
    spec = _PRINCIPAL_TYPES.get(principal_type.lower())
    if spec is None:
        valid = ", ".join(_PRINCIPAL_TYPES)
        raise D365Error(
            f"unknown principal type {principal_type!r}; expected one of: {valid}"
        )
    logical, id_attr = spec
    gid = normalize_guid(principal_id)
    if gid is None:
        raise D365Error(f"principal id must be a GUID; got {principal_id!r}")
    return {id_attr: gid, "@odata.type": f"Microsoft.Dynamics.CRM.{logical}"}


def _target_ref(backend: D365Backend, entity_set: str, record_id: str) -> dict[str, str]:
    """Build the Target EntityReference dict for a record in *entity_set*.

    A bound action parameter of entity type needs the record's own primary-id
    attribute (``accountid``) and ``@odata.type``, so the entity-set name is
    resolved to its logical name + primary-id attribute via the cached name map.
    """
    gid = normalize_guid(record_id)
    if gid is None:
        raise D365Error(f"record id must be a GUID; got {record_id!r}")
    name_map = entity_names.load_name_map(backend)
    logical = name_map.resolve(entity_set)
    id_attr = name_map.primary_id_for(logical)
    if not id_attr:
        raise D365Error(f"could not resolve the primary-id attribute for {entity_set!r}")
    return {id_attr: gid, "@odata.type": f"Microsoft.Dynamics.CRM.{logical}"}


def grant_access(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    *,
    principal_type: str,
    principal_id: str,
    rights: str,
) -> dict[str, Any]:
    """Share a record with a principal at the given access rights (GrantAccess).

    ``rights`` is a comma-separated friendly list (``Read,Write,Share``). The
    record (``entity_set``/``record_id``) and principal (``principal_type`` ∈
    user/team/org, ``principal_id``) are both validated before the call.
    """
    body = {
        "Target": _target_ref(backend, entity_set, record_id),
        "PrincipalAccess": {
            "Principal": _principal_ref(principal_type, principal_id),
            "AccessMask": _access_mask(rights),
        },
    }
    result = as_dict(backend.post(_GRANT_ACCESS_ACTION, json_body=body))
    return result or {"granted": True}


def revoke_access(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    *,
    principal_type: str,
    principal_id: str,
) -> dict[str, Any]:
    """Remove a principal's shared access to a record (RevokeAccess).

    RevokeAccess removes *all* of the principal's shared rights on the record;
    there is no per-right revoke (use :func:`grant_access` to re-share at a
    narrower set).
    """
    body = {
        "Target": _target_ref(backend, entity_set, record_id),
        "Revokee": _principal_ref(principal_type, principal_id),
    }
    result = as_dict(backend.post(_REVOKE_ACCESS_ACTION, json_body=body))
    return result or {"revoked": True}


def _normalize_shared_principal(pa: dict[str, Any]) -> dict[str, Any]:
    """Flatten one RetrieveSharedPrincipalsAndAccess entry to a stable shape.

    The raw entry is ``{"AccessMask": "...", "Principal": {"@odata.type": "#…
    systemuser", "<idkey>": "<guid>"}}``. The standard JSON envelope strips
    ``@odata.*`` keys, which would drop the principal's type, so the type and id
    are lifted into plain ``principalType``/``principalId`` fields here.
    """
    raw = pa.get("Principal")
    principal: dict[str, Any] = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    odata_type = str(principal.get("@odata.type", ""))
    principal_type = odata_type.rsplit(".", 1)[-1] if odata_type else ""
    principal_id = ""
    for key, value in principal.items():
        if not key.startswith("@"):
            principal_id = str(value)
            break
    return {
        "principalType": principal_type,
        "principalId": principal_id,
        "accessMask": pa.get("AccessMask", ""),
    }


def list_access(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
) -> list[dict[str, Any]]:
    """List the principals a record is shared with and their access masks.

    Wraps the RetrieveSharedPrincipalsAndAccess function. Each entry is flattened
    to ``{principalType, principalId, accessMask}``. Read-only — needs no metadata
    resolution because the function takes the record as an ``@odata.id`` reference
    keyed by entity-set name.
    """
    gid = normalize_guid(record_id)
    if gid is None:
        raise D365Error(f"record id must be a GUID; got {record_id!r}")
    target = json.dumps({"@odata.id": f"{entity_set}({gid})"})
    path = f"{_SHARED_PRINCIPALS_FN}(Target=@p1)"
    result = as_dict(backend.get(path, params={"@p1": target}))
    accesses: list[dict[str, Any]] = result.get("PrincipalAccesses", [])
    return [_normalize_shared_principal(pa) for pa in accesses]
