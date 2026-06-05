"""Schema metadata browsing.

Most metadata lives under `EntityDefinitions(...)` in the Web API. We hide the
quoting/path-building behind small helpers.
"""

from __future__ import annotations

import re
from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core import dependencies as dep_mod


def list_entities(
    backend: D365Backend,
    *,
    custom_only: bool = False,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """List entity definitions. Returns a list of `{LogicalName, EntitySetName, ...}` dicts.

    Note: `EntityDefinitions` does NOT support `$top` server-side (rejects with
    "The query parameter $top is not supported"), so we slice client-side after
    the response comes back.
    """
    params = {
        "$select": "LogicalName,EntitySetName,SchemaName,IsCustomEntity,DisplayName",
    }
    if custom_only:
        params["$filter"] = "IsCustomEntity eq true"

    result = as_dict(backend.get("EntityDefinitions", params=params))
    items = result.get("value", [])
    if top is not None:
        if top < 1:
            raise D365Error("--top must be >= 1")
        items = items[:top]
    return items


def entity_info(backend: D365Backend, logical_name: str) -> dict[str, Any]:
    """Retrieve the full entity definition for `logical_name`."""
    if not logical_name:
        raise D365Error("logical_name is required.")
    path = f"EntityDefinitions(LogicalName='{logical_name}')"
    return as_dict(backend.get(path))


def list_attributes(backend: D365Backend, logical_name: str) -> list[dict[str, Any]]:
    """List attributes for an entity (logical name)."""
    path = f"EntityDefinitions(LogicalName='{logical_name}')/Attributes"
    result = as_dict(backend.get(
        path,
        params={"$select": "LogicalName,SchemaName,AttributeType,IsCustomAttribute"},
    ))
    return result.get("value", [])


def attribute_info(backend: D365Backend, logical_name: str, attribute: str) -> dict[str, Any]:
    """Retrieve a single attribute definition."""
    path = (
        f"EntityDefinitions(LogicalName='{logical_name}')"
        f"/Attributes(LogicalName='{attribute}')"
    )
    return as_dict(backend.get(path))


def picklist_options(
    backend: D365Backend,
    logical_name: str,
    attribute: str,
    *,
    global_optionset: bool = True,
) -> dict[str, Any]:
    """Retrieve option set values for a picklist / state / status / boolean attribute.

    Casts to `Microsoft.Dynamics.CRM.PicklistAttributeMetadata` and expands
    `OptionSet` (local) + `GlobalOptionSet` (global, when present).

    Returns `{ "LogicalName": ..., "OptionSet": {...}, "GlobalOptionSet": {...} }`.
    Per MS Learn: https://learn.microsoft.com/power-apps/developer/data-platform/webapi/query-metadata-web-api#retrieve-attributes
    """
    if not logical_name or not attribute:
        raise D365Error("logical_name and attribute are required.")
    cast = "Microsoft.Dynamics.CRM.PicklistAttributeMetadata"
    path = (
        f"EntityDefinitions(LogicalName='{logical_name}')"
        f"/Attributes(LogicalName='{attribute}')/{cast}"
    )
    expand = "OptionSet" + (",GlobalOptionSet" if global_optionset else "")
    return as_dict(backend.get(
        path,
        params={"$select": "LogicalName", "$expand": expand},
    ))


def _label_text(label_obj: dict[str, Any]) -> str:
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
    its root. Labels use the robust `_label_text` fallback. A Boolean attribute
    has no `Options` array (it casts to `TrueOption` / `FalseOption`), so this
    returns `[]` for booleans — read those raw fields instead.
    """
    rows: list[dict[str, Any]] = container.get("Options") or []
    out: list[dict[str, Any]] = []
    for o in rows:
        lbl: dict[str, Any] = o.get("Label") or {}
        out.append({"value": o.get("Value"), "label": _label_text(lbl)})
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


def _enrich_lookups(
    backend: D365Backend, logical_name: str, writable: list[dict[str, Any]]
) -> None:
    """Attach `bind_key` + `targets[]` to each writable lookup attribute, in place.

    The bind key is self-derived from `ManyToOne` relationship metadata: a 1:N
    relationship's `ReferencedEntityNavigationPropertyName` is the navigation
    property used in a `<Nav>@odata.bind` deep-link, joined to the lookup column
    on `ReferencingAttribute`. Each target's `EntitySetName` is resolved so the
    agent has a usable bind VALUE (`/<set_name>(<id>)`). No-op when the entity
    has no lookup columns.
    """
    lookups = [a for a in writable if a["attribute_type"] == "Lookup"]
    if not lookups:
        return
    m2o = as_dict(backend.get(
        f"EntityDefinitions(LogicalName='{logical_name}')/ManyToOneRelationships",
        params={"$select":
                "ReferencingAttribute,ReferencedEntity,"
                "ReferencedEntityNavigationPropertyName"},
    ))
    rels: list[dict[str, Any]] = m2o.get("value", [])
    # ReferencingAttribute (lookup column) -> [(referenced_entity, nav_property)].
    by_attr: dict[str, list[tuple[str, str]]] = {}
    for r in rels:
        ref_attr = r.get("ReferencingAttribute")
        if not ref_attr:
            continue
        by_attr.setdefault(ref_attr, []).append((
            r.get("ReferencedEntity") or "",
            r.get("ReferencedEntityNavigationPropertyName") or "",
        ))

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
    attrs = as_dict(backend.get(
        f"EntityDefinitions(LogicalName='{logical_name}')/Attributes",
        params={"$select":
                "LogicalName,AttributeType,RequiredLevel,"
                "IsValidForCreate,IsValidForUpdate"},
    ))
    value: list[dict[str, Any]] = attrs.get("value", [])
    writable: list[dict[str, Any]] = []
    for a in value:
        if not (a.get("IsValidForCreate") or a.get("IsValidForUpdate")):
            continue
        required: dict[str, Any] = a.get("RequiredLevel") or {}
        writable.append({
            "logical_name": a.get("LogicalName"),
            "attribute_type": a.get("AttributeType"),
            "required_level": required.get("Value"),
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
    was_dry = backend.dry_run
    backend.dry_run = False
    try:
        backend.get(path, params={"$select": "MetadataId"})
        return True
    except D365Error as exc:
        if exc.status == 404:
            return False
        raise
    finally:
        backend.dry_run = was_dry


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
    if ownership not in ("UserOwned", "OrganizationOwned"):
        raise D365Error("ownership must be 'UserOwned' or 'OrganizationOwned'.")

    if if_exists not in ("error", "skip"):
        raise D365Error("if_exists must be 'error' or 'skip'.")

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

    # Read-back: parse MetadataId from the OData-EntityId URL, then GET the
    # server's authoritative EntitySetName. Failure here does NOT fail the
    # command — the entity was created.
    entity_set_name: str | None = None
    entity_set_lookup_error: str | None = None
    if not entity_id_url:
        entity_set_lookup_error = "OData-EntityId header missing from create response."
    else:
        match = re.search(r"EntityDefinitions\(([0-9a-fA-F-]{36})\)", entity_id_url)
        if not match:
            entity_set_lookup_error = (
                f"Could not parse MetadataId from OData-EntityId URL: {entity_id_url!r}"
            )
        else:
            metadata_id = match.group(1)
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
    was_dry = backend.dry_run
    backend.dry_run = False
    try:
        rb = as_dict(backend.get(
            path,
            params={"$select": "IsCustomEntity,IsManaged,MetadataId"},
        ))
    finally:
        backend.dry_run = was_dry
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
    return result


import xml.etree.ElementTree as _ET  # noqa: E402

_EDM_NS = "http://docs.oasis-open.org/odata/ns/edm"


_D365_NAMESPACE = "Microsoft.Dynamics.CRM"


def _fetch_csdl(backend: D365Backend) -> list[_ET.Element]:
    """GET $metadata and parse as XML. Returns all <Schema> elements."""
    raw = backend.get("$metadata", expect_json=False)
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
    items: list[dict[str, Any]] = []
    for elem in schema.findall(f"{{{_EDM_NS}}}{tag}"):
        params: list[dict[str, str]] = []
        for p in elem.findall(f"{{{_EDM_NS}}}Parameter"):
            params.append({
                "name": p.attrib.get("Name", ""),
                "type": p.attrib.get("Type", ""),
            })
        items.append({"name": elem.attrib.get("Name", ""), "parameters": params})
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
