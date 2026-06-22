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
  it is in `metadata_constraints.STRING_FORMATS`; a live `Json` / `RichText` format
  (which `apply` cannot create) is dropped and the column re-created as `Text`.
  Datetime format is NOT captured (re-created with the default format).
- A multiselect's options are read via the MultiSelect cast
  (`multiselect_options`) — a multiselect column is NOT a PicklistAttributeMetadata,
  so the Picklist cast raises on it. A *local* set emits inline `options`; a
  *global* set is handled the same as a picklist (emits `optionset_name`).
"""

from __future__ import annotations

from typing import Any, cast

from crm.core import metadata, optionsets, relationships, views
from crm.core import metadata_constraints as mc
from crm.utils.d365_backend import D365Backend, D365Error

_PICKLIST_KINDS = frozenset({"picklist", "multiselect"})
_LENGTH_KINDS = frozenset({"string", "memo"})
_PRECISION_KINDS = frozenset({"decimal", "double", "money"})


def _as_dict(value: Any) -> dict[str, Any]:
    """Return `value` as a dict, or an empty dict when it is not a mapping/None.

    Narrows a nested `Any`-typed metadata field (e.g. `attr["AttributeTypeName"]`),
    distinct from `d365_backend.as_dict`, which narrows a `dict|str|None` response.
    """
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _label_or(label_value: str, fallback: str) -> str:
    """Return `label_value` when non-empty, else `fallback`.

    `metadata.label_text` returns "" on a missing / unlocalized label (sparse or
    permission-limited reads); `validate_spec` requires entity / attribute
    `display_name` to be truthy, so we fall back to the schema/logical name to
    keep the projected spec apply-consumable and deterministic.
    """
    return label_value or fallback


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
    kind: str,
    attr_logical: str,
    attr_out: dict[str, Any],
    accumulator: dict[str, dict[str, Any]],
    warnings: list[str],
) -> bool:
    """Resolve a picklist/multiselect's options onto `attr_out`.

    Global-bound -> `optionset_name` + add the set to `accumulator` (dedup).
    Local -> inline `options`. Dispatches by `kind`: a `picklist` reads via the
    Picklist cast, a `multiselect` via the MultiSelect cast — a multiselect column
    is NOT a `PicklistAttributeMetadata`, so the Picklist cast raises on it. Both
    metadata kinds carry `OptionSet` / `GlobalOptionSet` in the same shape.

    Returns False when the attribute resolves to NEITHER a global
    `optionset_name` NOR a non-empty `options` list (empty/permission-limited
    cast). The caller skips such an attribute rather than emit a bare picklist
    or `options: []`, which `validate_spec` rejects.
    """
    read = metadata.multiselect_options if kind == "multiselect" else metadata.picklist_options
    try:
        pick = read(backend, logical_name, attr_logical, global_optionset=True)
    except D365Error as exc:
        warnings.append(
            f"dropped column {attr_logical!r}: could not read its {kind} options ({exc})"
        )
        return False
    glob = _as_dict(pick.get("GlobalOptionSet"))
    glob_name = glob.get("Name")
    if isinstance(glob_name, str) and glob_name:
        try:
            _add_global_optionset(backend, glob_name, accumulator)
        except D365Error as exc:
            warnings.append(
                f"dropped column {attr_logical!r}: referenced global option set "
                f"{glob_name!r} is unreadable ({exc})"
            )
            return False
        attr_out["optionset_name"] = glob_name
        return True
    local = _as_dict(pick.get("OptionSet"))
    options = metadata.flatten_options(local)
    if not options:
        warnings.append(
            f"dropped column {attr_logical!r}: resolved option list is empty"
        )
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
    warnings: list[str],
) -> dict[str, Any] | None:
    """Project an already-deep-read attribute `info` into an apply attribute dict.

    Returns None when a required projection cannot be satisfied (e.g. a lookup
    with no resolvable target) so the caller can skip it rather than emit a spec
    that fails `validate_spec`. `info` is the `attribute_info` deep read the
    caller already fetched to derive `kind`, so no second per-attribute read.
    """
    schema_name = info.get("SchemaName")
    if not isinstance(schema_name, str) or not schema_name:
        warnings.append(f"dropped column {attr_logical!r}: no readable SchemaName")
        return None

    out: dict[str, Any] = {
        "kind": kind,
        "schema_name": schema_name,
        # validate_spec requires display_name truthy; a sparse/unlocalized label
        # reads as "" -> fall back to the schema name so the spec stays appliable.
        "display_name": _label_or(
            metadata.label_text(_as_dict(info.get("DisplayName"))), schema_name
        ),
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
            warnings.append(
                f"dropped column {attr_logical!r}: {kind} has no readable MaxLength"
            )
            return None
        out["max_length"] = max_length

    if kind == "string":
        # Emit format_name only when the live format is one apply can re-create;
        # Json / RichText are read-only kinds add_attribute rejects, so omit the
        # key and let the column round-trip as the default Text.
        fmt = _format_name(info)
        if fmt and fmt in mc.STRING_FORMATS:
            out["format_name"] = fmt

    if kind in _PRECISION_KINDS:
        precision = info.get("Precision")
        if not isinstance(precision, int):
            warnings.append(
                f"dropped column {attr_logical!r}: {kind} has no readable Precision"
            )
            return None
        out["precision"] = precision

    if kind == "lookup":
        targets = info.get("Targets")
        if not isinstance(targets, list) or not targets:
            warnings.append(
                f"dropped column {attr_logical!r}: lookup has no resolvable target entity"
            )
            return None
        first = cast("list[Any]", targets)[0]
        if not isinstance(first, str) or not first:
            warnings.append(
                f"dropped column {attr_logical!r}: lookup target entry is not a valid string"
            )
            return None
        # A polymorphic lookup (len(Targets) > 1) exports only the first target
        # because apply creates single-target lookups (add_attribute → one
        # referenced entity). Capturing all targets would not round-trip.
        out["target_entity"] = first

    if kind in _PICKLIST_KINDS:
        if not _project_options(
            backend, logical_name, kind, attr_logical, out, accumulator, warnings
        ):
            return None  # _project_options already appended the reason

    return out


def build_entity_spec(
    backend: D365Backend,
    logical_name: str,
    *,
    with_views: bool = False,
    with_relationships: bool = False,
    warnings: list[str] | None = None,
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
        warnings: Optional list to accumulate structured drop-reason strings.
            Each silently skipped attribute appends one entry. Callers that
            want to surface diagnostics pass an empty list here; callers that
            don't care may omit the argument.
    """
    warn = warnings if warnings is not None else []
    ent = metadata.entity_info(backend, logical_name)

    schema_name = ent.get("SchemaName")
    if not isinstance(schema_name, str) or not schema_name:
        raise D365Error(f"entity {logical_name!r} has no SchemaName; cannot export.")

    primary_logical = ent.get("PrimaryNameAttribute")
    primary_logical = primary_logical if isinstance(primary_logical, str) else ""

    entity: dict[str, Any] = {
        "schema_name": schema_name,
        # validate_spec requires entity display_name truthy; fall back to the
        # schema name when the label reads empty (sparse/permission-limited).
        "display_name": _label_or(
            metadata.label_text(_as_dict(ent.get("DisplayName"))), schema_name
        ),
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

        # Skip non-primary attributes that are not independently creatable: both
        # non-custom system columns and the server-auto-generated …Name/…YomiName
        # companions of lookup/customer attributes (IsCustomAttribute=true but
        # IsValidForCreate=false). Emitting a companion as a standalone column
        # collides with the one the server auto-generates when apply re-creates
        # the parent lookup ("attribute …Name already exists", #497).
        if not is_primary and (
            not shallow_attr.get("IsCustomAttribute")
            or not shallow_attr.get("IsValidForCreate")
        ):
            continue

        # The primary name attribute is represented by entity["primary_attr"],
        # never re-created as a column. Deep-read it only to capture schema+label.
        if is_primary:
            info = metadata.attribute_info(backend, logical_name, attr_logical)
            p_schema = info.get("SchemaName")
            p_schema = p_schema if isinstance(p_schema, str) and p_schema else attr_logical
            primary_attr = {
                "schema_name": p_schema,
                # Keep the label non-empty (deterministic) when unlocalized/sparse.
                "label": _label_or(
                    metadata.label_text(_as_dict(info.get("DisplayName"))), p_schema
                ),
            }
            continue

        # Custom, non-primary: the shallow row lacks AttributeTypeName, so deep-read
        # once, map the kind from AttributeTypeName.Value, then project from the
        # same `info` (no second per-attribute read).
        info = metadata.attribute_info(backend, logical_name, attr_logical)
        type_name = _type_name(info)
        kind = mc.kind_for_type_name(type_name) if type_name else None
        if kind is None:
            warn.append(
                f"dropped column {attr_logical!r}: attribute type "
                f"{type_name!r} is not one apply can create"
            )
            continue

        projected = _project_attribute(
            backend, logical_name, kind, attr_logical, info, optionset_acc, warn
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
        # validate_spec requires both a non-empty name and non-empty columns per
        # view; drop any view that fails either check so the spec stays valid.
        ent_views = [v for v in views.read_entity_views(backend, logical_name)
                     if v.get("name") and v.get("columns")]
        if ent_views:
            entity["views"] = ent_views

    spec: dict[str, Any] = {"entities": [entity]}
    if optionset_acc:
        spec["optionsets"] = list(optionset_acc.values())
    return spec
