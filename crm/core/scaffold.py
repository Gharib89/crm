"""Pure spec-builder for `scaffold table` — converts CLI shorthand to an apply spec.

`build_table_spec` accepts a table display name, a publisher prefix, and a list
of column shorthand strings (`"DISPLAY:KIND[:key=value,...]"`) and returns a
dict that `crm.core.apply.apply_spec` can consume directly. No network, no IO.

Column grammar::

    "Project Name:string:max_length=200,required=ApplicationRequired"
    "Owner:lookup:target_entity=systemuser"
    "Status:picklist:optionset_name=new_status"

All validation raises `D365Error` with a clear, specific message so failures
surface before any HTTP call, exactly like `apply.validate_spec`.
"""

from __future__ import annotations

import re
from typing import Any

from crm.core.metadata_attrs import ATTRIBUTE_KINDS
from crm.utils.d365_backend import D365Error

_VALID_REQUIRED = {"None", "Recommended", "ApplicationRequired"}
_ALLOWED_OPT_KEYS = {"max_length", "required", "target_entity", "optionset_name", "description"}

# Kinds that require max_length and their defaults.
_MAX_LENGTH_DEFAULTS: dict[str, int] = {"string": 100, "memo": 2000}
_PICKLIST_KINDS = frozenset({"picklist", "multiselect"})


def _pascal(token: str) -> str:
    """Convert a display-name token to PascalCase using word-boundary splitting.

    Matches the derivation rule used elsewhere in the codebase: split on
    non-alphanumeric runs, upper-case the first character of each word and
    preserve the rest, then join.

    Examples::

        _pascal("Project Task")  -> "ProjectTask"
        _pascal("due-date")      -> "DueDate"
        _pascal("code")          -> "Code"
    """
    return "".join(w[:1].upper() + w[1:] for w in re.split(r"[^0-9A-Za-z]+", token) if w)


def _derive_schema(prefix: str, display: str, label: str) -> str:
    """Derive ``<prefix>_<Pascal(display)>``, raising D365Error on empty result."""
    pascal = _pascal(display)
    if not pascal:
        raise D365Error(f"{label}: cannot derive schema name — {display!r} has no alphanumerics.")
    return f"{prefix}_{pascal}"


def _parse_opts(raw_opts: str, column_label: str) -> dict[str, Any]:
    """Parse comma-separated ``key=value`` pairs from the third column segment.

    Validates keys and value types; raises ``D365Error`` on any violation.
    """
    result: dict[str, Any] = {}
    for pair in raw_opts.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise D365Error(
                f"column {column_label!r}: malformed opt {pair!r} — expected key=value."
            )
        key, _, value = pair.partition("=")
        key = key.strip()
        value = value.strip()
        if key not in _ALLOWED_OPT_KEYS:
            raise D365Error(
                f"column {column_label!r}: unknown opt key {key!r}; "
                f"allowed: {sorted(_ALLOWED_OPT_KEYS)}."
            )
        if key == "max_length":
            try:
                int_val = int(value)
            except ValueError:
                raise D365Error(
                    f"column {column_label!r}: max_length must be a positive integer, got {value!r}."
                )
            if int_val <= 0:
                raise D365Error(
                    f"column {column_label!r}: max_length must be a positive integer, got {int_val}."
                )
            result[key] = int_val
        elif key == "required":
            if value not in _VALID_REQUIRED:
                raise D365Error(
                    f"column {column_label!r}: required must be one of "
                    f"{sorted(_VALID_REQUIRED)}, got {value!r}."
                )
            result[key] = value
        else:
            result[key] = value
    return result


def _build_attribute(
    prefix: str,
    display: str,
    kind: str,
    opts: dict[str, Any],
) -> dict[str, Any]:
    """Build a single attribute dict for the apply spec."""
    schema = _derive_schema(prefix, display, f"column {display!r}")

    attr: dict[str, Any] = {
        "kind": kind,
        "schema_name": schema,
        "display_name": display,
    }

    # Per-kind validation and default injection.
    if kind in _MAX_LENGTH_DEFAULTS:
        if "max_length" not in opts:
            attr["max_length"] = _MAX_LENGTH_DEFAULTS[kind]
        else:
            attr["max_length"] = opts["max_length"]
    elif "max_length" in opts:
        raise D365Error(
            f"column {display!r}: max_length is only valid for string/memo columns, not {kind!r}."
        )

    if kind == "lookup":
        if not opts.get("target_entity"):
            raise D365Error(
                f"lookup column {display!r} requires target_entity=<logical_name>."
            )
        attr["target_entity"] = opts["target_entity"]

    if kind in _PICKLIST_KINDS:
        if not opts.get("optionset_name"):
            raise D365Error(
                f"{kind} column {display!r} requires optionset_name=<name>; "
                "inline options are not supported in the scaffold shorthand."
            )
        attr["optionset_name"] = opts["optionset_name"]

    # Carry through the remaining optional fields.
    for key in ("required", "description"):
        if key in opts:
            attr[key] = opts[key]

    return attr


def _parse_column(raw: str, prefix: str) -> dict[str, Any]:
    """Parse one column shorthand string and return an attribute dict."""
    parts = raw.split(":", 2)
    display = parts[0].strip()
    if not display:
        raise D365Error(f"column shorthand {raw!r}: display name must not be empty.")

    if len(parts) < 2 or not parts[1].strip():
        raise D365Error(
            f"column {display!r}: kind is required (format: DISPLAY:KIND[:opts])."
        )
    kind = parts[1].strip()
    if kind not in ATTRIBUTE_KINDS:
        raise D365Error(
            f"column {display!r}: unknown kind {kind!r}; "
            f"valid kinds: {sorted(ATTRIBUTE_KINDS)}."
        )

    opts: dict[str, Any] = {}
    if len(parts) == 3 and parts[2].strip():
        opts = _parse_opts(parts[2], display)

    return _build_attribute(prefix, display, kind, opts)


def build_table_spec(
    *,
    display_name: str,
    columns: list[str],
    prefix: str,
    schema_name: str | None = None,
    display_collection: str | None = None,
    ownership: str = "UserOwned",
) -> dict[str, Any]:
    """Convert `scaffold table` CLI shorthand into an apply-spec dict.

    Args:
        display_name: Human-readable entity name (e.g. ``"Project"``).
        columns: Column shorthand strings, each ``"DISPLAY:KIND[:key=value,...]"``.
        prefix: Publisher prefix used to derive schema names (e.g. ``"new"``).
        schema_name: Override entity schema name verbatim; derived if omitted.
        display_collection: Plural display name for the entity set. Omitted from
            the spec when not provided (lets apply default it).
        ownership: Passed through to the entity spec; default ``"UserOwned"``.

    Returns:
        A spec dict shaped as ``{"entities": [...]}``, ready for ``apply_spec``.

    Raises:
        D365Error: For any malformed input — unknown kind, bad opts, empty name,
            missing required opts per kind, etc.
    """
    ent_schema = (
        schema_name
        if schema_name is not None
        else _derive_schema(prefix, display_name, "entity")
    )

    attributes = [_parse_column(col, prefix) for col in columns]

    entity: dict[str, Any] = {
        "schema_name": ent_schema,
        "display_name": display_name,
        "ownership": ownership,
        "attributes": attributes,
    }
    if display_collection is not None:
        entity["display_collection_name"] = display_collection

    return {"entities": [entity]}
