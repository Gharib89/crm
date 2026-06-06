"""Project a live D365 entity into an apply-consumable desired-state spec.

`build_entity_spec` is the inverse of `crm.core.scaffold.build_table_spec` /
`crm.core.apply.apply_spec`: it reads an existing custom entity over the Web API
and emits the EXACT `{"entities": [...]}` spec shape (plus a top-level
`"optionsets": [...]` when global option sets are referenced) so the result
round-trips through `crm.core.apply.validate_spec` / `apply_spec`.

It composes the read cores — it does not reimplement any read:
`metadata` (entity / attribute / picklist), `optionsets` (global set),
`relationships.read_entity_relationships`, `views.read_entity_views`.

Attribute kind is inverted from `AttributeTypeName.Value` (e.g. ``StringType``),
not the ambiguous `AttributeType` (a multiselect reports ``AttributeType:
"Virtual"``). Only kinds `apply` can create are emitted; system attributes
(Owner / State / Status / Uniqueidentifier / …) and the primary name attribute
are skipped — the latter is represented by the entity's `primary_attr`.

Fidelity notes:
- `precision` (decimal/double/money) and string `format_name` are forwarded to
  `add_attribute`, so they round-trip. A string `format_name` is emitted only when
  it is in `metadata_attrs.STRING_FORMATS`; a live `Json` / `RichText` format
  (which `apply` cannot create) is dropped and the column re-created as `Text`.
  Datetime format is NOT captured (re-created with the default format).
- A multiselect bound to a *local* option set has its options read via the
  Picklist cast (`picklist_options`), which the server returns for both kinds;
  inline `options` are emitted best-effort. A multiselect bound to a *global*
  set is handled the same as a picklist (emits `optionset_name`).
"""

from __future__ import annotations

from typing import Any, cast

from crm.core import metadata, optionsets, relationships, views
from crm.core.metadata_attrs import STRING_FORMATS
from crm.utils.d365_backend import D365Backend, D365Error

# AttributeTypeName.Value -> apply `kind`. Inverse of the metadata_attrs builders
# / @odata.type discriminators. Verified against MS Learn "Introduction to entity
# attributes" + "Types of columns". Any AttributeTypeName not in this map (Owner,
# State, Status, Uniqueidentifier, EntityName, ManagedProperty, PartyList,
# Customer, CalendarRules, Virtual, …) is a system kind apply cannot create and is
# skipped.
_TYPE_NAME_TO_KIND: dict[str, str] = {
    "StringType": "string",
    "MemoType": "memo",
    "IntegerType": "integer",
    "BigIntType": "bigint",
    "DecimalType": "decimal",
    "DoubleType": "double",
    "MoneyType": "money",
    "BooleanType": "boolean",
    "DateTimeType": "datetime",
    "PicklistType": "picklist",
    "MultiSelectPicklistType": "multiselect",
    "LookupType": "lookup",
    "ImageType": "image",
    "FileType": "file",
}

_PICKLIST_KINDS = frozenset({"picklist", "multiselect"})
_LENGTH_KINDS = frozenset({"string", "memo"})
_PRECISION_KINDS = frozenset({"decimal", "double", "money"})


def _as_dict(value: Any) -> dict[str, Any]:
    """Return `value` as a dict, or an empty dict when it is not a mapping/None.

    Narrows a nested `Any`-typed metadata field (e.g. `attr["AttributeTypeName"]`),
    distinct from `d365_backend.as_dict`, which narrows a `dict|str|None` response.
    """
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _type_name(attr: dict[str, Any]) -> str | None:
    """Extract `AttributeTypeName.Value` (the unambiguous kind discriminator)."""
    name_obj = _as_dict(attr.get("AttributeTypeName"))
    val = name_obj.get("Value")
    return val if isinstance(val, str) and val else None


def _required_level(attr: dict[str, Any]) -> str | None:
    """Extract `RequiredLevel.Value` (e.g. 'None' / 'ApplicationRequired')."""
    val = _as_dict(attr.get("RequiredLevel")).get("Value")
    return val if isinstance(val, str) and val else None


