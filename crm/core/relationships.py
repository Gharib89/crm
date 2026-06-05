"""Relationship metadata (1:N + N:N) — create + list helpers.

`create_one_to_many` and `create_many_to_many` POST to the
`/RelationshipDefinitions` entity set with the `@odata.type` discriminator
(`OneToManyRelationshipMetadata` / `ManyToManyRelationshipMetadata`). The
lookup attribute (1:N, via a `Lookup` deep insert) and the intersect entity
(N:N) are created in the same operation. (`CreateOneToManyRequest` /
`CreateManyToManyRequest` are SDK message names, not Web API segments.)
"""

from __future__ import annotations

import re
from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import label, maybe_publish, target_exists
from crm.core import dependencies as dep_mod

_VALID_CASCADE = {"NoCascade", "Cascade", "Active", "UserOwned", "RemoveLink", "Restrict"}
_VALID_MENU_BEHAVIOR = {"UseLabel", "UseCollectionName", "DoNotDisplay"}
_VALID_REQUIRED = {"None", "Recommended", "ApplicationRequired"}


def list_relationships(backend: D365Backend, logical_name: str) -> dict[str, Any]:
    """Return the relationships for an entity.

    Covers all three collections: `OneToMany` (entity is the "1"/referenced side),
    `ManyToOne` (entity is the "N"/referencing side — i.e. its own lookups), and
    `ManyToMany`. Omitting ManyToOne would hide an entity's own lookup columns.
    """
    rel_select = "SchemaName,ReferencedEntity,ReferencingEntity,ReferencingAttribute"
    one_to_many = as_dict(backend.get(
        f"EntityDefinitions(LogicalName='{logical_name}')/OneToManyRelationships",
        params={"$select": rel_select},
    ))
    many_to_one = as_dict(backend.get(
        f"EntityDefinitions(LogicalName='{logical_name}')/ManyToOneRelationships",
        params={"$select": rel_select},
    ))
    many_to_many = as_dict(backend.get(
        f"EntityDefinitions(LogicalName='{logical_name}')/ManyToManyRelationships",
        params={"$select": "SchemaName,Entity1LogicalName,Entity2LogicalName,IntersectEntityName"},
    ))
    return {
        "OneToMany": one_to_many.get("value", []),
        "ManyToOne": many_to_one.get("value", []),
        "ManyToMany": many_to_many.get("value", []),
    }


def _parse_relationship_id(entity_id_url: str | None) -> str | None:
    if not entity_id_url:
        return None
    match = re.search(r"RelationshipDefinitions\(([0-9a-fA-F-]{36})\)", entity_id_url)
    return match.group(1) if match else None


