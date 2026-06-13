"""Add attributes to existing entities — 14 typed builders + dispatcher.

`add_attribute` is the single public entry point. It validates the
kwarg matrix per `kind` (raising `D365Error` before any HTTP), routes
to a `_<kind>_attr` builder for the OData body, POSTs to
`EntityDefinitions(...)/Attributes`, and reads back the canonical
attribute fields. Lookup short-circuits to `create_one_to_many`.
"""

from __future__ import annotations

from typing import Any, Callable

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import label, maybe_publish, target_exists
from crm.core import dependencies as dep_mod
from crm.core import metadata_cache
from crm.core import references as ref_mod

_VALID_REQUIRED = {"None", "Recommended", "ApplicationRequired"}
# The string formats add_attribute can create. Public so export_spec can filter
# live FormatName values to the creatable set without a private cross-module import.
STRING_FORMATS = {"Text", "Email", "Url", "Phone", "TextArea", "TickerSymbol", "VersionNumber"}
_DATETIME_FORMATS = {"DateOnly", "DateAndTime"}

_NUMERIC_KINDS = {"integer", "bigint", "decimal", "double", "money"}  # pyright: ignore[reportUnusedVariable]
_LENGTH_KINDS = {"string", "memo"}  # pyright: ignore[reportUnusedVariable]
_PICKLIST_KINDS = {"picklist", "multiselect"}


def _require(kwargs: dict[str, Any], *names: str) -> None:
    for n in names:
        if kwargs.get(n) is None:
            raise D365Error(f"--{n.replace('_', '-')} is required for this kind.")


def _forbid(kwargs: dict[str, Any], *names: str) -> None:
    for n in names:
        if kwargs.get(n) is not None:
            raise D365Error(f"--{n.replace('_', '-')} is not valid for this kind.")


def _base_attr_payload(
    *, schema_name: str, logical_name: str, display_name: str,
    description: str | None, required: str,
) -> dict[str, Any]:
    if required not in _VALID_REQUIRED:
        raise D365Error(f"required must be one of {sorted(_VALID_REQUIRED)}.")
    payload: dict[str, Any] = {
        "SchemaName": schema_name,
        "LogicalName": logical_name,
        "DisplayName": label(display_name),
        "RequiredLevel": {"Value": required},
    }
    if description:
        payload["Description"] = label(description)
    return payload


def _string_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "precision", "target_entity", "optionset_name", "options",
            "min_value", "max_value", "max_size_kb")
    _require(opts, "max_length")
    fmt = opts.get("format_name") or "Text"
    if fmt not in STRING_FORMATS:
        raise D365Error(f"format_name for string must be one of {sorted(STRING_FORMATS)}.")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = "Microsoft.Dynamics.CRM.StringAttributeMetadata"
    body["MaxLength"] = opts["max_length"]
    body["FormatName"] = {"Value": fmt}
    return body


def _memo_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "precision", "target_entity", "optionset_name", "options",
            "min_value", "max_value", "max_size_kb")
    _require(opts, "max_length")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = "Microsoft.Dynamics.CRM.MemoAttributeMetadata"
    body["MaxLength"] = opts["max_length"]
    body["Format"] = "TextArea"
    return body


def _common_numeric(opts: dict[str, Any], odata_type: str) -> dict[str, Any]:
    _forbid(opts, "max_length", "target_entity", "optionset_name", "options",
            "format_name", "max_size_kb")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = odata_type
    if opts.get("min_value") is not None:
        body["MinValue"] = opts["min_value"]
    if opts.get("max_value") is not None:
        body["MaxValue"] = opts["max_value"]
    return body


def _coerce_int_bounds(body: dict[str, Any]) -> None:
    """Force MinValue/MaxValue to Edm.Int32 integers.

    The CLI parses ``--min``/``--max`` as floats, so a bound of ``0`` arrives as
    ``0.0`` and serializes to JSON ``0.0`` (Edm.Decimal), which the server rejects
    for an integer column ("Cannot convert the literal '0.0' to the expected type
    'Edm.Int32'"). Integer/bigint bounds are whole numbers by definition, so a
    fractional bound (e.g. ``0.9``) is a user error — reject it rather than
    silently truncating to ``0``.
    """
    for key in ("MinValue", "MaxValue"):
        val = body.get(key)
        if val is None:
            continue
        if isinstance(val, float) and not val.is_integer():
            raise D365Error(
                f"{key} for an integer attribute must be a whole number, got {val}."
            )
        body[key] = int(val)