def _format_name(attr: dict[str, Any]) -> str | None:
    """Extract the string format from `FormatName.Value`, falling back to `Format`.

    A StringAttributeMetadata carries `FormatName` as a `{"Value": ...}` wrapper
    (cf. the `_string_attr` builder); some reads also expose a bare `Format`
    string. Emit whichever is present.
    """
    fmt = _as_dict(attr.get("FormatName")).get("Value")
    if isinstance(fmt, str) and fmt:
        return fmt
    bare = attr.get("Format")
    return bare if isinstance(bare, str) and bare else None


def _add_global_optionset(
    backend: D365Backend,
    name: str,
    accumulator: dict[str, dict[str, Any]],
) -> None:
    """Read a global option set once and stash it in the dedup accumulator."""
    if name in accumulator:
        return
    raw = optionsets.get_optionset(backend, name)
    display = metadata.label_text(_as_dict(raw.get("DisplayName")))
    accumulator[name] = {
        "name": name,
        "display_name": display or name,
        "options": metadata.flatten_options(raw),
    }


def _project_options(
    backend: D365Backend,
    logical_name: str,
    attr_logical: str,
    attr_out: dict[str, Any],
    accumulator: dict[str, dict[str, Any]],
) -> bool:
    """Resolve a picklist/multiselect's options onto `attr_out`.

    Global-bound -> `optionset_name` + add the set to `accumulator` (dedup).
    Local -> inline `options`. The Picklist cast returns options for both
    picklist and multiselect kinds, so one read covers both.

    Returns False when the attribute resolves to NEITHER a global
    `optionset_name` NOR a non-empty `options` list (empty/permission-limited
    cast). The caller skips such an attribute rather than emit a bare picklist
    or `options: []`, which `validate_spec` rejects.
    """
    pick = metadata.picklist_options(
        backend, logical_name, attr_logical, global_optionset=True
    )
    glob = _as_dict(pick.get("GlobalOptionSet"))
    glob_name = glob.get("Name")
    if isinstance(glob_name, str) and glob_name:
        attr_out["optionset_name"] = glob_name
        _add_global_optionset(backend, glob_name, accumulator)
        return True
    local = _as_dict(pick.get("OptionSet"))
    options = metadata.flatten_options(local)
    if not options:
        return False
    attr_out["options"] = options
    return True


