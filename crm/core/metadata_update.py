"""Safe metadata UPDATE via retrieve-merge-write.

The Dataverse Web API updates metadata with HTTP `PUT`, which **replaces the
entire definition** — there is no `PATCH` for metadata, and a partial PUT
silently drops every property you omit. To make a partial update impossible,
every update here GETs the full current definition, deep-merges a *sparse*
`changes` dict onto it, and PUTs the complete merged body back. The
`MSCRM.MergeLabels: true` header preserves localized labels in languages we
did not touch; `Consistency: Strong` on the read makes the GET see the most
recent write.

Reference:
  https://learn.microsoft.com/power-apps/developer/data-platform/webapi/create-update-entity-definitions-using-web-api#update-table-definitions
  https://learn.microsoft.com/power-apps/developer/data-platform/webapi/create-update-column-definitions-using-web-api#update-a-column
  https://learn.microsoft.com/power-apps/developer/data-platform/webapi/create-update-entity-relationships-using-web-api#update-relationships
"""

from __future__ import annotations

from typing import Any, cast

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import label, maybe_publish

_VALID_REQUIRED = {"None", "Recommended", "ApplicationRequired"}
_VALID_CASCADE = {"NoCascade", "Cascade", "Active", "UserOwned", "RemoveLink", "Restrict"}
_VALID_MENU_BEHAVIOR = {"UseLabel", "UseCollectionName", "DoNotDisplay"}
_VALID_OWNERSHIP = {"UserOwned", "OrganizationOwned"}

# Read-time headers: Strong consistency so we read our own latest write.
_READ_HEADERS = {"Consistency": "Strong"}
# Write-time header: preserve localized labels in untouched languages.
_WRITE_HEADERS = {"MSCRM.MergeLabels": "true"}


