"""Record-to-record connection roles via the D365 Web API.

Wraps the ``connectionrole`` + ``connectionroleobjecttypecode`` entities and the
self-referential ``connectionroleassociation_association`` N:N relationship used
to pair reciprocal (matching) roles:

- :func:`create_role` creates a named role, optionally in a category.
- :func:`scope` restricts a role to records of a given entity type (one
  ``connectionroleobjecttypecode`` per entity).
- :func:`match` pairs two roles as reciprocal/matching via the N:N association.

Every public function takes the backend first and returns a plain dict — the
Click layer owns all formatting. Click-free so the module stays pyright-strict.
"""

from __future__ import annotations

from typing import Any

from crm.core import entity as entity_mod
from crm.utils.d365_backend import (
    D365Backend,
    D365Error,
    as_dict,
    normalize_guid,
)

# ── Constants ──────────────────────────────────────────────────────────────

ROLES_SET = "connectionroles"
OBJECTTYPECODES_SET = "connectionroleobjecttypecodes"
_ROLE_ID = "connectionroleid"
_OTC_ID = "connectionroleobjecttypecodeid"
# Self-referential N:N navigation property pairing reciprocal/matching roles.
_MATCH_NAV = "connectionroleassociation_association"

# connectionrole.category — the connectionrole_category global choice. Friendly
# CLI names map to the system option values (verified against the Dataverse /
# on-prem v9.x connectionrole_category option set).
CATEGORIES: dict[str, int] = {
    "business": 1,
    "family": 2,
    "social": 3,
    "sales": 4,
    "other": 5,
    "stakeholder": 1000,
    "sales-team": 1001,
    "service": 1002,
}


# ── Role resolution ──────────────────────────────────────────────────────


def resolve_role_id(backend: D365Backend, role: str) -> str:
    """Resolve a connection-role reference (GUID or name) to its id.

    A GUID is used as-is; anything else is treated as the role ``name`` and
    looked up with an exact match. Raises :class:`D365Error` (``NotFound``) when
    no role matches. The read runs for real even under dry-run so a preview path
    still resolves the id it needs.
    """
    gid = normalize_guid(role)
    if gid is not None:
        return gid
    rid = backend.resolve_id_by_name(
        ROLES_SET, filter_field="name", id_field=_ROLE_ID, value=role,
    )
    if rid is None:
        raise D365Error(f"No connection role named {role!r}.", code="NotFound")
    return rid


# ── Writes ─────────────────────────────────────────────────────────────────


def create_role(
    backend: D365Backend,
    *,
    name: str,
    category: str | None = None,
    description: str | None = None,
    solution: str | None = None,
) -> dict[str, Any]:
    """Create a connection role. Returns ``{created, connectionroleid, ...}``.

    ``category`` is an optional friendly name from :data:`CATEGORIES`.
    """
    if not name:
        raise D365Error("name is required.")
    body: dict[str, Any] = {"name": name}
    if category is not None:
        if category not in CATEGORIES:
            raise D365Error(
                f"unknown category {category!r}; expected one of {sorted(CATEGORIES)}.",
            )
        body["category"] = CATEGORIES[category]
    if description is not None:
        body["description"] = description
    result = as_dict(backend.post(
        ROLES_SET, json_body=body, solution=solution,
    ))
    if result.get("_dry_run"):
        result["would_create"] = True
        return result
    role_id = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "name": name,
        _ROLE_ID: role_id,
        "solution": solution,
    }
    if category is not None:
        out["category"] = CATEGORIES[category]
        out["category_name"] = category
    if not role_id:
        out["connectionrole_lookup_error"] = (
            "Could not parse connectionroleid from response: "
            f"{result.get('_entity_id_url')!r}"
        )
    return out


def scope(
    backend: D365Backend,
    *,
    role: str,
    entity: str,
    solution: str | None = None,
) -> dict[str, Any]:
    """Restrict ``role`` to records of ``entity`` (a logical name).

    Creates one ``connectionroleobjecttypecode`` linking the role to the entity;
    call repeatedly to scope a role to several entity types. ``role`` is a GUID
    or a role name. Returns ``{created, connectionroleobjecttypecodeid, ...}``.
    """
    if not entity:
        raise D365Error("entity is required.")
    role_id = resolve_role_id(backend, role)
    body: dict[str, Any] = {
        "associatedobjecttypecode": entity,
        f"{_ROLE_ID}@odata.bind": f"/{ROLES_SET}({role_id})",
    }
    result = as_dict(backend.post(
        OBJECTTYPECODES_SET, json_body=body, solution=solution,
    ))
    if result.get("_dry_run"):
        result["would_create"] = True
        return result
    otc_id = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        _OTC_ID: otc_id,
        "role": role_id,
        "entity": entity,
        "solution": solution,
    }
    if not otc_id:
        out["connectionroleobjecttypecode_lookup_error"] = (
            "Could not parse connectionroleobjecttypecodeid from response: "
            f"{result.get('_entity_id_url')!r}"
        )
    return out


def match(
    backend: D365Backend,
    *,
    role_a: str,
    role_b: str,
) -> dict[str, Any]:
    """Pair two roles as reciprocal/matching via the N:N association.

    Each argument is a GUID or a role name. The pairing is stored in the
    ``connectionroleassociation`` intersect table, which is not a solution
    component, so there is no ``--solution`` to plumb here. Returns
    ``{matched, role_a, role_b}``.
    """
    a_id = resolve_role_id(backend, role_a)
    b_id = resolve_role_id(backend, role_b)
    result = entity_mod.associate(
        backend, ROLES_SET, a_id, _MATCH_NAV, ROLES_SET, b_id,
    )
    if result.get("_dry_run"):
        result["would_match"] = True
        return result
    return {"matched": True, "role_a": a_id, "role_b": b_id}