def _int_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "precision")
    body = _common_numeric(opts, "Microsoft.Dynamics.CRM.IntegerAttributeMetadata")
    _coerce_int_bounds(body)
    return body


def _bigint_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "precision")
    body = _common_numeric(opts, "Microsoft.Dynamics.CRM.BigIntAttributeMetadata")
    _coerce_int_bounds(body)
    return body


def _numeric_with_precision(
    opts: dict[str, Any], odata_type: str, precision_range: tuple[int, int],
) -> dict[str, Any]:
    _require(opts, "precision")
    prec = opts["precision"]
    lo, hi = precision_range
    if not (lo <= prec <= hi):
        raise D365Error(f"precision for this kind must be in [{lo}, {hi}].")
    body = _common_numeric(opts, odata_type)
    body["Precision"] = prec
    return body


def _decimal_attr(opts: dict[str, Any]) -> dict[str, Any]:
    return _numeric_with_precision(
        opts, "Microsoft.Dynamics.CRM.DecimalAttributeMetadata", (0, 10),
    )


def _double_attr(opts: dict[str, Any]) -> dict[str, Any]:
    return _numeric_with_precision(
        opts, "Microsoft.Dynamics.CRM.DoubleAttributeMetadata", (0, 5),
    )


def _money_attr(opts: dict[str, Any]) -> dict[str, Any]:
    return _numeric_with_precision(
        opts, "Microsoft.Dynamics.CRM.MoneyAttributeMetadata", (0, 4),
    )


def _bool_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "max_length", "precision", "target_entity", "optionset_name",
            "options", "format_name", "min_value", "max_value", "max_size_kb")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = "Microsoft.Dynamics.CRM.BooleanAttributeMetadata"
    body["OptionSet"] = {
        "TrueOption": {"Value": 1, "Label": label(opts.get("true_label", "Yes"))},
        "FalseOption": {"Value": 0, "Label": label(opts.get("false_label", "No"))},
        "OptionSetType": "Boolean",
    }
    if opts.get("default_value") is not None:
        body["DefaultValue"] = bool(opts["default_value"])
    return body


def _datetime_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "max_length", "precision", "target_entity", "optionset_name",
            "options", "min_value", "max_value", "max_size_kb")
    fmt = opts.get("format_name") or "DateAndTime"
    if fmt not in _DATETIME_FORMATS:
        raise D365Error(f"format_name for datetime must be one of {sorted(_DATETIME_FORMATS)}.")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = "Microsoft.Dynamics.CRM.DateTimeAttributeMetadata"
    body["Format"] = fmt
    return body


def _build_options_payload(
    options: list[tuple[int | None, str]] | None,
    optionset_name: str | None,
    optionset_metadata_id: str | None = None,
) -> dict[str, Any]:
    """Return the body fragment that attaches options to a picklist/multiselect.

    A *global* option set is attached through the ``GlobalOptionSet`` single-valued
    navigation property via ``@odata.bind``. On-prem 9.x requires the ``MetadataId``
    GUID as the bind key — the ``Name`` alternate key is rejected there ("Guid
    should contain 32 digits...") even though it works for GET — so the caller
    resolves the id first and passes it as ``optionset_metadata_id``; the ``Name``
    key is only used as an offline fallback. The server also rejects an inline
    ``OptionSet`` with ``IsGlobal=true`` on attribute create ("Only Local option
    set can be created through the attribute create."). An inline (local) option
    set is embedded under the ``OptionSet`` property.
    """
    has_inline = bool(options)
    has_global = bool(optionset_name)
    if has_inline and has_global:
        raise D365Error(
            "--options and --optionset-name are mutually exclusive."
        )
    if not has_inline and not has_global:
        raise D365Error(
            "either optionset_name or options is required for picklist/multiselect."
        )
    if has_global:
        key = optionset_metadata_id or f"Name='{optionset_name}'"
        return {"GlobalOptionSet@odata.bind": f"GlobalOptionSetDefinitions({key})"}
    seen: set[int] = set()
    option_list: list[dict[str, Any]] = []
    assert options is not None  # has_inline ensures non-empty
    for value, lbl in options:
        if value is not None:
            if value in seen:
                raise D365Error(f"Duplicate option value: {value}.")
            seen.add(value)
        if not lbl:
            raise D365Error("Option label must not be empty.")
        opt: dict[str, Any] = {"Label": label(lbl)}
        if value is not None:
            opt["Value"] = value
        option_list.append(opt)
    return {
        "OptionSet": {
            "Options": option_list, "IsGlobal": False, "OptionSetType": "Picklist",
        }
    }


