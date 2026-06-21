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

# ── Role authoring (create-role / set-role-privileges) ─────────────────────

# Bound actions on mscrm.role for privilege authoring.
_ADD_PRIVILEGES_ACTION = "Microsoft.Dynamics.CRM.AddPrivilegesRole"
_REPLACE_PRIVILEGES_ACTION = "Microsoft.Dynamics.CRM.ReplacePrivilegesRole"

# Privilege depth levels, lowest → highest. The list index is the rank used when
# clamping a requested depth to the levels a privilege actually supports.
_DEPTH_ORDER: tuple[str, ...] = ("Basic", "Local", "Deep", "Global")

# Friendly/alias depth name (lower-case) → canonical Web API Depth enum member.
_DEPTH_ALIASES: dict[str, str] = {
    "basic": "Basic", "user": "Basic",
    "local": "Local", "businessunit": "Local",
    "deep": "Deep", "parentchild": "Deep",
    "global": "Global", "organization": "Global",
}

# Access keyword → AccessRights bitmask (the `privileges` entity's `accessright`
# column). Canonical platform enum values — verified live on cloud + on-prem.
_ACCESS_BITMASK: dict[str, int] = {
    "read": 1, "write": 2, "append": 4, "appendto": 16,
    "create": 32, "delete": 65536, "share": 262144, "assign": 524288,
}