def _deep_merge(base: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `changes` onto a copy of `base`.

    Dict values merge key-by-key; everything else (scalars, lists) replaces.
    Returns a new dict — `base` is not mutated.
    """
    out: dict[str, Any] = dict(base)
    for key, value in changes.items():
        existing = out.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            out[key] = _deep_merge(
                cast("dict[str, Any]", existing), cast("dict[str, Any]", value)
            )
        else:
            out[key] = value
    return out


def _shallow_diff(
    current: dict[str, Any], changes: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Targeted diff of the top-level keys in `changes` vs `current`.

    Returns `{key: {"old": <current>, "new": <merged>}}` for each top-level key
    touched by `changes`. Kept deliberately shallow for the --dry-run preview.
    """
    diff: dict[str, dict[str, Any]] = {}
    for key, value in changes.items():
        old = current.get(key)
        if isinstance(old, dict) and isinstance(value, dict):
            new: Any = _deep_merge(
                cast("dict[str, Any]", old), cast("dict[str, Any]", value)
            )
        else:
            new = value
        diff[key] = {"old": old, "new": new}
    return diff


def _read(backend: D365Backend, path: str, **kw: Any) -> dict[str, Any]:
    """GET that always hits the network, even in dry-run.

    Reads are side-effect free, so a dry-run still performs the GET in order to
    compute the merged PUT body and diff; only the PUT is suppressed.
    """
    saved = backend.dry_run
    backend.dry_run = False
    try:
        return as_dict(backend.get(path, **kw))
    finally:
        backend.dry_run = saved


def _retrieve_merge_write(
    backend: D365Backend,
    *,
    path: str,
    changes: dict[str, Any],
    solution: str | None,
    publish: bool,
) -> dict[str, Any]:
    """GET the full definition at `path`, deep-merge `changes`, PUT it back.

    The PUT body is always the full retrieved definition with `changes` merged
    on top — never the sparse `changes` alone — so omitted properties can never
    be dropped. Fails closed: a GET error propagates and no PUT is attempted.
    """
    current = _read(backend, path, extra_headers=dict(_READ_HEADERS))

    if backend.dry_run:
        merged = _deep_merge(current, changes)
        return {
            "_dry_run": True,
            "method": "PUT",
            "path": path,
            "body": merged,
            "diff": _shallow_diff(current, changes),
        }

    merged = _deep_merge(current, changes)
    write_headers = dict(_WRITE_HEADERS)
    if solution:
        write_headers["MSCRM.SolutionUniqueName"] = solution
    backend.put(path, json_body=merged, extra_headers=write_headers)

    out: dict[str, Any] = {"updated": True, "path": path, "solution": solution}
    maybe_publish(backend, out, publish)
    return out


def _build_entity_changes(
    *,
    display_name: str | None,
    display_collection_name: str | None,
    description: str | None,
    ownership: str | None,
    has_activities: bool | None,
    has_notes: bool | None,
) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if display_name is not None:
        changes["DisplayName"] = label(display_name)
    if display_collection_name is not None:
        changes["DisplayCollectionName"] = label(display_collection_name)
    if description is not None:
        changes["Description"] = label(description)
    if ownership is not None:
        if ownership not in _VALID_OWNERSHIP:
            raise D365Error(f"ownership must be one of {sorted(_VALID_OWNERSHIP)}.")
        changes["OwnershipType"] = ownership
    if has_activities is not None:
        changes["HasActivities"] = has_activities
    if has_notes is not None:
        changes["HasNotes"] = has_notes
    return changes


def update_entity(
    backend: D365Backend,
    logical_name: str,
    *,
    display_name: str | None = None,
    display_collection_name: str | None = None,
    description: str | None = None,
    ownership: str | None = None,
    has_activities: bool | None = None,
    has_notes: bool | None = None,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Update an entity (table) definition via safe retrieve-merge-write."""
    if not logical_name:
        raise D365Error("logical_name is required.")
    changes = _build_entity_changes(
        display_name=display_name,
        display_collection_name=display_collection_name,
        description=description,
        ownership=ownership,
        has_activities=has_activities,
        has_notes=has_notes,
    )
    if not changes:
        raise D365Error(
            "nothing to update — pass at least one of "
            "--display/--display-collection/--description/--ownership/"
            "--has-activities/--has-notes."
        )
    path = f"EntityDefinitions(LogicalName='{logical_name}')"
    out = _retrieve_merge_write(
        backend, path=path, changes=changes, solution=solution, publish=publish,
    )
    out.setdefault("logical_name", logical_name)
    return out


def _build_attribute_changes(
    *,
    display_name: str | None,
    description: str | None,
    required: str | None,
    max_length: int | None,
    precision: int | None,
    min_value: float | None,
    max_value: float | None,
    format_name: str | None,
) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if display_name is not None:
        changes["DisplayName"] = label(display_name)
    if description is not None:
        changes["Description"] = label(description)
    if required is not None:
        if required not in _VALID_REQUIRED:
            raise D365Error(f"required must be one of {sorted(_VALID_REQUIRED)}.")
        changes["RequiredLevel"] = {"Value": required}
    if max_length is not None:
        changes["MaxLength"] = max_length
    if precision is not None:
        changes["Precision"] = precision
    if min_value is not None:
        changes["MinValue"] = min_value
    if max_value is not None:
        changes["MaxValue"] = max_value
    if format_name is not None:
        changes["FormatName"] = {"Value": format_name}
    return changes


def update_attribute(
    backend: D365Backend,
    entity: str,
    attribute: str,
    *,
    display_name: str | None = None,
    description: str | None = None,
    required: str | None = None,
    max_length: int | None = None,
    precision: int | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    format_name: str | None = None,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Update an attribute (column) definition via safe retrieve-merge-write.

    The attribute is retrieved and written through its `@odata.type` cast path
    (e.g. `.../Microsoft.Dynamics.CRM.StringAttributeMetadata`); a metadata PUT
    of a typed attribute is rejected without the cast. The cast is derived from
    the `@odata.type` returned by the un-cast GET.
    """
    if not entity or not attribute:
        raise D365Error("entity and attribute are required.")
    changes = _build_attribute_changes(
        display_name=display_name,
        description=description,
        required=required,
        max_length=max_length,
        precision=precision,
        min_value=min_value,
        max_value=max_value,
        format_name=format_name,
    )
    if not changes:
        raise D365Error(
            "nothing to update — pass at least one of "
            "--display/--description/--required/--max-length/--precision/"
            "--min/--max/--format."
        )

    base_path = (
        f"EntityDefinitions(LogicalName='{entity}')"
        f"/Attributes(LogicalName='{attribute}')"
    )
    base = _read(backend, base_path, extra_headers=dict(_READ_HEADERS))
    odata_type = base.get("@odata.type")
    if not isinstance(odata_type, str) or not odata_type:
        raise D365Error(
            f"Could not determine attribute metadata type for {entity}.{attribute} "
            "(missing @odata.type); cannot build the cast path for PUT."
        )
    cast = odata_type.lstrip("#")
    cast_path = f"{base_path}/{cast}"

    out = _retrieve_merge_write(
        backend, path=cast_path, changes=changes, solution=solution, publish=publish,
    )
    out.setdefault("entity", entity)
    out.setdefault("attribute", attribute)
    return out


def _build_relationship_changes(
    *,
    cascade: dict[str, str] | None,
    menu_behavior: str | None,
    menu_label: str | None,
    menu_order: int | None,
) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if cascade:
        for name, value in cascade.items():
            if value not in _VALID_CASCADE:
                raise D365Error(
                    f"cascade {name} must be one of {sorted(_VALID_CASCADE)}."
                )
        changes["CascadeConfiguration"] = dict(cascade)
    menu: dict[str, Any] = {}
    if menu_behavior is not None:
        if menu_behavior not in _VALID_MENU_BEHAVIOR:
            raise D365Error(
                f"menu_behavior must be one of {sorted(_VALID_MENU_BEHAVIOR)}."
            )
        menu["Behavior"] = menu_behavior
    if menu_label is not None:
        menu["Label"] = label(menu_label)
    if menu_order is not None:
        menu["Order"] = menu_order
    if menu:
        changes["AssociatedMenuConfiguration"] = menu
    return changes


def update_relationship(
    backend: D365Backend,
    schema_name: str,
    *,
    cascade: dict[str, str] | None = None,
    menu_behavior: str | None = None,
    menu_label: str | None = None,
    menu_order: int | None = None,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Update a relationship definition via safe retrieve-merge-write.

    The relationship is resolved by `SchemaName` to its `MetadataId` and
    `@odata.type` cast (OneToMany vs ManyToMany), then retrieved and written
    through `RelationshipDefinitions(<MetadataId>)/<cast>`.
    """
    if not schema_name:
        raise D365Error("schema_name is required.")
    changes = _build_relationship_changes(
        cascade=cascade,
        menu_behavior=menu_behavior,
        menu_label=menu_label,
        menu_order=menu_order,
    )
    if not changes:
        raise D365Error(
            "nothing to update — pass at least one of "
            "--cascade-*/--menu-behavior/--menu-label/--menu-order."
        )

    resolve = _read(
        backend,
        f"RelationshipDefinitions(SchemaName='{schema_name}')",
        params={"$select": "MetadataId,SchemaName,RelationshipType"},
        extra_headers=dict(_READ_HEADERS),
    )
    metadata_id = resolve.get("MetadataId")
    if not isinstance(metadata_id, str) or not metadata_id:
        raise D365Error(
            f"Could not resolve MetadataId for relationship {schema_name!r}."
        )
    odata_type = resolve.get("@odata.type")
    rel_type = resolve.get("RelationshipType")
    if isinstance(odata_type, str) and odata_type:
        cast = odata_type.lstrip("#")
    elif rel_type == "ManyToManyRelationship":
        cast = "Microsoft.Dynamics.CRM.ManyToManyRelationshipMetadata"
    else:
        cast = "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata"
    path = f"RelationshipDefinitions({metadata_id})/{cast}"

    out = _retrieve_merge_write(
        backend, path=path, changes=changes, solution=solution, publish=publish,
    )
    out.setdefault("schema_name", schema_name)
    return out