def _picklist_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "max_length", "precision", "target_entity",
            "min_value", "max_value", "format_name", "max_size_kb")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = "Microsoft.Dynamics.CRM.PicklistAttributeMetadata"
    body.update(_build_options_payload(
        opts.get("options"), opts.get("optionset_name"),
        opts.get("optionset_metadata_id"),
    ))
    if opts.get("default_value") is not None:
        body["DefaultFormValue"] = int(opts["default_value"])
    return body


def _multiselect_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "max_length", "precision", "target_entity",
            "min_value", "max_value", "format_name", "max_size_kb")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = "Microsoft.Dynamics.CRM.MultiSelectPicklistAttributeMetadata"
    body.update(_build_options_payload(
        opts.get("options"), opts.get("optionset_name"),
        opts.get("optionset_metadata_id"),
    ))
    return body


def _image_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "max_length", "precision", "target_entity",
            "min_value", "max_value", "optionset_name", "options",
            "format_name", "max_size_kb")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = "Microsoft.Dynamics.CRM.ImageAttributeMetadata"
    body["IsPrimaryImage"] = True
    return body


def _file_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "max_length", "precision", "target_entity",
            "min_value", "max_value", "optionset_name", "options",
            "format_name")
    body = _base_attr_payload(
        schema_name=opts["schema_name"],
        logical_name=opts["logical_name"],
        display_name=opts["display_name"],
        description=opts.get("description"),
        required=opts.get("required", "None"),
    )
    body["@odata.type"] = "Microsoft.Dynamics.CRM.FileAttributeMetadata"
    body["MaxSizeInKB"] = opts.get("max_size_kb") or 32768
    return body


_BUILDERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "string": _string_attr,
    "memo": _memo_attr,
    "integer": _int_attr,
    "bigint": _bigint_attr,
    "decimal": _decimal_attr,
    "double": _double_attr,
    "money": _money_attr,
    "boolean": _bool_attr,
    "datetime": _datetime_attr,
    "picklist": _picklist_attr,
    "multiselect": _multiselect_attr,
    "image": _image_attr,
    "file": _file_attr,
}

# Every attribute kind add_attribute accepts: the builder-backed kinds plus the
# special-cased "lookup". Exposed so callers (e.g. crm.core.apply) validate
# against one source of truth and never drift.
ATTRIBUTE_KINDS = frozenset(_BUILDERS) | {"lookup"}


def _resolve_global_optionset_id(backend: D365Backend, name: str) -> str:
    """Return the MetadataId GUID of a global option set, looked up by Name.

    Needed because the ``GlobalOptionSet`` @odata.bind on attribute create only
    accepts the MetadataId key on on-prem 9.x, not the Name alternate key.
    """
    rb = as_dict(backend.get(
        f"GlobalOptionSetDefinitions(Name='{name}')",
        params={"$select": "MetadataId"},
    ))
    metadata_id = rb.get("MetadataId")
    if not metadata_id:
        raise D365Error(
            f"Could not resolve MetadataId for global option set {name!r}."
        )
    return str(metadata_id)