# Access keyword → metadata PrivilegeType enum member (EntityMetadata.Privileges).
_ACCESS_PRIVILEGE_TYPE: dict[str, str] = {
    "read": "Read", "write": "Write", "append": "Append", "appendto": "AppendTo",
    "create": "Create", "delete": "Delete", "share": "Share", "assign": "Assign",
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


# ── Role authoring ─────────────────────────────────────────────────────────


def _caller_business_unit(backend: D365Backend) -> str:
    """Resolve the caller's business unit GUID via WhoAmI (default for new roles)."""
    info = as_dict(backend.get("WhoAmI"))
    bu = info.get("BusinessUnitId")
    if not bu:
        raise D365Error("could not resolve the caller's business unit from WhoAmI")
    return str(bu)


def _find_role_by_name(backend: D365Backend, name: str, business_unit: str) -> str | None:
    """Return the roleid of a role with this name in the business unit, or None."""
    escaped = name.replace("'", "''")
    rows = backend.get_collection(
        _ROLES_SET,
        params={
            "$select": "roleid",
            "$filter": f"name eq '{escaped}' and _businessunitid_value eq {business_unit}",
            "$top": "1",
        },
    )
    if rows:
        return str(rows[0].get("roleid"))
    return None


def create_role(
    backend: D365Backend,
    name: str,
    *,
    business_unit: str | None = None,
    if_exists: str = "error",
    solution: str | None = None,
) -> dict[str, Any]:
    """Create a security role, returning ``{roleid, name, businessunitid}``.

    ``business_unit`` (GUID) defaults to the caller's own business unit (WhoAmI).
    ``if_exists`` controls a same-name/same-BU collision: ``error`` (default)
    raises; ``skip`` returns the existing role's id with ``existed: True``.

    ``solution`` (an unmanaged solution's unique name) adds the new role to that
    solution as a component, via the ``MSCRM.SolutionUniqueName`` create header
    (roles are solution components, so no separate AddSolutionComponent call is
    needed). Solution membership applies only to a *newly created* role: with
    ``if_exists='skip'`` an existing role is returned unchanged and is **not**
    added to ``solution``.
    """
    if business_unit is None:
        bu = _caller_business_unit(backend)
    else:
        normalized = normalize_guid(business_unit)
        if normalized is None:
            raise D365Error(f"business_unit must be a GUID; got {business_unit!r}")
        bu = normalized

    existing = _find_role_by_name(backend, name, bu)
    if existing is not None:
        if if_exists == "skip":
            return {"roleid": existing, "name": name,
                    "businessunitid": bu, "existed": True}
        raise D365Error(
            f"a role named {name!r} already exists in business unit {bu} "
            "(use --if-exists skip to reuse it)"
        )

    payload: dict[str, Any] = {
        "name": name,
        "businessunitid@odata.bind": f"/businessunits({bu})",
    }
    if backend.dry_run:
        # Stable preview shape (mirrors themes/dashboard/charts cores) rather
        # than leaking the backend's raw request echo.
        return {"_dry_run": True,
                "would_create": {"entity_set": _ROLES_SET, "body": payload,
                                  "solution": solution}}
    extra_headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = entity_mod.create(backend, _ROLES_SET, payload, extra_headers=extra_headers)
    return {
        "roleid": str(result.get("roleid", "")),
        "name": str(result.get("name", name)),
        "businessunitid": bu,
    }


def _normalize_depth(depth: str) -> str:
    """Map a friendly/alias depth to its canonical Web API enum member."""
    canonical = _DEPTH_ALIASES.get(depth.strip().lower())
    if canonical is None:
        valid = ", ".join(sorted(set(_DEPTH_ALIASES)))
        raise D365Error(f"unknown depth {depth!r}; valid: {valid}")
    return canonical


def _validate_access(access: list[str]) -> list[str]:
    """Lower-case, validate, and de-dupe a list of access keywords."""
    normalized: list[str] = []
    for raw in access:
        key = raw.strip().lower()
        if key not in _ACCESS_BITMASK:
            valid = ", ".join(_ACCESS_BITMASK)
            raise D365Error(f"unknown access type {raw!r}; valid: {valid}")
        if key not in normalized:
            normalized.append(key)
    return normalized


def _norm_priv(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw privilege to ``{name, privilegeid, supported}``.

    ``supported`` is the list of depths the privilege allows. Accepts both the
    metadata shape (PascalCase ``CanBeBasic`` …, ``Name``, ``PrivilegeId``) and
    the ``privileges`` entity shape (lower-case ``canbebasic`` …, ``name``,
    ``privilegeid``).
    """
    def flag(*keys: str) -> bool:
        for key in keys:
            if key in raw:
                return bool(raw[key])
        return False

    supported: list[str] = []
    if flag("CanBeBasic", "canbebasic"):
        supported.append("Basic")
    if flag("CanBeLocal", "canbelocal"):
        supported.append("Local")
    if flag("CanBeDeep", "canbedeep"):
        supported.append("Deep")
    if flag("CanBeGlobal", "canbeglobal"):
        supported.append("Global")
    return {
        "name": str(raw.get("Name") or raw.get("name") or ""),
        "privilegeid": str(raw.get("PrivilegeId") or raw.get("privilegeid") or ""),
        "supported": supported,
    }


def _clamp_depth(requested: str, supported: list[str]) -> str:
    """Resolve the requested depth to a level the privilege actually supports.

    The goal is to never send an unsupported depth (the server rejects it).
    Returns the requested depth when supported; otherwise clamps *down* to the
    highest supported level below it. When nothing lower exists — e.g. a
    Global-only customization privilege requested at Basic — there is no lower
    valid level, so the only correct resolution is *up* to the lowest (only)
    supported level. Returns the requested depth unchanged when the privilege
    advertises no levels at all (let the server decide).
    """
    if not supported:
        return requested
    if requested in supported:
        return requested
    rank = _DEPTH_ORDER.index
    lower = [d for d in supported if rank(d) < rank(requested)]
    if lower:
        return max(lower, key=rank)
    return min(supported, key=rank)


def _resolve_role_id(backend: D365Backend, role: str) -> str:
    """Resolve a role argument (GUID or unique role name) to a roleid."""
    gid = normalize_guid(role)
    if gid is not None:
        return gid
    escaped = role.replace("'", "''")
    rows = backend.get_collection(
        _ROLES_SET,
        params={"$select": "roleid,name", "$filter": f"name eq '{escaped}'"},
    )
    if not rows:
        raise D365Error(f"no role found named {role!r}")
    if len(rows) > 1:
        raise D365Error(
            f"role name {role!r} is ambiguous ({len(rows)} matches across business "
            "units); pass the role id instead"
        )
    return str(rows[0].get("roleid"))


_PRIV_SELECT = "name,privilegeid,canbebasic,canbelocal,canbedeep,canbeglobal"


def _entity_privileges(backend: D365Backend, logical: str) -> list[dict[str, Any]]:
    """Fetch the Privileges complex property for one entity's metadata.

    Raises :class:`D365Error` with a clean message when the entity is unknown.
    """
    escaped = logical.replace("'", "''")
    path = f"EntityDefinitions(LogicalName='{escaped}')"
    try:
        result = as_dict(backend.get(path, params={"$select": "Privileges"}))
    except D365Error as exc:
        if exc.status == 404:
            raise D365Error(f"unknown entity {logical!r}") from exc
        raise
    privileges = result.get("Privileges")
    if isinstance(privileges, list):
        return cast("list[dict[str, Any]]", privileges)
    return []


def _all_entity_privileges(backend: D365Backend, access: list[str]) -> list[dict[str, Any]]:
    """Fetch every privilege matching the requested access types, org-wide."""
    clauses = " or ".join(f"accessright eq {_ACCESS_BITMASK[a]}" for a in access)
    return backend.get_collection(
        "privileges", params={"$select": _PRIV_SELECT, "$filter": clauses},
    )


def _named_privileges(backend: D365Backend, names: list[str]) -> list[dict[str, Any]]:
    """Fetch privileges by exact name; raise if any requested name is unknown."""
    clauses = " or ".join(f"name eq '{n.replace(chr(39), chr(39) * 2)}'" for n in names)
    rows = backend.get_collection(
        "privileges", params={"$select": _PRIV_SELECT, "$filter": clauses},
    )
    found = {str(r.get("name")) for r in rows}
    missing = [n for n in names if n not in found]
    if missing:
        raise D365Error(f"unknown privilege(s): {', '.join(missing)}")
    return rows


def set_role_privileges(
    backend: D365Backend,
    role: str,
    *,
    access: list[str] | None = None,
    entities: list[str] | None = None,
    all_entities: bool = False,
    privilege_names: list[str] | None = None,
    depth: str,
    replace: bool = False,
) -> dict[str, Any]:
    """Add or replace a role's privileges from typed selectors.

    Selectors (≥1 required): ``access`` (+ ``entities`` or ``all_entities``)
    and/or explicit ``privilege_names``. ``depth`` is required (friendly name or
    alias) and is clamped per privilege to the levels it supports. ``replace``
    swaps the role's privileges for exactly the resolved set; otherwise the set
    is merged (added).

    Returns ``{roleid, mode, depth, privileges, count, warnings}``. A requested
    access×entity combo with no matching privilege is skipped with a warning; an
    empty resolved set is an error (never sends an empty replace).
    """
    access = access or []
    entities = entities or []
    privilege_names = privilege_names or []

    if entities and all_entities:
        raise D365Error("provide either --entities or --all-entities, not both")
    if access:
        access = _validate_access(access)
        if not entities and not all_entities:
            raise D365Error(
                "--access requires an entity scope: add --entities or --all-entities"
            )
    elif entities or all_entities:
        raise D365Error("--entities/--all-entities require --access")
    if not access and not privilege_names:
        raise D365Error(
            "provide at least one selector: --access (with --entities/--all-entities) "
            "or --privilege"
        )

    requested_depth = _normalize_depth(depth)
    role_id = _resolve_role_id(backend, role)

    warnings: list[str] = []
    resolved: dict[str, dict[str, Any]] = {}

    def add(raw: dict[str, Any]) -> None:
        norm = _norm_priv(raw)
        pid = str(norm["privilegeid"])
        if not pid:
            return
        chosen = _clamp_depth(requested_depth, norm["supported"])
        if chosen != requested_depth:
            warnings.append(f"{norm['name']}: depth clamped {requested_depth} → {chosen}")
        prev = resolved.get(pid)
        if prev is None or _DEPTH_ORDER.index(chosen) > _DEPTH_ORDER.index(prev["depth"]):
            resolved[pid] = {"name": norm["name"], "privilegeid": pid, "depth": chosen}

    if access and entities:
        for ent in entities:
            logical = ent.strip().lower()
            by_type: dict[str, dict[str, Any]] = {}
            for raw in _entity_privileges(backend, logical):
                ptype = raw.get("PrivilegeType")
                if isinstance(ptype, str):
                    by_type[ptype] = raw
            for key in access:
                raw = by_type.get(_ACCESS_PRIVILEGE_TYPE[key])
                if raw is None:
                    warnings.append(f"{logical}: no {key} privilege (skipped)")
                    continue
                add(raw)
    if access and all_entities:
        for raw in _all_entity_privileges(backend, access):
            add(raw)
    if privilege_names:
        for raw in _named_privileges(backend, privilege_names):
            add(raw)

    privileges = sorted(resolved.values(), key=lambda p: str(p["name"]))
    if not privileges:
        raise D365Error("no privileges resolved for the given selectors; nothing to apply")

    action = _REPLACE_PRIVILEGES_ACTION if replace else _ADD_PRIVILEGES_ACTION
    out: dict[str, Any] = {
        "roleid": role_id,
        "mode": "replace" if replace else "add",
        "depth": requested_depth,
        "privileges": privileges,
        "count": len(privileges),
        "warnings": warnings,
    }
    if backend.dry_run:
        # The resolved `privileges`/`count` above are the preview; add the
        # conventional `_dry_run` marker (so emit treats it as a dry-run preview)
        # plus a stable `would_apply` summary, rather than POSTing.
        out["_dry_run"] = True
        out["would_apply"] = {"action": out["mode"], "count": len(privileges)}
        return out
    body = {
        "Privileges": [
            {"PrivilegeId": p["privilegeid"], "Depth": p["depth"]} for p in privileges
        ]
    }
    path = f"{entity_mod.build_record_path(_ROLES_SET, role_id)}/{action}"
    backend.post(path, json_body=body)
    out["applied"] = True
    return out