def delete_relationship(
    backend: D365Backend,
    schema_name: str,
    *,
    solution: str | None = None,
    check_dependencies: bool = False,
) -> dict[str, Any]:
    """Delete a custom relationship (1:N or N:N) by schema name.

    Pre-flight: refuses if `IsCustomRelationship=False` or `IsManaged=True`.
    Server enforces remaining-dependency checks and returns 4xx on conflict.

    Args:
        check_dependencies: When True, call RetrieveDependenciesForDelete
            before the DELETE and fold ``can_delete`` + ``blockers`` into the
            result. Informational only — does not abort the delete.
    """
    if not schema_name:
        raise D365Error("schema_name is required.")
    path = f"RelationshipDefinitions(SchemaName='{schema_name}')"
    rb = as_dict(backend.get(
        path, params={"$select": "IsCustomRelationship,IsManaged"},
    ))
    if rb.get("IsCustomRelationship") is False:
        raise D365Error(
            f"{schema_name!r} is not a custom relationship; refusing to delete.",
            code="NotCustomRelationship",
        )
    if rb.get("IsManaged") is True:
        raise D365Error(
            f"{schema_name!r} is managed; uninstall the parent solution to remove it.",
            code="ManagedRelationship",
        )
    deps = None
    if check_dependencies:
        deps = dep_mod.retrieve_dependencies(
            backend, "relationship", schema_name, for_="delete"
        )
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    backend.delete(path, extra_headers=headers)
    result: dict[str, Any] = {"deleted": True, "schema_name": schema_name, "solution": solution}
    if deps is not None:
        result["can_delete"] = deps["can_delete"]
        result["blockers"] = deps["blockers"]
    return result


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
    menu_behavior: str = "UseCollectionName",
    menu_order: int = 10000,
    publish: bool = False,
    solution: str | None = None,
    if_exists: str = "error",
) -> dict[str, Any]:
    """Create a 1:N relationship + lookup attribute atomically.

    `POST /RelationshipDefinitions` with a `Lookup` deep insert. Read-back
    populates `schema_name` and `referencing_attribute` from the server.
    """
    if "_" not in schema_name:
        raise D365Error(
            "schema_name must include a publisher prefix, e.g. 'new_account_new_project'."
        )
    if if_exists not in ("error", "skip"):
        raise D365Error("if_exists must be 'error' or 'skip'.")
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
    if menu_behavior == "UseLabel" and not menu_label:
        raise D365Error(
            "menu_behavior 'UseLabel' requires --menu-label; the server rejects a "
            "custom-label associated menu without a label."
        )

    exists = target_exists(
        backend, f"RelationshipDefinitions(SchemaName='{schema_name}')"
    )
    if exists and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(
                f"Relationship {schema_name!r} already exists.",
                code="AlreadyExists",
            )
        return {
            "skipped": True,
            "exists": True,
            "kind": "OneToMany",
            "schema_name": schema_name,
        }

    lookup_payload: dict[str, Any] = {
        "@odata.type": "Microsoft.Dynamics.CRM.LookupAttributeMetadata",
        "SchemaName": lookup_schema,
        "DisplayName": label(lookup_display),
        "RequiredLevel": {"Value": lookup_required},
    }
    if lookup_description:
        lookup_payload["Description"] = label(lookup_description)

    menu_config: dict[str, Any] = {
        "Behavior": menu_behavior,
        "Group": "Details",
        "Order": menu_order,
    }
    if menu_label:
        menu_config["Label"] = label(menu_label)
    body: dict[str, Any] = {
        "@odata.type": "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
        "SchemaName": schema_name,
        "ReferencedEntity": referenced_entity,
        "ReferencingEntity": referencing_entity,
        "AssociatedMenuConfiguration": menu_config,
        "CascadeConfiguration": {
            "Assign": cascade_assign,
            "Delete": cascade_delete,
            "Reparent": cascade_reparent,
            "Share": cascade_share,
            "Unshare": cascade_unshare,
            "Merge": cascade_merge,
        },
        # The lookup attribute is created in the same operation as a deep insert
        # on the Lookup single-valued navigation property.
        "Lookup": lookup_payload,
    }

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(
        "RelationshipDefinitions",
        json_body=body,
        extra_headers=headers,
    ))
    if result.get("_dry_run"):
        result["_exists"] = exists
        result["would_skip"] = exists and if_exists == "skip"
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
            # Cast to the 1:N subtype — ReferencingAttribute is not on the
            # RelationshipMetadataBase type returned by the uncast endpoint.
            rb = as_dict(backend.get(
                f"RelationshipDefinitions({relationship_id})"
                "/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata",
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
    maybe_publish(backend, out, publish)
    return out


def create_many_to_many(
    backend: D365Backend,
    *,
    schema_name: str,
    entity1_logical: str,
    entity2_logical: str,
    intersect_entity: str,
    entity1_menu_label: str | None = None,
    entity1_menu_behavior: str = "UseCollectionName",
    entity1_menu_order: int = 10000,
    entity2_menu_label: str | None = None,
    entity2_menu_behavior: str = "UseCollectionName",
    entity2_menu_order: int = 10000,
    publish: bool = False,
    solution: str | None = None,
    if_exists: str = "error",
) -> dict[str, Any]:
    """Create an N:N relationship via `POST /RelationshipDefinitions`.

    Server creates the intersect entity (`intersect_entity` is its logical name).
    """
    if "_" not in schema_name:
        raise D365Error(
            "schema_name must include a publisher prefix."
        )
    if if_exists not in ("error", "skip"):
        raise D365Error("if_exists must be 'error' or 'skip'.")
    if entity1_logical == entity2_logical:
        raise D365Error("self N:N is not supported by Dataverse Web API.")
    for name, value in (
        ("entity1_menu_behavior", entity1_menu_behavior),
        ("entity2_menu_behavior", entity2_menu_behavior),
    ):
        if value not in _VALID_MENU_BEHAVIOR:
            raise D365Error(f"{name} must be one of {sorted(_VALID_MENU_BEHAVIOR)}.")

    exists = target_exists(
        backend, f"RelationshipDefinitions(SchemaName='{schema_name}')"
    )
    if exists and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(
                f"Relationship {schema_name!r} already exists.",
                code="AlreadyExists",
            )
        return {
            "skipped": True,
            "exists": True,
            "kind": "ManyToMany",
            "schema_name": schema_name,
        }

    entity1_menu: dict[str, Any] = {
        "Behavior": entity1_menu_behavior,
        "Group": "Details",
        "Order": entity1_menu_order,
    }
    if entity1_menu_label:
        entity1_menu["Label"] = label(entity1_menu_label)
    entity2_menu: dict[str, Any] = {
        "Behavior": entity2_menu_behavior,
        "Group": "Details",
        "Order": entity2_menu_order,
    }
    if entity2_menu_label:
        entity2_menu["Label"] = label(entity2_menu_label)
    body: dict[str, Any] = {
        "@odata.type": "Microsoft.Dynamics.CRM.ManyToManyRelationshipMetadata",
        "SchemaName": schema_name,
        "Entity1LogicalName": entity1_logical,
        "Entity2LogicalName": entity2_logical,
        "IntersectEntityName": intersect_entity,
        "Entity1AssociatedMenuConfiguration": entity1_menu,
        "Entity2AssociatedMenuConfiguration": entity2_menu,
    }

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(
        "RelationshipDefinitions",
        json_body=body,
        extra_headers=headers,
    ))
    if result.get("_dry_run"):
        result["_exists"] = exists
        result["would_skip"] = exists and if_exists == "skip"
        return result

    entity_id_url = result.get("_entity_id_url")
    relationship_id = _parse_relationship_id(entity_id_url)
    schema_readback: str | None = None
    intersect_readback: str | None = None
    lookup_error: str | None = None
    if not relationship_id:
        lookup_error = (
            f"Could not parse RelationshipId from response: {entity_id_url!r}"
        )
    else:
        try:
            # Cast to the N:N subtype — IntersectEntityName is not on the
            # RelationshipMetadataBase type returned by the uncast endpoint.
            rb = as_dict(backend.get(
                f"RelationshipDefinitions({relationship_id})"
                "/Microsoft.Dynamics.CRM.ManyToManyRelationshipMetadata",
                params={"$select": "SchemaName,IntersectEntityName"},
            ))
            schema_readback = rb.get("SchemaName")
            intersect_readback = rb.get("IntersectEntityName")
        except D365Error as exc:
            lookup_error = f"Read-back failed: {exc}"

    out: dict[str, Any] = {
        "created": True,
        "kind": "ManyToMany",
        "schema_name": schema_readback or schema_name,
        "intersect_entity": intersect_readback or intersect_entity,
        "relationship_id": relationship_id,
        "metadata_id_url": entity_id_url,
        "solution": solution,
    }
    if lookup_error:
        out["relationship_lookup_error"] = lookup_error
    maybe_publish(backend, out, publish)
    return out
