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
from typing import Any, cast

from crm.utils.d365_backend import D365Backend, D365Error, as_dict, odata_literal
from crm.core.metadata import label, label_text, maybe_publish, target_exists
from crm.core import dependencies as dep_mod
from crm.core import metadata_cache
from crm.core import metadata as _meta_mod
from crm.core import metadata_constraints as mc
from crm.core import references as ref_mod


def list_relationships(backend: D365Backend, logical_name: str) -> dict[str, Any]:
    """Return the relationships for an entity.

    Covers all three collections: `OneToMany` (entity is the "1"/referenced side),
    `ManyToOne` (entity is the "N"/referencing side — i.e. its own lookups), and
    `ManyToMany`. Omitting ManyToOne would hide an entity's own lookup columns.
    """
    # ReferencingEntityNavigationPropertyName is the single-valued nav property —
    # the `@odata.bind` key — so 1:N / N:1 rows carry it and callers can build
    # bind payloads without a second round-trip. #228.
    rel_select = (
        "SchemaName,ReferencedEntity,ReferencingEntity,ReferencingAttribute,"
        "ReferencingEntityNavigationPropertyName"
    )
    one_to_many = backend.get_collection(
        f"EntityDefinitions(LogicalName='{logical_name}')/OneToManyRelationships",
        params={"$select": rel_select},
    )
    many_to_one = backend.get_collection(
        f"EntityDefinitions(LogicalName='{logical_name}')/ManyToOneRelationships",
        params={"$select": rel_select},
    )
    many_to_many = backend.get_collection(
        f"EntityDefinitions(LogicalName='{logical_name}')/ManyToManyRelationships",
        params={"$select": "SchemaName,Entity1LogicalName,Entity2LogicalName,IntersectEntityName"},
    )
    return {
        "OneToMany": one_to_many,
        "ManyToOne": many_to_one,
        "ManyToMany": many_to_many,
    }


# Relationship-eligibility wiring per role. Each role maps to its Can* action
# (POST {"EntityName": …} → {"<Can…>": bool}) and its valid-partners GET
# function. Note the many-to-many function (GetValidManyToMany) is *global* — it
# takes no entity parameter, so its partner list is org-wide, not specific to
# the queried entity (only the CanManyToMany eligibility check is entity-scoped).
_RELATE_ROLES: dict[str, dict[str, str]] = {
    "referenced": {
        "action": "CanBeReferenced",
        "partners_fn": "GetValidReferencingEntities",
        "partners_param": "ReferencedEntityName",
    },
    "referencing": {
        "action": "CanBeReferencing",
        "partners_fn": "GetValidReferencedEntities",
        "partners_param": "ReferencingEntityName",
    },
    "many-to-many": {
        "action": "CanManyToMany",
        "partners_fn": "GetValidManyToMany",
        "partners_param": "",  # global function, no entity parameter
    },
}


def can_relate(
    backend: D365Backend,
    entity: str,
    *,
    role: str,
    valid_partners: bool = False,
) -> dict[str, Any]:
    """Check relationship eligibility, or list legal partner tables, for *entity*.

    ``role`` is one of ``referenced`` / ``referencing`` / ``many-to-many``.

    Default (``valid_partners=False``) answers "can *entity* play this role?" via
    the matching Can* action (`CanBeReferenced` / `CanBeReferencing` /
    `CanManyToMany`) → ``{"eligible": bool}``.

    With ``valid_partners=True`` it returns the tables *entity* may legally pair
    with via the matching GetValid* function → ``{"valid_partners": [...],
    "count": n}``. For ``many-to-many`` the underlying `GetValidManyToMany`
    function is org-global (it takes no entity argument), so the list is every
    N:N-capable table, not partners specific to *entity*.

    This is a read-only diagnostic: it issues no writes and has no `--dry-run`
    behavior.
    """
    cfg = _RELATE_ROLES.get(role)
    if cfg is None:
        raise D365Error(
            f"role must be one of {sorted(_RELATE_ROLES)}; got {role!r}."
        )
    if not entity:
        raise D365Error("entity is required.")

    if valid_partners:
        fn = cfg["partners_fn"]
        param = cfg["partners_param"]
        path = (
            f"{fn}({param}={odata_literal(entity)})" if param else fn
        )
        resp = as_dict(backend.get(path))
        names = cast("list[str]", resp.get("EntityNames") or [])
        return {
            "entity": entity,
            "as": role,
            "valid_partners": names,
            "count": len(names),
        }

    action = cfg["action"]
    resp = as_dict(backend.post(action, json_body={"EntityName": entity}))
    return {
        "entity": entity,
        "as": role,
        "eligible": bool(resp.get(action)),
    }


def _snake(name: str) -> str:
    """Convert a PascalCase key to snake_case (e.g. RollupView → rollup_view).

    Note: consecutive capitals (e.g. XMLFoo) are not split — acceptable for
    the known D365 cascade/menu key set where no such names appear.
    """
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


