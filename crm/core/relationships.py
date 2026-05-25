"""Relationship metadata (1:N + N:N) — create + list helpers.

`create_one_to_many` and `create_many_to_many` use the dedicated
`CreateOneToManyRequest` / `CreateManyToManyRequest` unbound actions
rather than POSTing directly to `/RelationshipDefinitions`; the actions
also create the lookup attribute (1:N) or intersect entity (N:N)
atomically.
"""

from __future__ import annotations

import re
from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import _label, _maybe_publish  # pyright: ignore[reportPrivateUsage]

_VALID_CASCADE = {"NoCascade", "Cascade", "Active", "UserOwned", "RemoveLink", "Restrict"}
_VALID_MENU_BEHAVIOR = {"UseLabel", "UseCollectionName", "DoNotDisplay"}
_VALID_REQUIRED = {"None", "Recommended", "ApplicationRequired"}


def list_relationships(backend: D365Backend, logical_name: str) -> dict[str, Any]:
    """Return one-to-many and many-to-many relationships for an entity."""
    one_to_many = as_dict(backend.get(
        f"EntityDefinitions(LogicalName='{logical_name}')/OneToManyRelationships",
        params={"$select": "SchemaName,ReferencedEntity,ReferencingEntity,ReferencingAttribute"},
    ))
    many_to_many = as_dict(backend.get(
        f"EntityDefinitions(LogicalName='{logical_name}')/ManyToManyRelationships",
        params={"$select": "SchemaName,Entity1LogicalName,Entity2LogicalName,IntersectEntityName"},
    ))
    return {
        "OneToMany": one_to_many.get("value", []),
        "ManyToMany": many_to_many.get("value", []),
    }


def _parse_relationship_id(entity_id_url: str | None) -> str | None:
    if not entity_id_url:
        return None
    match = re.search(r"RelationshipDefinitions\(([0-9a-fA-F-]{36})\)", entity_id_url)
    return match.group(1) if match else None


def create_one_to_many(
    backend: D365Backend,
    *,
    schema_name: str,
    referenced_entity: str,
    referencing_entity: str,
    lookup_schema: str,
    lookup_display: str,
    lookup_required: str = "None",
    lookup_description: str | None = None,
    cascade_assign: str = "NoCascade",
    cascade_delete: str = "RemoveLink",
    cascade_reparent: str = "NoCascade",
    cascade_share: str = "NoCascade",
    cascade_unshare: str = "NoCascade",
    cascade_merge: str = "NoCascade",
    menu_label: str | None = None,
    menu_behavior: str = "UseLabel",
    menu_order: int = 10000,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Create a 1:N relationship + lookup attribute atomically.

    Calls `POST /CreateOneToManyRequest`. Read-back populates
    `schema_name` and `referencing_attribute` from the server.
    """
    if "_" not in schema_name:
        raise D365Error(
            "schema_name must include a publisher prefix, e.g. 'new_account_new_project'."
        )
    if "_" not in lookup_schema:
        raise D365Error("lookup_schema must include a publisher prefix.")
    if lookup_required not in _VALID_REQUIRED:
        raise D365Error(f"lookup_required must be one of {sorted(_VALID_REQUIRED)}.")
    for name, value in (
        ("cascade_assign", cascade_assign), ("cascade_delete", cascade_delete),
        ("cascade_reparent", cascade_reparent), ("cascade_share", cascade_share),
        ("cascade_unshare", cascade_unshare), ("cascade_merge", cascade_merge),
    ):
        if value not in _VALID_CASCADE:
            raise D365Error(f"{name} must be one of {sorted(_VALID_CASCADE)}.")
    if menu_behavior not in _VALID_MENU_BEHAVIOR:
        raise D365Error(f"menu_behavior must be one of {sorted(_VALID_MENU_BEHAVIOR)}.")

    lookup_payload: dict[str, Any] = {
        "@odata.type": "Microsoft.Dynamics.CRM.LookupAttributeMetadata",
        "SchemaName": lookup_schema,
        "DisplayName": _label(lookup_display),
        "RequiredLevel": {"Value": lookup_required},
    }
    if lookup_description:
        lookup_payload["Description"] = _label(lookup_description)

    body: dict[str, Any] = {
        "OneToManyRelationship": {
            "@odata.type": "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
            "SchemaName": schema_name,
            "ReferencedEntity": referenced_entity,
            "ReferencingEntity": referencing_entity,
            "AssociatedMenuConfiguration": {
                "Behavior": menu_behavior,
                "Group": "Details",
                "Label": _label(menu_label) if menu_label else None,
                "Order": menu_order,
            },
            "CascadeConfiguration": {
                "Assign": cascade_assign,
                "Delete": cascade_delete,
                "Reparent": cascade_reparent,
                "Share": cascade_share,
                "Unshare": cascade_unshare,
                "Merge": cascade_merge,
            },
        },
        "Lookup": lookup_payload,
    }
    if solution:
        body["SolutionUniqueName"] = solution

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(
        "CreateOneToManyRequest",
        json_body=body,
        extra_headers=headers,
    ))
    if result.get("_dry_run"):
        return result

    entity_id_url = result.get("_entity_id_url")
    relationship_id = _parse_relationship_id(entity_id_url)
    schema_readback: str | None = None
    referencing_attr: str | None = None
    lookup_error: str | None = None
    if not relationship_id:
        lookup_error = (
            f"Could not parse RelationshipId from response: {entity_id_url!r}"
        )
    else:
        try:
            rb = as_dict(backend.get(
                f"RelationshipDefinitions({relationship_id})",
                params={"$select": "SchemaName,ReferencingAttribute"},
            ))
            schema_readback = rb.get("SchemaName")
            referencing_attr = rb.get("ReferencingAttribute")
        except D365Error as exc:
            lookup_error = f"Read-back failed: {exc}"

    out: dict[str, Any] = {
        "created": True,
        "kind": "OneToMany",
        "schema_name": schema_readback or schema_name,
        "referenced_entity": referenced_entity,
        "referencing_entity": referencing_entity,
        "referencing_attribute": referencing_attr,
        "relationship_id": relationship_id,
        "metadata_id_url": entity_id_url,
        "solution": solution,
    }
    if lookup_error:
        out["relationship_lookup_error"] = lookup_error
    _maybe_publish(backend, out, publish)
    return out
