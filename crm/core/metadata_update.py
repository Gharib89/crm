"""Safe metadata UPDATE via retrieve-merge-write.

The Dataverse Web API updates metadata with HTTP `PUT`, which **replaces the
entire definition** — there is no `PATCH` for metadata, and a partial PUT
silently drops every property you omit. To make a partial update impossible,
every update here GETs the full current definition, deep-merges a *sparse*
`changes` dict onto it, and PUTs the complete merged body back. The
`MSCRM.MergeLabels: true` header preserves localized labels in languages we
did not touch.

Reference:
  https://learn.microsoft.com/power-apps/developer/data-platform/webapi/create-update-entity-definitions-using-web-api#update-table-definitions
  https://learn.microsoft.com/power-apps/developer/data-platform/webapi/create-update-column-definitions-using-web-api#update-a-column
  https://learn.microsoft.com/power-apps/developer/data-platform/webapi/create-update-entity-relationships-using-web-api#update-relationships
"""

from __future__ import annotations

from typing import Any, cast

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import label, maybe_publish
from crm.core import metadata_cache
from crm.core import metadata_constraints as mc

# Attribute @odata.type cast names (without leading '#') by capability, derived
# from the single KINDS table so the create and update paths cannot diverge.
_STRING_TYPE = mc.KINDS["string"].cast
_DATETIME_TYPE = mc.KINDS["datetime"].cast
_LENGTH_TYPES = frozenset(mc.KINDS[k].cast for k in ("string", "memo"))
_NUMERIC_TYPES = frozenset(
    mc.KINDS[k].cast for k in ("integer", "bigint", "decimal", "double", "money")
)
_PRECISION_TYPES = frozenset(mc.KINDS[k].cast for k in ("decimal", "double", "money"))

# Relationship @odata.type cast for many-to-many (not an attribute kind).
_MANY_TO_MANY_CAST = "Microsoft.Dynamics.CRM.ManyToManyRelationshipMetadata"

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
    return as_dict(backend.get(path, **kw))