def _project_cascade(raw: dict[str, Any]) -> dict[str, Any]:
    """Strip @-annotation keys and snake_case the remaining cascade config keys."""
    return {_snake(k): v for k, v in raw.items() if not k.startswith("@")}


def _project_menu(raw: dict[str, Any]) -> dict[str, Any]:
    """Project AssociatedMenuConfiguration: snake_case keys, strip @, capture label."""
    out: dict[str, Any] = {}
    for k, v in raw.items():
        if k.startswith("@"):
            continue
        if k == "Label":
            # Extract the UserLocalizedLabel text
            lbl_dict = cast("dict[str, Any]", v) if isinstance(v, dict) else {}
            text = label_text(lbl_dict)
            if text:
                out["label"] = text
        else:
            out[_snake(k)] = v
    return out


def read_entity_relationships(
    backend: D365Backend, entity_logical_name: str
) -> list[dict[str, Any]]:
    """Read an entity's custom 1:N relationships as apply-spec relationship dicts.

    GETs ``EntityDefinitions(LogicalName='<entity>')/OneToManyRelationships``
    with ``$select`` that includes ``CascadeConfiguration`` and
    ``AssociatedMenuConfiguration`` — the endpoint returns the full 1:N subtype
    when queried via the entity navigation collection, so no per-item cast is
    needed.

    Filters to ``IsCustomRelationship == True`` (system relationships such as
    owner/createdby are skipped). For each custom 1:N, the result is projected
    into the apply-spec relationship sub-schema, plus a faithful capture of
    cascade and associated-menu config.

    N:N (ManyToMany) relationships are NOT emitted — the apply spec only
    supports 1:N creation via ``create_one_to_many``.

    Returns ``[]`` when the entity has no custom 1:N relationships.
    """
    raw = as_dict(backend.get(
        f"EntityDefinitions(LogicalName={odata_literal(entity_logical_name)})/OneToManyRelationships",
        params={
            "$select": (
                "SchemaName,ReferencedEntity,ReferencingEntity,"
                "ReferencingAttribute,IsCustomRelationship,"
                "CascadeConfiguration,AssociatedMenuConfiguration"
            )
        },
    ))
    rows: list[dict[str, Any]] = raw.get("value", [])

    result: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("IsCustomRelationship"):
            continue

        schema_name: str = row.get("SchemaName") or ""
        referenced_entity: str = row.get("ReferencedEntity") or ""
        referencing_entity: str = row.get("ReferencingEntity") or ""
        referencing_attr: str = row.get("ReferencingAttribute") or ""

        # Look up the display name and required level for the lookup column.
        lookup_display: str = referencing_attr
        required: str | None = None
        if referencing_entity and referencing_attr:
            try:
                attr_info = _meta_mod.attribute_info(
                    backend, referencing_entity, referencing_attr
                )
                dn_obj = cast("dict[str, Any]", attr_info.get("DisplayName") or {})
                text = label_text(dn_obj)
                if text:
                    lookup_display = text
                req_obj = cast("dict[str, Any]", attr_info.get("RequiredLevel") or {})
                req_val = req_obj.get("Value")
                if isinstance(req_val, str) and req_val:
                    required = req_val
            except D365Error:
                pass  # fall back to referencing_attr / no required key

        rel_dict: dict[str, Any] = {
            "schema_name": schema_name,
            "referenced_entity": referenced_entity,
            "referencing_entity": referencing_entity,
            "lookup_schema": referencing_attr,
            "lookup_display": lookup_display,
        }
        if required is not None:
            rel_dict["required"] = required

        cascade_raw = cast("dict[str, Any]", row.get("CascadeConfiguration") or {})
        if cascade_raw:
            rel_dict["cascade"] = _project_cascade(cascade_raw)

        menu_raw = cast("dict[str, Any]", row.get("AssociatedMenuConfiguration") or {})
        if menu_raw:
            rel_dict["associated_menu"] = _project_menu(menu_raw)

        result.append(rel_dict)

    return result


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
        path, params={"$select": "IsCustomRelationship,IsManaged,MetadataId"},
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
        _mid = rb.get("MetadataId")
        if isinstance(_mid, str) and _mid:
            deps = dep_mod.dependencies_by_id(backend, _mid, 10, for_="delete", kind="relationship")
        else:
            deps = dep_mod.retrieve_dependencies(
                backend, "relationship", schema_name, for_="delete"
            )
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    preview = backend.delete(path, extra_headers=headers)
    if isinstance(preview, dict) and preview.get("_dry_run"):
        result: dict[str, Any] = {
            "_dry_run": True,
            "would_delete": True,
            "schema_name": schema_name,
            "solution": solution,
        }
    else:
        result = {
            "deleted": True,
            "schema_name": schema_name,
            "solution": solution,
        }
    if deps is not None:
        result["can_delete"] = deps["can_delete"]
        result["blockers"] = deps["blockers"]
    if not backend.dry_run:
        metadata_cache.invalidate(backend.profile)
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
    is_hierarchical: bool = False,
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
    mc.validate_required(lookup_required, subject="lookup_required")
    for name, value in (
        ("cascade_assign", cascade_assign), ("cascade_delete", cascade_delete),
        ("cascade_reparent", cascade_reparent), ("cascade_share", cascade_share),
        ("cascade_unshare", cascade_unshare), ("cascade_merge", cascade_merge),
    ):
        mc.validate_cascade(value, subject=name)
    mc.validate_menu_behavior(menu_behavior)
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
    # A hierarchical 1:N is the self-referencing parent/child link the hierarchy
    # visualizations and `Above`/`Under` operators read; the server defaults the
    # flag false, so only emit it when requested.
    if is_hierarchical:
        body["IsHierarchical"] = True

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(
        "RelationshipDefinitions",
        json_body=body,
        extra_headers=headers,
    ))
    if result.get("_dry_run"):
        result["_exists"] = exists
        result["would_skip"] = exists and if_exists == "skip"
        # Resolve the entities this relationship binds so a dangling reference is
        # a pre-flight finding instead of a server fault at real write (#281).
        result["references"] = [
            ref_mod.make_reference(
                "referenced_entity", referenced_entity,
                ref_mod.entity_exists(backend, referenced_entity)),
            ref_mod.make_reference(
                "referencing_entity", referencing_entity,
                ref_mod.entity_exists(backend, referencing_entity)),
        ]
        return result

    entity_id_url = result.get("_entity_id_url")
    relationship_id = result.get("_entity_id")
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
    if not backend.dry_run:
        metadata_cache.invalidate(backend.profile)
    return out


