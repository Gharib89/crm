"""Schema metadata browsing.

Most metadata lives under `EntityDefinitions(...)` in the Web API. We hide the
quoting/path-building behind small helpers.
"""

from __future__ import annotations

import difflib
import json
from typing import Any, cast

from crm.utils.d365_backend import D365Backend, D365Error, as_dict, odata_literal
from crm.core import dependencies as dep_mod
from crm.core import entity_names
from crm.core import metadata_cache
from crm.core import metadata_constraints as mc


def list_entities(
    backend: D365Backend,
    *,
    custom_only: bool = False,
    managed_only: bool = False,
    filter_expr: str | None = None,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """List entity definitions. Returns a list of `{LogicalName, EntitySetName, ...}` dicts.

    Note: `EntityDefinitions` does NOT support `$top` server-side (rejects with
    "The query parameter $top is not supported"), so we slice client-side after
    the response comes back.
    """
    params = {
        "$select": "LogicalName,EntitySetName,SchemaName,IsCustomEntity,IsManaged,DisplayName",
    }
    clauses: list[str] = []
    if custom_only:
        clauses.append("IsCustomEntity eq true")
    if managed_only:
        clauses.append("IsManaged eq true")
    if filter_expr:
        clauses.append(filter_expr)
        
    if clauses:
        params["$filter"] = " and ".join(clauses)

    result = as_dict(backend.get("EntityDefinitions", params=params))
    items = result.get("value", [])
    if top is not None:
        if top < 1:
            raise D365Error("--top must be >= 1")
        items = items[:top]
    return items


def suggest_logical_name(
    backend: D365Backend, wrong_name: str
) -> dict[str, Any] | None:
    """Return a logical-name suggestion for `wrong_name`, or None.

    Two passes against a single 2-field GET (LogicalName + EntitySetName):
    1. Exact EntitySetName match → reason "exact-set"
    2. difflib close match over LogicalNames → reason "close-match"
    If the recovery GET itself raises, returns None so the original error is
    preserved by the caller.
    """
    try:
        rows: list[dict[str, Any]] = backend.get_collection(
            "EntityDefinitions",
            params={"$select": "LogicalName,EntitySetName"},
        )
    except D365Error:
        return None

    # Pass 1: exact EntitySetName match
    for row in rows:
        if row.get("EntitySetName") == wrong_name:
            return {"logical_name": row["LogicalName"], "reason": "exact-set"}

    # Pass 2: fuzzy over LogicalNames
    logical_names = sorted(r["LogicalName"] for r in rows if r.get("LogicalName"))
    matches = difflib.get_close_matches(wrong_name, logical_names, n=1, cutoff=0.6)
    if matches:
        return {"logical_name": matches[0], "reason": "close-match"}

    return None


def entity_info(backend: D365Backend, logical_name: str) -> dict[str, Any]:
    """Retrieve the full entity definition for `logical_name`."""
    if not logical_name:
        raise D365Error("logical_name is required.")
    path = f"EntityDefinitions(LogicalName='{logical_name}')"
    return as_dict(backend.get(path))


def resolve_entity_set_name(backend: D365Backend, logical_name: str) -> str:
    """Resolve a logical entity name to its OData entity-set name.

    Thin shim over the :mod:`crm.core.entity_names` seam: the bidirectional map is
    served read-through from the metadata cache (warm cache → no live GET). The
    entity-set name is metadata-defined and must not be guessed (e.g. pluralised).
    Raises D365Error if the logical name is unknown or has no entity-set name.
    """
    if not logical_name:
        raise D365Error("logical_name is required.")
    entity_set_name = entity_names.load_name_map(backend).set_for(logical_name)
    if not entity_set_name:
        raise D365Error(
            f"Could not resolve entity-set name for logical name {logical_name!r}",
            code="UnknownEntityLogicalName",
        )
    return entity_set_name


def list_attributes(backend: D365Backend, logical_name: str) -> list[dict[str, Any]]:
    """List attributes for an entity (logical name).

    Projects write/read validity (`IsValidForCreate` / `IsValidForUpdate` /
    `IsValidForRead`) and `RequiredLevel` alongside the identifying fields so a
    caller can tell which attributes are settable when building a create/update
    payload (#337). `RequiredLevel` is a nested ``{"Value": ...}`` object
    server-side; it is flattened to its `Value` string here, matching the
    normalization in :func:`entity_names.specs_from_rows`.
    """
    path = f"EntityDefinitions(LogicalName='{logical_name}')/Attributes"
    result = as_dict(backend.get(
        path,
        params={"$select": "LogicalName,SchemaName,AttributeType,IsCustomAttribute,"
                "IsValidForCreate,IsValidForUpdate,IsValidForRead,RequiredLevel"},
    ))
    rows: list[dict[str, Any]] = result.get("value", [])
    for row in rows:
        required: dict[str, Any] = row.get("RequiredLevel") or {}
        level = required.get("Value")
        row["RequiredLevel"] = level if isinstance(level, str) else None
    return rows


def attribute_info(backend: D365Backend, logical_name: str, attribute: str) -> dict[str, Any]:
    """Retrieve a single attribute definition."""
    path = (
        f"EntityDefinitions(LogicalName='{logical_name}')"
        f"/Attributes(LogicalName='{attribute}')"
    )
    return as_dict(backend.get(path))


def attribute_info_or_raise(
    backend: D365Backend, entity: str, column: str
) -> dict[str, Any]:
    """Confirm ``column`` exists on ``entity`` and return its metadata, with a
    clean error if it does not. The shared existence check for the XML editors
    (charts, views) that validate a referenced column before a write.

    Only a 404 is translated to "does not exist"; an auth/server/transport
    failure is re-raised unchanged so a real outage is not misreported as a
    typo'd column.
    """
    try:
        return attribute_info(backend, entity, column)
    except D365Error as exc:
        if exc.status == 404:
            raise D365Error(
                f"attribute {column!r} does not exist on {entity!r}.") from exc
        raise


def picklist_options(
    backend: D365Backend,
    logical_name: str,
    attribute: str,
    *,
    global_optionset: bool = True,
) -> dict[str, Any]:
    """Retrieve option set values for a picklist / state / status attribute.

    Fetches the attribute's `AttributeType` first, then selects the correct
    OData cast segment from `_OPTION_SET_CASTS`. Raises `D365Error` for
    attribute types that do not carry an option set.

    Returns `{ "LogicalName": ..., "OptionSet": {...}, "GlobalOptionSet": {...} }`.
    """
    if not logical_name or not attribute:
        raise D365Error("logical_name and attribute are required.")
    info = attribute_info(backend, logical_name, attribute)
    attr_type: str = info.get("AttributeType") or ""
    cast_entry = next(
        (e for e in _OPTION_SET_CASTS if e[0] == attr_type),
        None,
    )
    if cast_entry is None:
        supported = ", ".join(e[0] for e in _OPTION_SET_CASTS)
        raise D365Error(
            f"Attribute '{attribute}' has type '{attr_type}', which does not "
            f"carry an option set. Supported: {supported}."
        )
    _, cast_subtype, expand = cast_entry
    if not global_optionset:
        expand = "OptionSet"
    path = (
        f"EntityDefinitions(LogicalName='{logical_name}')"
        f"/Attributes(LogicalName='{attribute}')/"
        f"Microsoft.Dynamics.CRM.{cast_subtype}"
    )
    return as_dict(backend.get(
        path,
        params={"$select": "LogicalName", "$expand": expand},
    ))


def multiselect_options(
    backend: D365Backend,
    logical_name: str,
    attribute: str,
    *,
    global_optionset: bool = True,
) -> dict[str, Any]:
    """Retrieve option set values for a multi-select picklist attribute.

    Mirrors `picklist_options` but casts to
    `Microsoft.Dynamics.CRM.MultiSelectPicklistAttributeMetadata` — a multiselect
    column is NOT a `PicklistAttributeMetadata`, so the Picklist cast raises. The
    MultiSelect metadata carries `OptionSet` (local) + `GlobalOptionSet` (global)
    with the same shape, so `flatten_options` works on the result either way.

    Returns `{ "LogicalName": ..., "OptionSet": {...}, "GlobalOptionSet": {...} }`.
    """
    if not logical_name or not attribute:
        raise D365Error("logical_name and attribute are required.")
    cast = "Microsoft.Dynamics.CRM.MultiSelectPicklistAttributeMetadata"
    path = (
        f"EntityDefinitions(LogicalName='{logical_name}')"
        f"/Attributes(LogicalName='{attribute}')/{cast}"
    )
    expand = "OptionSet" + (",GlobalOptionSet" if global_optionset else "")
    return as_dict(backend.get(
        path,
        params={"$select": "LogicalName", "$expand": expand},
    ))


def label_text(label_obj: dict[str, Any]) -> str:
    """Best-effort display label from a Dataverse Label payload."""
    ull: dict[str, Any] = label_obj.get("UserLocalizedLabel") or {}
    if ull.get("Label"):
        return str(ull["Label"])
    locs: list[dict[str, Any]] = label_obj.get("LocalizedLabels") or []
    if locs:
        return str(locs[0].get("Label") or "")
    return ""


def flatten_options(container: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a container's `Options` to `[{value, label}]`.

    `container` is any dict carrying an `Options` array — a picklist's
    `OptionSet` / `GlobalOptionSet`, or a global option set with `Options` at
    its root. Labels use the robust `label_text` fallback. A Boolean attribute
    has no `Options` array (it casts to `TrueOption` / `FalseOption`), so this
    returns `[]` for booleans — read those raw fields instead.
    """
    rows: list[dict[str, Any]] = container.get("Options") or []
    out: list[dict[str, Any]] = []
    for o in rows:
        lbl: dict[str, Any] = o.get("Label") or {}
        out.append({"value": o.get("Value"), "label": label_text(lbl)})
    return out


# (AttributeType, cast subtype, $expand) for each option-set-bearing kind. Only
# Picklist can bind a GLOBAL option set, so it expands GlobalOptionSet too; State
# / Status options are always local (and read-only server-side).
_OPTION_SET_CASTS: tuple[tuple[str, str, str], ...] = (
    ("Picklist", "PicklistAttributeMetadata", "OptionSet,GlobalOptionSet"),
    ("State", "StateAttributeMetadata", "OptionSet"),
    ("Status", "StatusAttributeMetadata", "OptionSet"),
)


def _enrich_options(
    backend: D365Backend, logical_name: str, writable: list[dict[str, Any]]
) -> None:
    """Attach inline `options[]` to each writable picklist/state/status attribute.

    The `Attributes` collection must be cast to the typed subtype to reach the
    `OptionSet` / `GlobalOptionSet` navigation properties; each present kind is
    fetched in one expanded GET. A picklist bound to a global option set has
    `OptionSet` null and its options (plus the `MetadataId` GUID on-prem 9.1 needs
    to bind on create) under `GlobalOptionSet`; everything else is the reverse.
    Mutates `writable` in place. No-op for kinds the entity does not use.
    """
    present = {a["attribute_type"] for a in writable}
    for attr_type, cast, expand in _OPTION_SET_CASTS:
        if attr_type not in present:
            continue
        res = as_dict(backend.get(
            f"EntityDefinitions(LogicalName='{logical_name}')/Attributes/"
            f"Microsoft.Dynamics.CRM.{cast}",
            params={"$select": "LogicalName", "$expand": expand},
        ))
        rows: list[dict[str, Any]] = res.get("value", [])
        by_logical: dict[str, dict[str, Any]] = {}
        for r in rows:
            name = r.get("LogicalName")
            if name:
                by_logical[name] = r
        for attr in writable:
            if attr["attribute_type"] != attr_type:
                continue
            row = by_logical.get(attr["logical_name"])
            if row is None:
                continue
            glob: dict[str, Any] = row.get("GlobalOptionSet") or {}
            if glob:
                attr["options"] = flatten_options(glob)
                if glob.get("MetadataId"):
                    attr["global_optionset_id"] = glob["MetadataId"]
            else:
                local: dict[str, Any] = row.get("OptionSet") or {}
                attr["options"] = flatten_options(local)


def lookup_nav_map(
    backend: D365Backend, logical_name: str
) -> dict[str, list[tuple[str, str]]]:
    """Map each lookup column of *logical_name* to its bind targets.

    Returns ``{ReferencingAttribute: [(referenced_entity_logical, nav_property)]}``
    from the entity's ``ManyToOne`` relationship metadata. The
    ``ReferencingEntityNavigationPropertyName`` is the single-valued navigation
    property on THIS (referencing) entity — the case-sensitive name the server
    accepts in a ``<Nav>@odata.bind`` deep-link. A polymorphic lookup (customer /
    owner / regarding) yields one entry per target table; a single-target lookup
    yields one. Empty when the entity has no lookup columns.
    """
    m2o = as_dict(backend.get(
        f"EntityDefinitions(LogicalName='{logical_name}')/ManyToOneRelationships",
        params={"$select":
                "ReferencingAttribute,ReferencedEntity,"
                "ReferencingEntityNavigationPropertyName"},
    ))
    rels: list[dict[str, Any]] = m2o.get("value", [])
    by_attr: dict[str, list[tuple[str, str]]] = {}
    for r in rels:
        ref_attr = r.get("ReferencingAttribute")
        if not ref_attr:
            continue
        by_attr.setdefault(ref_attr, []).append((
            r.get("ReferencedEntity") or "",
            r.get("ReferencingEntityNavigationPropertyName") or "",
        ))
    return by_attr


def _enrich_lookups(
    backend: D365Backend, logical_name: str, writable: list[dict[str, Any]]
) -> None:
    """Attach `bind_key` + `targets[]` to each writable lookup attribute, in place.

    The bind key is self-derived from `ManyToOne` relationship metadata (see
    :func:`lookup_nav_map`): a 1:N relationship's
    `ReferencingEntityNavigationPropertyName` is the single-valued navigation
    property on THIS (referencing) entity — the case-sensitive name the server
    accepts in a `<Nav>@odata.bind` deep-link. Each target's `EntitySetName` is
    resolved so the agent has a usable bind VALUE (`/<set_name>(<id>)`). No-op
    when the entity has no lookup columns.
    """
    lookups = [a for a in writable if a["attribute_type"] == "Lookup"]
    if not lookups:
        return
    by_attr = lookup_nav_map(backend, logical_name)

    set_names: dict[str, str] = {}

    def _set_name(ref_logical: str) -> str:
        if ref_logical not in set_names:
            rb = as_dict(backend.get(
                f"EntityDefinitions(LogicalName='{ref_logical}')",
                params={"$select": "EntitySetName"},
            ))
            set_names[ref_logical] = rb.get("EntitySetName") or ""
        return set_names[ref_logical]

    for attr in lookups:
        matched = by_attr.get(attr["logical_name"])
        if not matched:
            continue
        attr["targets"] = [
            {"logical": ref, "set_name": _set_name(ref)} for ref, _ in matched
        ]
        # For the common single-target lookup there is exactly one relationship;
        # a polymorphic lookup surfaces the first navigation property here.
        nav = matched[0][1]
        if nav:
            attr["bind_key"] = f"{nav}@odata.bind"


def describe_entity(backend: D365Backend, logical_name: str) -> dict[str, Any]:
    """One read-only write-readiness brief for `logical_name` (#68).

    Consolidates the metadata an agent needs to construct a valid create/update
    payload: the entity set name, primary id/name, and every *writable* attribute
    (valid for create or update) with its required level. Pure GETs.
    """
    if not logical_name:
        raise D365Error("logical_name is required.")
    ent = as_dict(backend.get(
        f"EntityDefinitions(LogicalName='{logical_name}')",
        params={"$select":
                "LogicalName,EntitySetName,PrimaryIdAttribute,PrimaryNameAttribute"},
    ))
    # Writable = valid for create OR update. The IsValidForCreate/IsValidForUpdate
    # walk lives once in entity_names.attribute_specs (#261).
    writable: list[dict[str, Any]] = []
    for spec in entity_names.attribute_specs(backend, logical_name):
        if not (spec.valid_for_create or spec.valid_for_update):
            continue
        writable.append({
            "logical_name": spec.logical_name,
            "attribute_type": spec.attribute_type,
            "required_level": spec.required_level,
        })

    _enrich_lookups(backend, logical_name, writable)
    _enrich_options(backend, logical_name, writable)

    return {
        "logical_name": ent.get("LogicalName"),
        "entity_set_name": ent.get("EntitySetName"),
        "primary_id": ent.get("PrimaryIdAttribute"),
        "primary_name": ent.get("PrimaryNameAttribute"),
        "writable_attributes": writable,
    }


def label(text: str, lang: int = 1033) -> dict[str, Any]:
    """Build a Dataverse Label payload from a single string."""
    return {"LocalizedLabels": [{"Label": text, "LanguageCode": lang}]}


def target_exists(backend: D365Backend, path: str) -> bool:
    """Probe whether a metadata target exists via a read-only GET.

    Returns True when the GET succeeds (target present), False when it raises
    `D365Error` with status 404 (target absent). Any other `D365Error` is
    re-raised — a real failure must not be masked as "not found".

    The probe is always a real GET even when the backend is in dry-run mode:
    it never mutates, and idempotency checks need the live answer to build an
    accurate preview.
    """
    try:
        backend.get(path, params={"$select": "MetadataId"})
        return True
    except D365Error as exc:
        if exc.status == 404:
            return False
        raise


def maybe_publish(backend: D365Backend, info: dict[str, Any], publish: bool) -> dict[str, Any]:
    """Run PublishAllXml unless dry-run or publish=False. Returns info dict (mutated)."""
    if not publish or info.get("_dry_run"):
        return info
    from crm.core import solution as sol_mod
    sol_mod.publish_all(backend)
    info["published"] = True
    return info


def create_entity(
    backend: D365Backend,
    *,
    schema_name: str,
    display_name: str,
    display_collection_name: str | None = None,
    primary_attr_schema: str | None = None,
    primary_attr_label: str | None = None,
    primary_attr_max_length: int = 200,
    description: str | None = None,
    ownership: str = "UserOwned",
    has_activities: bool = False,
    has_notes: bool = False,
    is_activity: bool = False,
    data_provider_id: str | None = None,
    data_source_id: str | None = None,
    external_name: str | None = None,
    external_collection_name: str | None = None,
    solution: str | None = None,
    if_exists: str = "error",
) -> dict[str, Any]:
    """Create a new custom entity (table) via POST /EntityDefinitions.

    Args:
        schema_name: PascalCase with publisher prefix, e.g. `new_Project`.
        display_name: Singular UI name, e.g. "Project".
        display_collection_name: Plural UI name; defaults to display_name + 's'.
        primary_attr_schema: Schema name of the primary name attribute. Must
            share the publisher prefix. Defaults to `<prefix>_Name`.
        primary_attr_label: UI label for the primary attribute. Defaults to "Name".
        primary_attr_max_length: Max length for primary name string. Default 200.
        description: Optional entity description.
        ownership: `UserOwned` (default) or `OrganizationOwned`.
        has_activities: Enable activities on the entity.
        has_notes: Enable notes (annotations) on the entity.
        is_activity: Create as an activity entity (mutually exclusive with most options).
        data_provider_id: Virtual-entity data provider GUID. Setting any of the
            four `external_*` / `data_*` arguments marks this a *virtual* table
            (its rows live in an external store). Virtual tables are read-only on
            v9.1 and require the data-provider (and optional data-source) records
            to exist first. `external_name`, `external_collection_name`, and
            `data_provider_id` are required together; `data_source_id` is optional.
        data_source_id: Virtual-entity data source GUID (optional).
        external_name: External table name this virtual entity maps to.
        external_collection_name: External collection (plural) name.
        solution: Optional `uniquename` to add the entity to a specific solution
            via the `MSCRM.SolutionUniqueName` header.

    Returns a dict describing the created entity. The Web API returns 204 No
    Content with an `OData-EntityId` header pointing at the new MetadataId.

    Reference: https://learn.microsoft.com/power-apps/developer/data-platform/webapi/use-web-api-metadata#create-and-update-entity-definitions
    """
    if not schema_name or "_" not in schema_name:
        raise D365Error(
            "schema_name must include a publisher prefix and be PascalCase, "
            "e.g. 'new_Project'."
        )
    mc.validate_ownership(ownership)

    if if_exists not in ("error", "skip"):
        raise D365Error("if_exists must be 'error' or 'skip'.")

    # Virtual table: any external/data-provider argument opts in; the server
    # rejects a partial set, so require the three load-bearing values together
    # (data_source_id stays optional — the docs allow a null source at create
    # time). Standard tables must leave all four null or the server faults.
    is_virtual = any((data_provider_id, data_source_id, external_name,
                      external_collection_name))
    if is_virtual:
        if not external_name:
            raise D365Error(
                "a virtual table requires --external-name (the external table "
                "name its rows map to)."
            )
        if not external_collection_name:
            raise D365Error(
                "a virtual table requires --external-collection-name (the "
                "external collection/plural name)."
            )
        if not data_provider_id:
            raise D365Error(
                "a virtual table requires --data-provider (the data-provider "
                "record GUID); create the data-provider/data-source records first."
            )

    prefix, _, _ = schema_name.partition("_")
    logical_name = schema_name.lower()

    exists = target_exists(
        backend, f"EntityDefinitions(LogicalName='{logical_name}')"
    )
    if exists and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(
                f"Entity {logical_name!r} already exists.",
                code="AlreadyExists",
            )
        return {
            "skipped": True,
            "exists": True,
            "schema_name": schema_name,
            "logical_name": logical_name,
        }

    primary_schema = primary_attr_schema or f"{prefix}_Name"
    if "_" not in primary_schema:
        raise D365Error(
            "primary_attr_schema must include the publisher prefix, "
            f"e.g. '{prefix}_Name'."
        )
    primary_logical = primary_schema.lower()
    primary_label_text = primary_attr_label or "Name"
    collection_label = display_collection_name or (display_name + "s")

    body: dict[str, Any] = {
        "@odata.type": "Microsoft.Dynamics.CRM.EntityMetadata",
        "SchemaName": schema_name,
        "LogicalName": logical_name,
        "DisplayName": label(display_name),
        "DisplayCollectionName": label(collection_label),
        "OwnershipType": ownership,
        "HasActivities": has_activities,
        "HasNotes": has_notes,
        "IsActivity": is_activity,
        "Attributes": [{
            "@odata.type": "Microsoft.Dynamics.CRM.StringAttributeMetadata",
            "SchemaName": primary_schema,
            "LogicalName": primary_logical,
            "RequiredLevel": {"Value": "ApplicationRequired"},
            "MaxLength": primary_attr_max_length,
            "FormatName": {"Value": "Text"},
            "DisplayName": label(primary_label_text),
            "IsPrimaryName": True,
        }],
    }
    if description:
        body["Description"] = label(description)
    if is_virtual:
        body["ExternalName"] = external_name
        body["ExternalCollectionName"] = external_collection_name
        body["DataProviderId"] = data_provider_id
        if data_source_id:
            body["DataSourceId"] = data_source_id

    headers = {}
    if solution:
        headers["MSCRM.SolutionUniqueName"] = solution

    result = as_dict(backend.post(
        "EntityDefinitions",
        json_body=body,
        extra_headers=headers or None,
    ))
    if result.get("_dry_run"):
        result["_exists"] = exists
        result["would_skip"] = exists and if_exists == "skip"
        return result
    entity_id_url: str | None = result.get("_entity_id_url")
    metadata_id: str | None = result.get("_entity_id")

    # Read-back: take the MetadataId the parser extracted from the OData-EntityId
    # URL, then GET the server's authoritative EntitySetName. Failure here does
    # NOT fail the command — the entity was created.
    entity_set_name: str | None = None
    entity_set_lookup_error: str | None = None
    if not entity_id_url:
        entity_set_lookup_error = "OData-EntityId header missing from create response."
    elif not metadata_id:
        entity_set_lookup_error = (
            f"Could not parse MetadataId from OData-EntityId URL: {entity_id_url!r}"
        )
    else:
        try:
            rb = as_dict(backend.get(
                f"EntityDefinitions({metadata_id})",
                params={"$select": "EntitySetName,LogicalName"},
            ))
            name = rb.get("EntitySetName")
            if isinstance(name, str) and name:  # pyright: ignore[reportUnnecessaryIsInstance]
                entity_set_name = name
            else:
                entity_set_lookup_error = (
                    f"Read-back returned no EntitySetName for MetadataId {metadata_id}."
                )
        except D365Error as exc:
            entity_set_lookup_error = f"Read-back failed: {exc}"

    out: dict[str, Any] = {
        "created": True,
        "schema_name": schema_name,
        "logical_name": logical_name,
        "entity_set_name": entity_set_name,
        "primary_attribute": primary_logical,
        "metadata_id_url": entity_id_url,
        "solution": solution,
    }
    if entity_set_lookup_error is not None:
        out["entity_set_lookup_error"] = entity_set_lookup_error
    if not backend.dry_run:
        metadata_cache.invalidate(backend.profile)
    return out


def delete_entity(
    backend: D365Backend,
    logical_name: str,
    *,
    solution: str | None = None,
    check_dependencies: bool = False,
) -> dict[str, Any]:
    """Permanently delete a custom entity (table) and ALL its rows.

    Pre-flight: refuses if `IsCustomEntity=False` or `IsManaged=True`.
    Server enforces remaining dependency checks (workflows, forms,
    relationships) and returns 4xx on conflict.

    Args:
        check_dependencies: When True, call RetrieveDependenciesForDelete
            before the DELETE and fold ``can_delete`` + ``blockers`` into the
            result. Informational only — does not abort the delete.
    """
    if not logical_name:
        raise D365Error("logical_name is required.")
    path = f"EntityDefinitions(LogicalName='{logical_name}')"
    rb = as_dict(backend.get(
        path,
        params={"$select": "IsCustomEntity,IsManaged,MetadataId"},
    ))
    if rb.get("IsCustomEntity") is False:
        raise D365Error(
            f"{logical_name!r} is not a custom entity; refusing to delete.",
            code="NotCustomEntity",
        )
    if rb.get("IsManaged") is True:
        raise D365Error(
            f"{logical_name!r} is a managed entity; uninstall the parent "
            "solution to remove it.",
            code="ManagedEntity",
        )
    deps = None
    if check_dependencies:
        _mid = rb.get("MetadataId")
        if isinstance(_mid, str) and _mid:
            deps = dep_mod.dependencies_by_id(backend, _mid, 1, for_="delete", kind="entity")
        else:
            deps = dep_mod.retrieve_dependencies(backend, "entity", logical_name, for_="delete")
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    preview = backend.delete(path, extra_headers=headers)
    if isinstance(preview, dict) and preview.get("_dry_run"):
        result: dict[str, Any] = {
            "_dry_run": True,
            "would_delete": True,
            "logical_name": logical_name,
            "solution": solution,
        }
    else:
        result = {
            "deleted": True,
            "logical_name": logical_name,
            "solution": solution,
        }
    if deps is not None:
        result["can_delete"] = deps["can_delete"]
        result["blockers"] = deps["blockers"]
    if not backend.dry_run:
        metadata_cache.invalidate(backend.profile)
    return result


import xml.etree.ElementTree as _ET  # noqa: E402

_EDM_NS = "http://docs.oasis-open.org/odata/ns/edm"


_D365_NAMESPACE = "Microsoft.Dynamics.CRM"


def _fetch_csdl(backend: D365Backend) -> list[_ET.Element]:
    """GET $metadata and parse as XML. Returns all <Schema> elements."""
    # $metadata is served only as CSDL XML; the default Accept: application/json
    # makes Dataverse answer HTTP 415 (#266). Override Accept for this call only.
    raw = backend.get(
        "$metadata", expect_json=False, extra_headers={"Accept": "application/xml"}
    )
    if not isinstance(raw, str):
        raise D365Error("$metadata response was not text/xml")
    try:
        root = _ET.fromstring(raw)
    except _ET.ParseError as exc:
        raise D365Error(f"Failed to parse $metadata XML: {exc}") from exc
    all_schemas = root.findall(f".//{{{_EDM_NS}}}Schema")
    if not all_schemas:
        raise D365Error("No <Schema> element in $metadata response")
    d365_schemas = [s for s in all_schemas
                    if s.attrib.get("Namespace") == _D365_NAMESPACE]
    return d365_schemas if d365_schemas else all_schemas


def _extract_callable(schema: _ET.Element, tag: str) -> list[dict[str, Any]]:
    # CSDL booleans are absent unless true; the return type is a child
    # <ReturnType Type="..."/> element. IsComposable only applies to Functions.
    is_function = tag == "Function"
    items: list[dict[str, Any]] = []
    for elem in schema.findall(f"{{{_EDM_NS}}}{tag}"):
        params: list[dict[str, str]] = []
        for p in elem.findall(f"{{{_EDM_NS}}}Parameter"):
            params.append({
                "name": p.attrib.get("Name", ""),
                "type": p.attrib.get("Type", ""),
            })
        return_type = elem.find(f"{{{_EDM_NS}}}ReturnType")
        item: dict[str, Any] = {
            "name": elem.attrib.get("Name", ""),
            "is_bound": elem.attrib.get("IsBound") == "true",
            "return_type": return_type.attrib.get("Type") if return_type is not None else None,
            "parameters": params,
        }
        if is_function:
            item["is_composable"] = elem.attrib.get("IsComposable") == "true"
        items.append(item)
    return items


def _extract_all_callables(schemas: list[_ET.Element], tag: str) -> list[dict[str, Any]]:
    """Aggregate callables of `tag` across all Schema elements."""
    items: list[dict[str, Any]] = []
    for schema in schemas:
        items.extend(_extract_callable(schema, tag))
    return items


def list_actions(backend: D365Backend) -> list[dict[str, Any]]:
    """List OData actions (POST verbs) declared by the D365 service."""
    return _extract_all_callables(_fetch_csdl(backend), "Action")


def list_functions(backend: D365Backend) -> list[dict[str, Any]]:
    """List OData functions (GET verbs) declared by the D365 service."""
    return _extract_all_callables(_fetch_csdl(backend), "Function")


def list_entity_definitions(backend: D365Backend) -> list[dict[str, str]]:
    """Return `[{logical, set_name}]` for all entities.

    Fetches both LogicalName and EntitySetName in one call so callers can
    derive either list without a second round-trip.
    """
    result = as_dict(backend.get(
        "EntityDefinitions",
        params={"$select": "LogicalName,EntitySetName"},
    ))
    items: list[dict[str, str]] = []
    for e in result.get("value", []):
        logical: str = e.get("LogicalName") or ""
        set_name: str = e.get("EntitySetName") or ""
        if logical:
            items.append({"logical": logical, "set_name": set_name})
    return items


def list_entity_names(backend: D365Backend) -> list[str]:
    """Return entity logical names (backward-compat wrapper)."""
    return [d["logical"] for d in list_entity_definitions(backend)]


def list_entity_keys(backend: D365Backend, logical_name: str) -> list[dict[str, Any]]:
    """Return the alternate keys defined on `logical_name`.

    Fetches ``EntityDefinitions(LogicalName='...')/Keys`` and normalises each
    item to ``{logical_name, schema_name, key_attributes, index_status}``.
    Returns an empty list for entities that have no alternate keys — that is
    not an error. Raises ``D365Error`` for any backend failure (including 404
    for an unknown entity).
    """
    if not logical_name:
        raise D365Error("logical_name is required.")
    path = f"EntityDefinitions(LogicalName='{logical_name}')/Keys"
    result = as_dict(backend.get(
        path,
        params={"$select": "LogicalName,SchemaName,KeyAttributes,EntityKeyIndexStatus"},
    ))
    rows: list[dict[str, Any]] = result.get("value", [])
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "logical_name": r.get("LogicalName") or "",
            "schema_name": r.get("SchemaName") or "",
            "key_attributes": r.get("KeyAttributes") or [],
            "index_status": r.get("EntityKeyIndexStatus") or "",
        })
    return out


def create_entity_key(
    backend: D365Backend,
    *,
    entity: str,
    schema_name: str,
    key_attributes: list[str],
    display_name: str | None = None,
    solution: str | None = None,
    if_exists: str = "error",
) -> dict[str, Any]:
    """Create an alternate key (``EntityKeyMetadata``) on ``entity``.

    POSTs to ``EntityDefinitions(LogicalName='...')/Keys`` — the collection that
    ``list_entity_keys`` reads. ``CreateEntityKey`` is an Organization-Service
    message with no OData action, so the metadata collection is the only Web API
    path. The server builds the supporting index asynchronously, so a freshly
    created key starts with ``EntityKeyIndexStatus`` ``Pending``.

    Args:
        schema_name: PascalCase with publisher prefix, e.g. ``new_Code``.
        key_attributes: Attribute logical names forming the key (1..n).
        display_name: UI label; defaults to ``schema_name``.
        solution: Optional ``uniquename`` added via ``MSCRM.SolutionUniqueName``.
        if_exists: ``error`` (default) or ``skip`` (no-op success) when the key
            already exists.

    The Web API returns 204 No Content with an ``OData-EntityId`` header. Alternate
    keys are not held in the entity-name metadata cache, so no cache invalidation
    is needed.

    Reference: https://learn.microsoft.com/power-apps/developer/data-platform/define-alternate-keys-entity#create-alternate-keys
    """
    if not entity:
        raise D365Error("entity is required.")
    if not schema_name or "_" not in schema_name:
        raise D365Error(
            "schema_name must include a publisher prefix and be PascalCase, "
            "e.g. 'new_Code'."
        )
    if not key_attributes:
        raise D365Error("at least one key attribute is required.")
    if if_exists not in ("error", "skip"):
        raise D365Error("if_exists must be 'error' or 'skip'.")

    logical_name = schema_name.lower()
    display = display_name or schema_name

    exists = target_exists(
        backend,
        f"EntityDefinitions(LogicalName='{entity}')/Keys(LogicalName='{logical_name}')",
    )
    if exists and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(
                f"Alternate key {logical_name!r} already exists on entity {entity!r}.",
                code="AlreadyExists",
            )
        return {
            "skipped": True,
            "exists": True,
            "entity": entity,
            "schema_name": schema_name,
            "logical_name": logical_name,
        }

    body: dict[str, Any] = {
        "@odata.type": "Microsoft.Dynamics.CRM.EntityKeyMetadata",
        "SchemaName": schema_name,
        "LogicalName": logical_name,
        "DisplayName": label(display),
        "KeyAttributes": list(key_attributes),
    }
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(
        f"EntityDefinitions(LogicalName='{entity}')/Keys",
        json_body=body,
        extra_headers=headers,
    ))
    if result.get("_dry_run"):
        result["_exists"] = exists
        result["would_skip"] = exists and if_exists == "skip"
        return result
    return {
        "created": True,
        "entity": entity,
        "schema_name": schema_name,
        "logical_name": logical_name,
        "key_attributes": list(key_attributes),
        "metadata_id_url": result.get("_entity_id_url"),
        "solution": solution,
    }


def delete_entity_key(
    backend: D365Backend,
    entity: str,
    key: str,
    *,
    solution: str | None = None,
) -> dict[str, Any]:
    """Delete an alternate key from ``entity`` by its logical name.

    DELETEs ``EntityDefinitions(LogicalName='...')/Keys(LogicalName='...')``.
    ``key`` is lower-cased before addressing the collection so callers can pass
    either the schema name or the logical name (Dataverse logical names are
    always lower-case). Alternate keys are not held in the entity-name metadata
    cache, so no cache invalidation is needed.
    """
    if not entity:
        raise D365Error("entity is required.")
    if not key:
        raise D365Error("key is required.")
    key_logical = key.lower()
    path = (
        f"EntityDefinitions(LogicalName='{entity}')"
        f"/Keys(LogicalName='{key_logical}')"
    )
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    preview = backend.delete(path, extra_headers=headers)
    if isinstance(preview, dict) and preview.get("_dry_run"):
        return {
            "_dry_run": True,
            "would_delete": True,
            "entity": entity,
            "key": key_logical,
            "solution": solution,
        }
    return {
        "deleted": True,
        "entity": entity,
        "key": key_logical,
        "solution": solution,
    }


# ── Metadata change tracking (RetrieveMetadataChanges) ───────────────────────
# RetrieveMetadataChanges is an unbound Web API *function* whose Query parameter
# is an EntityQueryExpression complex type passed as raw (un-quoted) JSON via a
# parameter alias, while ClientVersionStamp is an ordinary string literal. The
# function returns a ServerVersionStamp to feed back as ClientVersionStamp on a
# later call so only metadata changed since then comes back. Deletions are only
# reported when both ClientVersionStamp and DeletedMetadataFilters are supplied.
# Reference: https://learn.microsoft.com/power-apps/developer/data-platform/query-schema-definitions?tabs=webapi

# All deleted-metadata kinds (Entity|Attribute|Relationship|Label|OptionSet); the
# IsFlags "All" member, passed inline as an OData enum literal.
_DELETED_METADATA_FILTER_ALL = "Microsoft.Dynamics.CRM.DeletedMetadataFilters'All'"


def _build_changes_query(
    entities: list[str] | None, attributes: bool
) -> dict[str, Any]:
    """Build the EntityQueryExpression for :func:`metadata_changes`.

    Always selects entity ``SchemaName``/``DisplayName`` (``LogicalName``,
    ``MetadataId`` and ``HasChanged`` are returned regardless). ``attributes``
    expands column definitions; ``entities`` scopes the query to those logical
    names (omit to query every table — heavy on a baseline call).
    """
    prop_names: list[str] = ["LogicalName", "SchemaName", "DisplayName"]
    query: dict[str, Any] = {
        "Properties": {"AllProperties": False, "PropertyNames": prop_names},
        "LabelQuery": {"FilterLanguages": [1033], "MissingLabelBehavior": 0},
    }
    if attributes:
        # When AttributeQuery is used, "Attributes" must be a requested property.
        prop_names.append("Attributes")
        query["AttributeQuery"] = {
            "Properties": {
                "AllProperties": False,
                "PropertyNames": ["LogicalName", "AttributeType", "DisplayName"],
            }
        }
    if entities:
        query["Criteria"] = {
            "FilterOperator": "Or",
            "Conditions": [
                {
                    "ConditionOperator": "Equals",
                    "PropertyName": "LogicalName",
                    "Value": {"Type": "System.String", "Value": e},
                }
                for e in entities
            ],
        }
    return query


def _shape_changed_entity(entity: dict[str, Any]) -> dict[str, Any]:
    """Project a raw EntityMetadata item to the fields the command surfaces."""
    out: dict[str, Any] = {
        "logical_name": entity.get("LogicalName"),
        "schema_name": entity.get("SchemaName"),
        "has_changed": entity.get("HasChanged"),
    }
    display = entity.get("DisplayName")
    if isinstance(display, dict):
        text = label_text(cast("dict[str, Any]", display))
        if text:
            out["display_name"] = text
    attrs = entity.get("Attributes")
    if isinstance(attrs, list):
        out["attributes"] = [
            {
                "logical_name": a.get("LogicalName"),
                "attribute_type": a.get("AttributeType"),
                "has_changed": a.get("HasChanged"),
            }
            for a in cast("list[dict[str, Any]]", attrs)
        ]
    return out


def metadata_changes(
    backend: D365Backend,
    *,
    since: str | None = None,
    entities: list[str] | None = None,
    attributes: bool = False,
) -> dict[str, Any]:
    """Retrieve new/changed entity metadata via ``RetrieveMetadataChanges``.

    Without ``since``, returns a baseline snapshot plus a fresh
    ``server_version_stamp`` to save for next time. With ``since`` (a stamp from
    a prior call), returns only metadata that changed since then, the count of
    items deleted since then, and a new stamp. ``entities`` scopes to specific
    logical names; ``attributes`` expands column definitions so column-level
    changes are visible. This is a read, so it executes even under ``--dry-run``.
    """
    query = _build_changes_query(entities, attributes)
    params: dict[str, str] = {"@p1": json.dumps(query, separators=(",", ":"))}
    parts: list[str] = ["Query=@p1"]
    if since:
        parts.append("ClientVersionStamp=@p2")
        params["@p2"] = odata_literal(since)
        parts.append(f"DeletedMetadataFilters={_DELETED_METADATA_FILTER_ALL}")
    resp = as_dict(backend.get(f"RetrieveMetadataChanges({','.join(parts)})", params=params))

    raw_entities = resp.get("EntityMetadata")
    entity_list: list[dict[str, Any]] = (
        cast("list[dict[str, Any]]", raw_entities) if isinstance(raw_entities, list) else []
    )
    shaped = [_shape_changed_entity(e) for e in entity_list]
    deleted = cast("dict[str, Any]", resp.get("DeletedMetadata") or {})
    deleted_count = deleted.get("Count") or 0
    return {
        "server_version_stamp": resp.get("ServerVersionStamp"),
        "entities": shaped,
        "count": len(shaped),
        "deleted_count": deleted_count,
    }
