"""Security role listing and assignment via the D365 Web API.

Every public function returns a plain dict or list of dicts — callers are
responsible for formatting.
"""

from __future__ import annotations

from typing import Any

from crm.core import entity as entity_mod
from crm.utils.d365_backend import D365Backend, as_dict

# ── Constants ────────────────────────────────────────────────────────────

_ROLES_SET = "roles"
_USER_ROLES_NAV = "systemuserroles_association"
_TEAM_ROLES_NAV = "teamroles_association"

# ── Reads ────────────────────────────────────────────────────────────────


def list_roles(
    backend: D365Backend,
    *,
    business_unit: str | None = None,
) -> list[dict[str, Any]]:
    """List security roles, optionally filtered by business unit GUID.

    ``business_unit`` is a GUID string; when supplied, only roles belonging
    to that business unit are returned (server-side OData ``$filter``).
    """
    params: dict[str, str] = {
        "$select": "roleid,name,_businessunitid_value",
        "$orderby": "name",
    }
    if business_unit is not None:
        params["$filter"] = f"_businessunitid_value eq {business_unit}"
    return as_dict(backend.get(_ROLES_SET, params=params)).get("value", [])


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
    return as_dict(backend.get(path, params=params)).get("value", [])


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
    return as_dict(backend.get(path, params=params)).get("value", [])


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
