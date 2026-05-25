"""Add attributes to existing entities — 14 typed builders + dispatcher.

`add_attribute` is the single public entry point. It validates the
kwarg matrix per `kind` (raising `D365Error` before any HTTP), routes
to a `_<kind>_attr` builder for the OData body, POSTs to
`EntityDefinitions(...)/Attributes`, and reads back the canonical
attribute fields. Lookup short-circuits to `create_one_to_many`.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import label, maybe_publish

_VALID_REQUIRED = {"None", "Recommended", "ApplicationRequired"}
_STRING_FORMATS = {"Text", "Email", "Url", "Phone", "TextArea", "TickerSymbol", "VersionNumber"}
_DATETIME_FORMATS = {"DateOnly", "DateAndTime"}

_NUMERIC_KINDS = {"integer", "bigint", "decimal", "double", "money"}  # pyright: ignore[reportUnusedVariable]
_LENGTH_KINDS = {"string", "memo"}  # pyright: ignore[reportUnusedVariable]
_PICKLIST_KINDS = {"picklist", "multiselect"}  # pyright: ignore[reportUnusedVariable]


def _require(kwargs: dict[str, Any], *names: str) -> None:
    for n in names:
        if kwargs.get(n) is None:
            raise D365Error(f"--{n} is required for this kind.")


def _forbid(kwargs: dict[str, Any], *names: str) -> None:
    for n in names:
        if kwargs.get(n) is not None:
            raise D365Error(f"--{n} is not valid for this kind.")


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
    if fmt not in _STRING_FORMATS:
        raise D365Error(f"format_name for string must be one of {sorted(_STRING_FORMATS)}.")
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


def _int_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "precision")
    return _common_numeric(opts, "Microsoft.Dynamics.CRM.IntegerAttributeMetadata")


def _bigint_attr(opts: dict[str, Any]) -> dict[str, Any]:
    _forbid(opts, "precision")
    return _common_numeric(opts, "Microsoft.Dynamics.CRM.BigIntAttributeMetadata")


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
) -> dict[str, Any]:
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
        return {"Name": optionset_name, "IsGlobal": True, "OptionSetType": "Picklist"}
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
    return {"Options": option_list, "IsGlobal": False, "OptionSetType": "Picklist"}


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
    body["OptionSet"] = _build_options_payload(
        opts.get("options"), opts.get("optionset_name"),
    )
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
    body["OptionSet"] = _build_options_payload(
        opts.get("options"), opts.get("optionset_name"),
    )
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
}


def _parse_attribute_id(entity_id_url: str | None) -> str | None:
    if not entity_id_url:
        return None
    match = re.search(r"Attributes\(([0-9a-fA-F-]{36})\)", entity_id_url)
    return match.group(1) if match else None


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
) -> dict[str, Any]:
    """Add an attribute (column) to an existing entity."""
    if "_" not in schema_name:
        raise D365Error("schema_name must include a publisher prefix.")
    logical_name = schema_name.lower()

    # Lookup is a special dispatch (covered in a later task).
    if kind == "lookup":
        raise D365Error("lookup kind not yet implemented in this build")

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
    body = builder(opts)

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    path = f"EntityDefinitions(LogicalName='{entity}')/Attributes"
    result = as_dict(backend.post(path, json_body=body, extra_headers=headers))
    if result.get("_dry_run"):
        return result

    entity_id_url = result.get("_entity_id_url")
    attr_id = _parse_attribute_id(entity_id_url)
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
    return out