def create_customer_relationships(
    backend: D365Backend,
    *,
    referencing_entity: str,
    lookup_schema: str,
    lookup_display: str,
    lookup_required: str = "None",
    lookup_description: str | None = None,
    publish: bool = False,
    solution: str | None = None,
    if_exists: str = "error",
) -> dict[str, Any]:
    """Create a Customer composite lookup (account + contact) atomically.

    A Customer column can't be made by a plain attribute `POST` or a single
    1:N relationship: it needs the `CreateCustomerRelationships` action, which
    creates one `ComplexLookupAttributeMetadata` whose `Targets` are fixed to
    `[account, contact]` plus the pair of 1:N relationships that back it, in one
    call. The relationship schema names are derived from the lookup
    (`<entity>_<lookup>_account` / `_contact`); unlike a single-target lookup,
    they aren't user-nameable because the action owns both.
    """
    if "_" not in lookup_schema:
        raise D365Error("lookup_schema must include a publisher prefix.")
    if if_exists not in ("error", "skip"):
        raise D365Error("if_exists must be 'error' or 'skip'.")
    mc.validate_required(lookup_required, subject="lookup_required")

    lookup_logical = lookup_schema.lower()
    attr_path = (
        f"EntityDefinitions(LogicalName='{referencing_entity}')"
        f"/Attributes(LogicalName='{lookup_logical}')"
    )
    exists = target_exists(backend, attr_path)
    if exists and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(
                f"Attribute {lookup_logical!r} already exists on entity "
                f"{referencing_entity!r}.",
                code="AlreadyExists",
            )
        return {
            "skipped": True,
            "exists": True,
            "entity": referencing_entity,
            "schema_name": lookup_schema,
            "logical_name": lookup_logical,
        }

    lookup_payload: dict[str, Any] = {
        "@odata.type": "Microsoft.Dynamics.CRM.ComplexLookupAttributeMetadata",
        "AttributeType": "Lookup",
        "AttributeTypeName": {"Value": "LookupType"},
        "SchemaName": lookup_schema,
        "DisplayName": label(lookup_display),
        "RequiredLevel": {"Value": lookup_required},
    }
    if lookup_description:
        lookup_payload["Description"] = label(lookup_description)
    body: dict[str, Any] = {
        "Lookup": lookup_payload,
        "OneToManyRelationships": [
            {
                "SchemaName": f"{referencing_entity}_{lookup_logical}_account",
                "ReferencedEntity": "account",
                "ReferencingEntity": referencing_entity,
            },
            {
                "SchemaName": f"{referencing_entity}_{lookup_logical}_contact",
                "ReferencedEntity": "contact",
                "ReferencingEntity": referencing_entity,
            },
        ],
    }

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(
        "CreateCustomerRelationships",
        json_body=body,
        extra_headers=headers,
    ))
    if result.get("_dry_run"):
        result["_exists"] = exists
        result["would_skip"] = exists and if_exists == "skip"
        return result

    out: dict[str, Any] = {
        "created": True,
        "kind": "Customer",
        "entity": referencing_entity,
        "schema_name": lookup_schema,
        "logical_name": lookup_logical,
        "targets": ["account", "contact"],
        "attribute_id": result.get("AttributeId"),
        "relationship_ids": result.get("RelationshipIds"),
        "solution": solution,
    }
    maybe_publish(backend, out, publish)
    if not backend.dry_run:
        metadata_cache.invalidate(backend.profile)
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
        mc.validate_menu_behavior(value, subject=name)

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
    relationship_id = result.get("_entity_id")
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
    if not backend.dry_run:
        metadata_cache.invalidate(backend.profile)
    return out
