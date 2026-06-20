"""Field-level (column) security via the D365 Web API.

Wraps the ``fieldsecurityprofile`` + ``fieldpermission`` entities and the
``systemuserprofiles_association`` / ``teamprofiles_association`` N:N
relationships used to attach a profile to users and teams.

Every public function takes the backend first and returns a plain dict (or list
of dicts) — callers (the Click layer) own all formatting. Click-free so the
module stays pyright-strict.
"""

from __future__ import annotations

from typing import Any

from crm.core import entity as entity_mod
from crm.utils.d365_backend import D365Backend, D365Error, as_dict, normalize_guid

# ── Constants ──────────────────────────────────────────────────────────────

PROFILES_SET = "fieldsecurityprofiles"
PERMISSIONS_SET = "fieldpermissions"
_PROFILE_ID = "fieldsecurityprofileid"
_PROFILE_LOOKUP_VALUE = "_fieldsecurityprofileid_value"

# fieldpermission CanCreate/CanRead/CanUpdate use the field_security_permission_type
# global choice: 0 = Not Allowed, 4 = Allowed (verified against the Dataverse /
# on-prem v9.1 fieldpermission reference).
PERM_ALLOWED = 4
PERM_NOT_ALLOWED = 0

# N:N navigation properties on the fieldsecurityprofile side.
_USER_NAV = "systemuserprofiles_association"
_TEAM_NAV = "teamprofiles_association"


# ── Profile resolution ───────────────────────────────────────────────────


def resolve_profile_id(backend: D365Backend, profile: str) -> str:
    """Resolve a field security profile reference (GUID or name) to its id.

    A GUID is used as-is; anything else is treated as the profile ``name`` and
    looked up with an exact match. Raises :class:`D365Error` (``NotFound``) when
    no profile matches. The read runs for real even under dry-run so a preview
    path still resolves the id it needs.
    """
    gid = normalize_guid(profile)
    if gid is not None:
        return gid
    rid = backend.resolve_id_by_name(
        PROFILES_SET, filter_field="name", id_field=_PROFILE_ID, value=profile,
    )
    if rid is None:
        raise D365Error(
            f"No field security profile named {profile!r}.", code="NotFound",
        )
    return rid


def _solution_headers(solution: str | None) -> dict[str, str] | None:
    return {"MSCRM.SolutionUniqueName": solution} if solution else None


# ── Reads ──────────────────────────────────────────────────────────────────


def list_profiles(backend: D365Backend) -> list[dict[str, Any]]:
    """List all field security profiles (id + name), ordered by name."""
    return backend.get_collection(
        PROFILES_SET,
        params={"$select": f"{_PROFILE_ID},name,description", "$orderby": "name"},
    )


def get_profile(backend: D365Backend, profile: str) -> dict[str, Any]:
    """Retrieve one profile plus the field permissions it grants.

    ``profile`` is a GUID or a profile name. Returns the profile fields with a
    ``permissions`` list of ``{entityname, attributelogicalname, canread,
    cancreate, canupdate, fieldpermissionid}`` entries.
    """
    profile_id = resolve_profile_id(backend, profile)
    record = as_dict(backend.get(
        entity_mod.build_record_path(PROFILES_SET, profile_id),
        params={"$select": f"{_PROFILE_ID},name,description"},
    ))
    permissions = backend.get_collection(
        PERMISSIONS_SET,
        params={
            "$select": ("fieldpermissionid,entityname,attributelogicalname,"
                        "cancreate,canread,canupdate"),
            "$filter": f"{_PROFILE_LOOKUP_VALUE} eq {profile_id}",
        },
    )
    record["permissions"] = permissions
    return record


# ── Writes ─────────────────────────────────────────────────────────────────


def create_profile(
    backend: D365Backend,
    *,
    name: str,
    description: str | None = None,
    solution: str | None = None,
) -> dict[str, Any]:
    """Create a field security profile. Returns ``{created, fieldsecurityprofileid, ...}``."""
    if not name:
        raise D365Error("name is required.")
    body: dict[str, Any] = {"name": name}
    if description is not None:
        body["description"] = description
    result = as_dict(backend.post(
        PROFILES_SET, json_body=body, extra_headers=_solution_headers(solution),
    ))
    if result.get("_dry_run"):
        result["would_create"] = True
        return result
    profile_id = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "name": name,
        _PROFILE_ID: profile_id,
        "solution": solution,
    }
    if not profile_id:
        out["fieldsecurityprofile_lookup_error"] = (
            "Could not parse fieldsecurityprofileid from response: "
            f"{result.get('_entity_id_url')!r}"
        )
    return out


def add_permission(
    backend: D365Backend,
    *,
    profile: str,
    entity: str,
    attribute: str,
    read: bool = False,
    create: bool = False,
    update: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Grant column-level permissions on ``entity.attribute`` for ``profile``.

    ``read`` / ``create`` / ``update`` map to the fieldpermission CanRead /
    CanCreate / CanUpdate levels (Allowed when set, Not Allowed otherwise). At
    least one grant is required. ``profile`` is a GUID or a profile name.
    """
    if not entity:
        raise D365Error("entity is required.")
    if not attribute:
        raise D365Error("attribute is required.")
    if not (read or create or update):
        raise D365Error(
            "at least one of --read/--create/--update is required.",
        )
    profile_id = resolve_profile_id(backend, profile)
    body: dict[str, Any] = {
        "entityname": entity,
        "attributelogicalname": attribute,
        "canread": PERM_ALLOWED if read else PERM_NOT_ALLOWED,
        "cancreate": PERM_ALLOWED if create else PERM_NOT_ALLOWED,
        "canupdate": PERM_ALLOWED if update else PERM_NOT_ALLOWED,
        f"{_PROFILE_ID}@odata.bind": f"/{PROFILES_SET}({profile_id})",
    }
    result = as_dict(backend.post(
        PERMISSIONS_SET, json_body=body, extra_headers=_solution_headers(solution),
    ))
    if result.get("_dry_run"):
        result["would_create"] = True
        return result
    permission_id = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "fieldpermissionid": permission_id,
        "profile": profile_id,
        "entity": entity,
        "attribute": attribute,
        "canread": body["canread"],
        "cancreate": body["cancreate"],
        "canupdate": body["canupdate"],
        "solution": solution,
    }
    if not permission_id:
        out["fieldpermission_lookup_error"] = (
            "Could not parse fieldpermissionid from response: "
            f"{result.get('_entity_id_url')!r}"
        )
    return out


def assign(
    backend: D365Backend,
    *,
    profile: str,
    user_id: str | None = None,
    team_id: str | None = None,
) -> dict[str, Any]:
    """Attach a profile to a user or a team via the N:N association.

    Exactly one of ``user_id`` / ``team_id`` must be given. ``profile`` is a
    GUID or a profile name.
    """
    if bool(user_id) == bool(team_id):
        raise D365Error("exactly one of user_id or team_id is required.")
    profile_id = resolve_profile_id(backend, profile)
    if user_id is not None:
        nav, related_set, related_id, ptype = (
            _USER_NAV, "systemusers", user_id, "user")
    else:
        assert team_id is not None
        nav, related_set, related_id, ptype = (
            _TEAM_NAV, "teams", team_id, "team")
    result = entity_mod.associate(
        backend, PROFILES_SET, profile_id, nav, related_set, related_id,
    )
    if result.get("_dry_run"):
        result["would_assign"] = True
        return result
    return {
        "assigned": True,
        "profile": profile_id,
        "principal_type": ptype,
        "principal_id": related_id,
    }