def _project_attribute(
    backend: D365Backend,
    logical_name: str,
    kind: str,
    attr_logical: str,
    info: dict[str, Any],
    accumulator: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Project an already-deep-read attribute `info` into an apply attribute dict.

    Returns None when a required projection cannot be satisfied (e.g. a lookup
    with no resolvable target) so the caller can skip it rather than emit a spec
    that fails `validate_spec`. `info` is the `attribute_info` deep read the
    caller already fetched to derive `kind`, so no second per-attribute read.
    """
    schema_name = info.get("SchemaName")
    if not isinstance(schema_name, str) or not schema_name:
        return None

    out: dict[str, Any] = {
        "kind": kind,
        "schema_name": schema_name,
        "display_name": metadata.label_text(_as_dict(info.get("DisplayName"))),
    }

    description = metadata.label_text(_as_dict(info.get("Description")))
    if description:
        out["description"] = description

    required = _required_level(info)
    if required:
        out["required"] = required

    if kind in _LENGTH_KINDS:
        max_length = info.get("MaxLength")
        if not isinstance(max_length, int):
            return None  # apply requires max_length for string/memo (sparse read)
        out["max_length"] = max_length

    if kind == "string":
        # Emit format_name only when the live format is one apply can re-create;
        # Json / RichText are read-only kinds add_attribute rejects, so omit the
        # key and let the column round-trip as the default Text.
        fmt = _format_name(info)
        if fmt and fmt in STRING_FORMATS:
            out["format_name"] = fmt

    if kind in _PRECISION_KINDS:
        precision = info.get("Precision")
        if not isinstance(precision, int):
            return None  # apply requires precision for decimal/double/money (sparse read)
        out["precision"] = precision

    if kind == "lookup":
        targets = info.get("Targets")
        if not isinstance(targets, list) or not targets:
            return None  # apply requires target_entity for a lookup
        first = cast("list[Any]", targets)[0]
        if not isinstance(first, str) or not first:
            return None
        out["target_entity"] = first

    if kind in _PICKLIST_KINDS:
        if not _project_options(backend, logical_name, attr_logical, out, accumulator):
            return None  # apply requires optionset_name or a non-empty options list

    return out


def build_entity_spec(
    backend: D365Backend,
    logical_name: str,
    *,
    with_views: bool = False,
    with_relationships: bool = False,
) -> dict[str, Any]:
    """Project a live entity into an apply-consumable desired-state spec dict.

    Reads `logical_name` over the Web API (pure GETs) and returns
    ``{"entities": [<entity>]}``, adding a top-level ``"optionsets": [...]`` key
    when one or more attributes bind a global option set. The result passes
    `crm.core.apply.validate_spec` and round-trips through `apply_spec`.

    Only custom, apply-creatable attributes are emitted; the primary name
    attribute (carried as the entity's `primary_attr`) and system attributes are
    excluded. Publisher / solution are NOT emitted — an existing entity does not
    know its publisher; the operator supplies one via `crm apply --solution` or
    by editing the file.

    Args:
        with_views: When True, attach the entity's public views (via
            `views.read_entity_views`); the key is omitted when there are none.
        with_relationships: When True, attach the entity's custom 1:N
            relationships (via `relationships.read_entity_relationships`); the
            key is omitted when there are none.
    """
    ent = metadata.entity_info(backend, logical_name)

    schema_name = ent.get("SchemaName")
    if not isinstance(schema_name, str) or not schema_name:
        raise D365Error(f"entity {logical_name!r} has no SchemaName; cannot export.")

    primary_logical = ent.get("PrimaryNameAttribute")
    primary_logical = primary_logical if isinstance(primary_logical, str) else ""

    entity: dict[str, Any] = {
        "schema_name": schema_name,
        "display_name": metadata.label_text(_as_dict(ent.get("DisplayName"))),
    }
    collection = metadata.label_text(_as_dict(ent.get("DisplayCollectionName")))
    if collection:
        entity["display_collection_name"] = collection
    ownership = ent.get("OwnershipType")
    if isinstance(ownership, str) and ownership:
        entity["ownership"] = ownership

    optionset_acc: dict[str, dict[str, Any]] = {}

    # Enumerate attributes (shallow), keep only custom ones, deep-read each kept.
    shallow = metadata.list_attributes(backend, logical_name)
    attributes: list[dict[str, Any]] = []
    primary_attr: dict[str, Any] | None = None

    for shallow_attr in shallow:
        attr_logical = shallow_attr.get("LogicalName")
        if not isinstance(attr_logical, str) or not attr_logical:
            continue

        is_primary = bool(primary_logical) and attr_logical == primary_logical

        if not is_primary and not shallow_attr.get("IsCustomAttribute"):
            continue

        # The primary name attribute is represented by entity["primary_attr"],
        # never re-created as a column. Deep-read it only to capture schema+label.
        if is_primary:
            info = metadata.attribute_info(backend, logical_name, attr_logical)
            p_schema = info.get("SchemaName")
            primary_attr = {
                "schema_name": p_schema if isinstance(p_schema, str) else attr_logical,
                "label": metadata.label_text(_as_dict(info.get("DisplayName"))),
            }
            continue

        # Custom, non-primary: the shallow row lacks AttributeTypeName, so deep-read
        # once, map the kind from AttributeTypeName.Value, then project from the
        # same `info` (no second per-attribute read).
        info = metadata.attribute_info(backend, logical_name, attr_logical)
        type_name = _type_name(info)
        kind = _TYPE_NAME_TO_KIND.get(type_name) if type_name else None
        if kind is None:
            continue  # system / uncreatable kind — skip

        projected = _project_attribute(
            backend, logical_name, kind, attr_logical, info, optionset_acc
        )
        if projected is not None:
            attributes.append(projected)

    if primary_attr is not None:
        entity["primary_attr"] = primary_attr
    if attributes:
        entity["attributes"] = attributes

    if with_relationships:
        rels = relationships.read_entity_relationships(backend, logical_name)
        if rels:
            entity["relationships"] = rels

    if with_views:
        # validate_spec requires non-empty columns per view; a view with empty
        # columns (unparseable/empty layoutxml) would fail the spec — drop it.
        ent_views = [v for v in views.read_entity_views(backend, logical_name)
                     if v.get("columns")]
        if ent_views:
            entity["views"] = ent_views

    spec: dict[str, Any] = {"entities": [entity]}
    if optionset_acc:
        spec["optionsets"] = list(optionset_acc.values())
    return spec