def _retrieve_merge_write(
    backend: D365Backend,
    *,
    path: str,
    changes: dict[str, Any],
    solution: str | None,
    publish: bool,
    write_path: str | None = None,
    ensure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """GET the full definition at `path`, deep-merge `changes`, PUT it back.

    The PUT body is always the full retrieved definition with `changes` merged
    on top — never the sparse `changes` alone — so omitted properties can never
    be dropped. Fails closed: a GET error propagates and no PUT is attempted.

    `path` must be the path that returns the *complete* definition. For typed
    attributes that is the `@odata.type` cast path (the un-cast projection omits
    type-specific properties); callers resolve the cast before calling here.

    `write_path` is the PUT target and defaults to `path`. Relationships read
    their merge base from the typed cast `path` (the only projection that carries
    CascadeConfiguration/AssociatedMenuConfiguration) but must PUT to the un-cast
    `write_path` — Dataverse rejects a PUT to a relationship cast segment with
    HTTP 405.

    `ensure` supplies keys forced into the merge base only when the GET omitted
    them — e.g. the `@odata.type` discriminator a minimal-metadata cast GET drops
    but the polymorphic un-cast PUT needs. A server-returned value always wins,
    and because these are base defaults (not `changes`) they never appear in the
    dry-run diff.
    """
    target = write_path or path
    current = _read(backend, path)
    if ensure:
        defaults = {k: v for k, v in ensure.items() if k not in current}
        if defaults:
            current = {**defaults, **current}

    if backend.dry_run:
        merged = _deep_merge(current, changes)
        return {
            "_dry_run": True,
            "method": "PUT",
            "path": target,
            "body": merged,
            "diff": _shallow_diff(current, changes),
        }

    merged = _deep_merge(current, changes)
    write_headers = dict(_WRITE_HEADERS)
    if solution:
        write_headers["MSCRM.SolutionUniqueName"] = solution
    backend.put(target, json_body=merged, extra_headers=write_headers)

    out: dict[str, Any] = {"updated": True, "path": target, "solution": solution}
    maybe_publish(backend, out, publish)
    if not backend.dry_run:
        metadata_cache.invalidate(backend.profile)
    return out


def _build_entity_changes(
    *,
    display_name: str | None,
    display_collection_name: str | None,
    description: str | None,
    ownership: str | None,
    has_activities: bool | None,
    has_notes: bool | None,
    is_sla_enabled: bool | None,
) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if display_name is not None:
        changes["DisplayName"] = label(display_name)
    if display_collection_name is not None:
        changes["DisplayCollectionName"] = label(display_collection_name)
    if description is not None:
        changes["Description"] = label(description)
    if ownership is not None:
        mc.validate_ownership(ownership)
        changes["OwnershipType"] = ownership
    if has_activities is not None:
        changes["HasActivities"] = has_activities
    if has_notes is not None:
        changes["HasNotes"] = has_notes
    if is_sla_enabled is not None:
        # IsSLAEnabled is a BooleanManagedProperty; merging the sparse {"Value": …}
        # onto the retrieved property object preserves CanBeChanged /
        # ManagedPropertyLogicalName (the retrieve-merge-write contract).
        changes["IsSLAEnabled"] = {"Value": is_sla_enabled}
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
    is_sla_enabled: bool | None = None,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Update an entity (table) definition via safe retrieve-merge-write.

    Note: `--ownership` mirrors the create surface, but Dataverse rejects an
    OwnershipType change after creation; passing a different value yields a
    server-side D365Error. This is expected, not a client bug.
    """
    if not logical_name:
        raise D365Error("logical_name is required.")
    changes = _build_entity_changes(
        display_name=display_name,
        display_collection_name=display_collection_name,
        description=description,
        ownership=ownership,
        has_activities=has_activities,
        has_notes=has_notes,
        is_sla_enabled=is_sla_enabled,
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
    odata_type: str,
    display_name: str | None,
    description: str | None,
    required: str | None,
    max_length: int | None,
    precision: int | None,
    min_value: float | None,
    max_value: float | None,
    format_name: str | None,
) -> dict[str, Any]:
    """Build the sparse `changes` dict, validated against the attribute type.

    `odata_type` is the attribute's `@odata.type` cast name (no leading '#').
    Numeric/length/format options that are incompatible with the type are
    rejected client-side (mirrors `_forbid` on the create path) instead of
    producing an invalid PUT body, and `--format` writes the correct property
    for the type: `Format` (string) on DateTime, `FormatName` (Value-wrapped)
    on string attributes.
    """
    changes: dict[str, Any] = {}
    if display_name is not None:
        changes["DisplayName"] = label(display_name)
    if description is not None:
        changes["Description"] = label(description)
    if required is not None:
        mc.validate_required(required)
        changes["RequiredLevel"] = {"Value": required}
    if max_length is not None:
        if odata_type not in _LENGTH_TYPES:
            raise D365Error("--max-length is only valid for string/memo attributes.")
        changes["MaxLength"] = max_length
    if precision is not None:
        if odata_type not in _PRECISION_TYPES:
            raise D365Error(
                "--precision is only valid for decimal/double/money attributes."
            )
        kind = mc.kind_for_cast(odata_type)
        if kind is None:  # unreachable: _PRECISION_TYPES is derived from mc.KINDS
            raise D365Error(f"no attribute kind for @odata.type {odata_type!r}.")
        mc.validate_precision(kind, precision, subject="--precision")
        changes["Precision"] = precision
    if min_value is not None:
        if odata_type not in _NUMERIC_TYPES:
            raise D365Error("--min is only valid for numeric attributes.")
        changes["MinValue"] = min_value
    if max_value is not None:
        if odata_type not in _NUMERIC_TYPES:
            raise D365Error("--max is only valid for numeric attributes.")
        changes["MaxValue"] = max_value
    if format_name is not None:
        if odata_type == _DATETIME_TYPE:
            mc.validate_format("datetime", format_name, subject="--format")
            changes["Format"] = format_name
        elif odata_type == _STRING_TYPE:
            mc.validate_format("string", format_name, subject="--format")
            changes["FormatName"] = {"Value": format_name}
        else:
            raise D365Error(
                "--format is only valid for string or datetime attributes."
            )
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
    of a typed attribute is rejected without the cast. Two GETs are required:

    1. The un-cast GET (`base_path`) discovers the `@odata.type` cast, which is
       all the base `AttributeMetadata` projection carries — type-specific
       properties (`MaxLength`, `Precision`, `Format`, `MinValue`/`MaxValue`,
       …) are *absent* from it.
    2. The merge base is then read from the derived cast path, which returns the
       full typed definition. Merging the sparse `changes` onto the cast body
       (not the un-cast body) is what stops a full PUT from silently dropping
       type-specific properties the caller did not touch.

    `_retrieve_merge_write` performs the typed GET itself (no `current=`),
    mirroring `update_relationship`, so the cast path is read exactly once.
    """
    if not entity or not attribute:
        raise D365Error("entity and attribute are required.")
    if all(
        v is None
        for v in (
            display_name, description, required, max_length, precision,
            min_value, max_value, format_name,
        )
    ):
        raise D365Error(
            "nothing to update — pass at least one of "
            "--display/--description/--required/--max-length/--precision/"
            "--min/--max/--format."
        )

    base_path = (
        f"EntityDefinitions(LogicalName='{entity}')"
        f"/Attributes(LogicalName='{attribute}')"
    )
    base = _read(backend, base_path)
    odata_type = base.get("@odata.type")
    if not isinstance(odata_type, str) or not odata_type:
        raise D365Error(
            f"Could not determine attribute metadata type for {entity}.{attribute} "
            "(missing @odata.type); cannot build the cast path for PUT."
        )
    odata_cast = odata_type.lstrip("#")
    cast_path = f"{base_path}/{odata_cast}"

    changes = _build_attribute_changes(
        odata_type=odata_cast,
        display_name=display_name,
        description=description,
        required=required,
        max_length=max_length,
        precision=precision,
        min_value=min_value,
        max_value=max_value,
        format_name=format_name,
    )

    out = _retrieve_merge_write(
        backend, path=cast_path, changes=changes, solution=solution,
        publish=publish,
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
            if name not in mc.CASCADE_KEYS:
                raise D365Error(
                    f"cascade key {name!r} is not valid; must be one of "
                    f"{sorted(mc.CASCADE_KEYS)}."
                )
            mc.validate_cascade(value, subject=f"cascade {name}")
        changes["CascadeConfiguration"] = dict(cascade)
    menu: dict[str, Any] = {}
    if menu_behavior is not None:
        mc.validate_menu_behavior(menu_behavior)
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
    `@odata.type` cast (OneToMany vs ManyToMany). The merge base is *read* from
    the typed cast path `RelationshipDefinitions(<MetadataId>)/<cast>` — the only
    projection that carries `CascadeConfiguration`/`AssociatedMenuConfiguration`
    — but the merged definition is *written* (PUT) to the un-cast entity-set path
    `RelationshipDefinitions(<MetadataId>)`, with the `@odata.type` discriminator
    in the body. A PUT to the cast segment returns HTTP 405 on both on-prem and
    cloud (issue #267); the un-cast path is the Web API's documented contract for
    relationship-metadata updates.
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
    )
    metadata_id = resolve.get("MetadataId")
    if not isinstance(metadata_id, str) or not metadata_id:
        raise D365Error(
            f"Could not resolve MetadataId for relationship {schema_name!r}."
        )
    odata_type = resolve.get("@odata.type")
    rel_type = resolve.get("RelationshipType")
    if isinstance(odata_type, str) and odata_type:
        odata_cast = odata_type.lstrip("#")
    elif rel_type == "ManyToManyRelationship":
        odata_cast = "Microsoft.Dynamics.CRM.ManyToManyRelationshipMetadata"
    else:
        odata_cast = "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata"

    # Cascade and associated-menu changes are one-to-many shaped. A many-to-many
    # relationship has no CascadeConfiguration/AssociatedMenuConfiguration (it
    # uses Entity1/Entity2-side menus, which are out of scope here), so emitting
    # them would produce an invalid PUT. Reject client-side instead.
    if odata_cast == _MANY_TO_MANY_CAST and (
        cascade or menu_behavior is not None
        or menu_label is not None or menu_order is not None
    ):
        raise D365Error(
            f"relationship {schema_name!r} is many-to-many; cascade and "
            "associated-menu updates are only valid for one-to-many "
            "relationships (N:N side-specific menus are not supported)."
        )

    # Read the merge base from the typed cast path — it is the only projection
    # that carries CascadeConfiguration/AssociatedMenuConfiguration — but PUT to
    # the un-cast entity-set path: a PUT to the cast segment returns HTTP 405 on
    # both on-prem and cloud (issue #267).
    cast_path = f"RelationshipDefinitions({metadata_id})/{odata_cast}"
    write_path = f"RelationshipDefinitions({metadata_id})"

    out = _retrieve_merge_write(
        backend, path=cast_path, changes=changes, solution=solution,
        publish=publish, write_path=write_path,
        ensure={"@odata.type": f"#{odata_cast}"},
    )
    out.setdefault("schema_name", schema_name)
    return out