def delete_attribute(
    backend: D365Backend,
    entity: str,
    attribute: str,
    *,
    solution: str | None = None,
    check_dependencies: bool = False,
) -> dict[str, Any]:
    """Delete a custom attribute (column) from an entity.

    Pre-flight: refuses managed, non-custom, primary (id/name), and
    sub-attributes (`AttributeOf` set — e.g. a Money's _base or a
    composite's parts, which the server deletes with their parent).
    Server enforces remaining-dependency checks and returns 4xx on conflict.

    Args:
        check_dependencies: When True, call RetrieveDependenciesForDelete
            before the DELETE and fold ``can_delete`` + ``blockers`` into the
            result. Informational only — does not abort the delete.
    """
    if not entity or not attribute:
        raise D365Error("entity and attribute are required.")
    path = (
        f"EntityDefinitions(LogicalName='{entity}')"
        f"/Attributes(LogicalName='{attribute}')"
    )
    rb = as_dict(backend.get(path, params={
        "$select": "IsCustomAttribute,IsManaged,IsPrimaryId,IsPrimaryName,AttributeOf,MetadataId",
    }))
    if rb.get("IsCustomAttribute") is False:
        raise D365Error(
            f"{attribute!r} is not a custom attribute; refusing to delete.",
            code="NotCustomAttribute",
        )
    if rb.get("IsManaged") is True:
        raise D365Error(
            f"{attribute!r} is managed; uninstall the parent solution to remove it.",
            code="ManagedAttribute",
        )
    if rb.get("IsPrimaryId") is True or rb.get("IsPrimaryName") is True:
        raise D365Error(
            f"{attribute!r} is a primary attribute; refusing to delete.",
            code="PrimaryAttribute",
        )
    if rb.get("AttributeOf"):
        raise D365Error(
            f"{attribute!r} is a sub-attribute of {rb['AttributeOf']!r}; "
            "delete the parent attribute instead.",
            code="SubAttribute",
        )
    deps = None
    if check_dependencies:
        _mid = rb.get("MetadataId")
        if isinstance(_mid, str) and _mid:
            deps = dep_mod.dependencies_by_id(backend, _mid, 2, for_="delete", kind="attribute")
        else:
            deps = dep_mod.retrieve_dependencies(
                backend, "attribute", f"{entity}.{attribute}", for_="delete"
            )
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    preview = backend.delete(path, extra_headers=headers)
    if isinstance(preview, dict) and preview.get("_dry_run"):
        result: dict[str, Any] = {
            "_dry_run": True,
            "would_delete": True,
            "entity": entity,
            "attribute": attribute,
            "solution": solution,
        }
    else:
        result = {
            "deleted": True,
            "entity": entity,
            "attribute": attribute,
            "solution": solution,
        }
    if deps is not None:
        result["can_delete"] = deps["can_delete"]
        result["blockers"] = deps["blockers"]
    if not backend.dry_run:
        metadata_cache.invalidate(backend.profile)
    return result


def add_attribute(
    backend: D365Backend,
    *,
    entity: str,
    kind: str,
    schema_name: str,
    display_name: str,
    description: str | None = None,
    required: str = "None",
    max_length: int | None = None,
    format_name: str | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    precision: int | None = None,
    default_value: bool | int | None = None,
    true_label: str = "Yes",
    false_label: str = "No",
    optionset_name: str | None = None,
    options: list[tuple[int | None, str]] | None = None,
    target_entity: str | None = None,
    relationship_schema: str | None = None,
    max_size_kb: int | None = None,
    publish: bool = False,
    solution: str | None = None,
    if_exists: str = "error",
) -> dict[str, Any]:
    """Add an attribute (column) to an existing entity."""
    if "_" not in schema_name:
        raise D365Error("schema_name must include a publisher prefix.")
    if if_exists not in ("error", "skip"):
        raise D365Error("if_exists must be 'error' or 'skip'.")
    logical_name = schema_name.lower()

    if kind == "lookup":
        if target_entity is None:
            raise D365Error("--target-entity is required for lookup attribute.")
        _forbid_kwargs = {
            "max_length": max_length, "precision": precision,
            "min_value": min_value, "max_value": max_value,
            "format_name": format_name,
            "optionset_name": optionset_name, "options": options,
            "max_size_kb": max_size_kb,
        }
        for n, v in _forbid_kwargs.items():
            if v is not None:
                raise D365Error(f"--{n.replace('_', '-')} is not valid for lookup.")
        from crm.core import relationships as rel
        rel_schema = relationship_schema or f"{entity}_{logical_name}"
        result = rel.create_one_to_many(
            backend,
            schema_name=rel_schema,
            referenced_entity=target_entity,
            referencing_entity=entity,
            lookup_schema=schema_name,
            lookup_display=display_name,
            lookup_required=required,
            lookup_description=description,
            publish=publish,
            solution=solution,
            if_exists=if_exists,
        )
        if backend.dry_run and result.get("references"):
            # A lookup is a 1:N under the hood; surface its one user-named
            # reference (the target entity == the relationship's referenced
            # entity) in --target-entity vocabulary. The referencing entity is
            # the host table being edited, so it is dropped from the preview.
            result["references"] = [
                {**r, "kind": "target_entity"}
                for r in result["references"]
                if r["kind"] == "referenced_entity"
            ]
        return result

    builder = _BUILDERS.get(kind)
    if builder is None:
        raise D365Error(f"unknown attribute kind: {kind!r}")

    opts: dict[str, Any] = {
        "schema_name": schema_name,
        "logical_name": logical_name,
        "display_name": display_name,
        "description": description,
        "required": required,
        "max_length": max_length,
        "format_name": format_name,
        "min_value": min_value,
        "max_value": max_value,
        "precision": precision,
        "default_value": default_value,
        "true_label": true_label,
        "false_label": false_label,
        "optionset_name": optionset_name,
        "options": options,
        "target_entity": target_entity,
        "max_size_kb": max_size_kb,
    }
    # Resolve the global option set's MetadataId only when it is the sole source;
    # if inline options are also given, let the builder raise the mutual-exclusivity
    # error instead of making a network call first.
    references: list[ref_mod.Reference] = []
    if kind in _PICKLIST_KINDS and optionset_name and not options:
        if backend.dry_run:
            # Under dry-run, report the referenced option set's existence rather
            # than raising on a dangling one (#281). When present, reuse its id so
            # the preview body matches the real write; when absent the builder
            # falls back to the Name bind for the (echo-only) preview.
            os_id = ref_mod.resolve_global_optionset_id(backend, optionset_name)
            references.append(ref_mod.make_reference(
                "optionset", optionset_name, os_id is not None))
            if os_id is not None:
                opts["optionset_metadata_id"] = os_id
        else:
            opts["optionset_metadata_id"] = _resolve_global_optionset_id(
                backend, optionset_name
            )
    body = builder(opts)

    exists = target_exists(
        backend,
        f"EntityDefinitions(LogicalName='{entity}')"
        f"/Attributes(LogicalName='{logical_name}')",
    )
    if exists and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(
                f"Attribute {logical_name!r} already exists on entity {entity!r}.",
                code="AlreadyExists",
            )
        return {
            "skipped": True,
            "exists": True,
            "entity": entity,
            "schema_name": schema_name,
            "logical_name": logical_name,
        }

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    path = f"EntityDefinitions(LogicalName='{entity}')/Attributes"
    result = as_dict(backend.post(path, json_body=body, extra_headers=headers))
    if result.get("_dry_run"):
        result["_exists"] = exists
        result["would_skip"] = exists and if_exists == "skip"
        if references:
            result["references"] = references
        return result

    entity_id_url = result.get("_entity_id_url")
    attr_id: str | None = result.get("_entity_id")
    attr_logical: str | None = None
    attr_type: str | None = None
    lookup_error: str | None = None
    if not attr_id:
        lookup_error = f"Could not parse AttributeId from response: {entity_id_url!r}"
    else:
        try:
            rb = as_dict(backend.get(
                f"EntityDefinitions(LogicalName='{entity}')/Attributes({attr_id})",
                params={"$select": "LogicalName,SchemaName,AttributeType"},
            ))
            attr_logical = rb.get("LogicalName")
            attr_type = rb.get("AttributeType")
        except D365Error as exc:
            lookup_error = f"Read-back failed: {exc}"

    out: dict[str, Any] = {
        "created": True,
        "entity": entity,
        "schema_name": schema_name,
        "logical_name": logical_name,
        "attribute_type": attr_type,
        "attribute_logical_name": attr_logical,
        "metadata_id_url": entity_id_url,
        "solution": solution,
    }
    if lookup_error:
        out["attribute_lookup_error"] = lookup_error
    maybe_publish(backend, out, publish)
    if not backend.dry_run:
        metadata_cache.invalidate(backend.profile)
    return out
