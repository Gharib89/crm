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

Wider apply-surface emit (#597): each kind's projected dict mirrors the fields its
`apply.REGISTRY` adapter accepts, read from the live metadata and emitted only when
*non-default* (a field equal to the platform/builder default is omitted so the spec
does not bloat with defaults). Covered now:
- relationship — flat `cascade_*` (six dimensions), `menu_behavior`/`menu_label`/
  `menu_order`, `is_hierarchical`, and the lookup column's `lookup_description`
  (see `relationships.read_entity_relationships`). The lookup column's
  `lookup_schema` carries the referencing attribute's SchemaName casing (read from
  the attribute metadata, not the relationship's lowercase ReferencingAttribute),
  so a round-tripped org re-creates the column with matching casing (#701);
- view — `filter_active`, `order_desc`, `description` (see `views.read_entity_views`);
- attribute — `auto_number_format` (string), `min_value`/`max_value` (integer/bigint),
  `behavior_name` (datetime), `max_size_kb` (file);
- entity — `has_notes`, `has_activities`, `primary_attr_max_length`, `description`;
- optionset (global) — `description` (#701).

Descriptions (entity / optionset / view) are emitted only when non-empty and wherever
the corresponding `apply` adapter already consumes them: entity and view descriptions
are reconciled on update, while the global option set description is applied on create
(the optionset reconcile inserts only missing options, so a drifted description is not
updated on re-apply). Attribute and lookup-column descriptions were already emitted.

Adapter fields intentionally NOT emitted (documented gaps, not oversights) are
declared as data in `EXPORT_GAPS` below — each key carries its own reason — and
enforced against the live adapter surface by the export↔apply contract test
(`test_export_spec.py`, #787), so an undeclared, unemitted adapter key is a test
failure rather than a silent round-trip fidelity loss. `KINDS_NOT_EXPORTED`
records the whole registry kinds the exporter cannot project at all (the plug-in
kinds — ADR 0019). (The decimal/double/money `min_value`/`max_value` nuance lives
next to `_INT_BOUND_DEFAULTS` below: those keys ARE emitted for integer/bigint,
but stay omitted for decimal/double/money, whose default ranges are
version-sensitive — so `min_value`/`max_value` are in `EXPORTED_KEYS`, not gaps.)
"""

from __future__ import annotations

from typing import Any, Callable, cast

from crm.core import (
    forms,
    metadata,
    optionsets,
    relationships,
    solution_components,
    views,
    xml_edit,
)
from crm.core import metadata_constraints as mc
from crm.utils.d365_backend import D365Backend, D365Error, as_dict, odata_literal

_PICKLIST_KINDS = frozenset({"picklist", "multiselect"})
_LENGTH_KINDS = frozenset({"string", "memo"})
_PRECISION_KINDS = frozenset({"decimal", "double", "money"})
# Integer/bigint full-range bounds: a column reading the type's full range was
# created without explicit min/max, so the bound is omitted (no default bloat).
# Decimal/double/money are deliberately absent — their platform default ranges
# are version-sensitive, so their bounds are not projected (see module docstring).
_INT_BOUND_DEFAULTS: dict[str, tuple[int, int]] = {
    "integer": (-2147483648, 2147483647),
    "bigint": (-9223372036854775808, 9223372036854775807),
}
_FILE_DEFAULT_MAX_KB = 32768
# Datetime behavior other than the platform default (UserLocal) round-trips.
_DATETIME_DEFAULT_BEHAVIOR = "UserLocal"
# SourceType ints apply can re-create (add_attribute --type): 1=calculated, 2=rollup.
# 0/None (simple) and 3/4 (formula/prompt, which apply cannot create) map to None.
_SOURCE_TYPES = {1: "calculated", 2: "rollup"}


# ── Export-gap contract (#787) ───────────────────────────────────────────────
#
# The apply adapters (`apply.REGISTRY`) are the source of truth for the spec keys
# each kind accepts; the projectors in this module emit a subset. An adapter spec
# key that lands in NEITHER the emit code NOR a declared gap is a silent fidelity
# loss in the export→apply round-trip (the exported spec re-applies cleanly but
# quietly drops the facet — the loss ADR 0019 exists to protect against). The data
# below reifies that boundary so the export↔apply contract test fails on an
# undeclared, unemitted key instead of losing it silently.
#
# The surface this contract models = `REGISTRY[kind].map` keys ∪ the spec keys the
# kind's transforms consume — i.e. the keys that ride the generic `map` projection.
# It is deliberately NOT the full set of spec keys a kind accepts: spec keys that
# reach the builder some other way are outside this contract (the projectors may
# still emit them). Two such classes exist — driver-`injected` params the runtime
# computes (web-resource `content`/`webresourcetype`) and keys a kind's
# `extra_validate`/`reconcile` reads directly off the block (web-resource `file`,
# security-role `privileges`) — neither goes through the generic `map` projection.

# Spec keys a transform reads off the block. A `transforms` entry produces a
# builder param, not a spec key, so the consumed spec key is invisible to lambda
# introspection and must be declared here (issue #787, point 2). Each listed key
# is deliberately absent from the kind's `map` (that is why it needs declaring).
TRANSFORM_CONSUMED_KEYS: dict[str, frozenset[str]] = {
    "attribute": frozenset({"options"}),
    "optionset": frozenset({"options"}),
    "entity": frozenset({"primary_attr"}),
    "view": frozenset({"columns"}),
}

# Per covered kind, the adapter-surface spec keys this module's projectors emit. An
# entry means the key CAN be emitted (most are conditional on the source metadata),
# not that every export carries it.
EXPORTED_KEYS: dict[str, frozenset[str]] = {
    "attribute": frozenset({
        "kind", "schema_name", "display_name", "description", "required",
        "max_length", "format_name", "auto_number_format", "min_value",
        "max_value", "behavior_name", "max_size_kb", "precision", "target_entity",
        "options", "optionset_name", "source_type", "formula_definition",
    }),
    "entity": frozenset({
        "schema_name", "display_name", "display_collection_name", "description",
        "ownership", "has_notes", "has_activities", "primary_attr",
        "primary_attr_max_length",
    }),
    "relationship": frozenset({
        "schema_name", "referenced_entity", "referencing_entity", "lookup_schema",
        "lookup_display", "required", "lookup_description", "cascade_assign",
        "cascade_delete", "cascade_reparent", "cascade_share", "cascade_unshare",
        "cascade_merge", "menu_behavior", "menu_label", "menu_order",
        "is_hierarchical",
    }),
    "view": frozenset({
        "name", "columns", "is_default", "description", "order_by", "order_desc",
        "filter_active",
    }),
    "optionset": frozenset({"name", "display_name", "description", "options"}),
    "webresource": frozenset({"name", "display_name"}),
    "security-role": frozenset({"name", "business_unit"}),
}

# Per covered kind, adapter-surface spec keys deliberately NOT emitted → reason.
# Migrated from this module's former docstring prose; disjoint from EXPORTED_KEYS
# and together exhausting each kind's surface (enforced by the contract test).
EXPORT_GAPS: dict[str, dict[str, str]] = {
    "attribute": {
        "default_value": "lives under the OptionSet, which does not ride the "
                         "un-cast attribute read; projecting it would need an "
                         "extra per-column cast read.",
        "true_label": "boolean label under the OptionSet; see default_value.",
        "false_label": "boolean label under the OptionSet; see default_value.",
        "relationship_schema": "niche and not round-trip-relevant for the custom "
                               "tables this exporter targets.",
        "is_audit_enabled": "audit-enable state is a runtime setting the projectors "
                            "do not read; not part of the customization round-trip.",
    },
    "entity": {
        "is_activity": "niche and not round-trip-relevant for the custom tables "
                       "this exporter targets.",
        "data_provider_id": "virtual-table field; virtual tables are read-only on "
                            "v9.1 and out of this exporter's scope.",
        "data_source_id": "virtual-table field; see data_provider_id.",
        "external_name": "virtual-table field; see data_provider_id.",
        "external_collection_name": "virtual-table field; see data_provider_id.",
        "is_audit_enabled": "audit-enable state is a runtime setting the projectors "
                            "do not read; not part of the customization round-trip.",
    },
    "relationship": {
        "is_audit_enabled": "audit-enable state is a runtime setting the projectors "
                            "do not read; not part of the customization round-trip.",
    },
    "view": {
        "query_type": "the read filters to public views (querytype eq 0), so "
                      "query_type is always the apply adapter's public default.",
    },
    "optionset": {},
    "webresource": {},
    "security-role": {},
}

# Whole registry kinds the exporter cannot project from a live org → reason. The
# plug-in kinds hinge on the assembly whose DLL bytes are absent from live metadata
# (ADR 0019), so no live read can reconstruct them.
KINDS_NOT_EXPORTED: dict[str, str] = {
    "plugin-assembly": "assembly DLL bytes do not exist in live org metadata "
                       "(ADR 0019); not projectable from a live read.",
    "plugin-step": "hinges on its plug-in assembly, which is not projectable from "
                   "a live read (ADR 0019).",
}


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


def _source_type_name(attr: dict[str, Any]) -> str | None:
    """Map a live `SourceType` int to apply's `source_type` value, or None.

    `SourceType` lives on the base AttributeMetadata, so it rides on the un-cast
    attribute read. Only 1 (calculated) / 2 (rollup) — the specialized columns
    `add_attribute` can re-create — map to a value; 0/None (simple) and 3/4
    (formula/prompt, which apply cannot create) return None and export as simple.
    """
    raw = attr.get("SourceType")
    return _SOURCE_TYPES.get(raw) if isinstance(raw, int) else None


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
    entry: dict[str, Any] = {
        "name": name,
        "display_name": display or name,
        "options": metadata.flatten_options(raw),
    }
    # apply applies the option set description on create (create_optionset); the
    # reconcile path updates only options, so emit it (when non-empty) so the
    # description round-trips into a freshly seeded org (#701).
    description = metadata.label_text(_as_dict(raw.get("Description")))
    if description:
        entry["description"] = description
    accumulator[name] = entry


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
        auto = info.get("AutoNumberFormat")
        if isinstance(auto, str) and auto:
            out["auto_number_format"] = auto

    if kind in _INT_BOUND_DEFAULTS:
        lo_default, hi_default = _INT_BOUND_DEFAULTS[kind]
        lo = info.get("MinValue")
        if isinstance(lo, int) and lo != lo_default:
            out["min_value"] = lo
        hi = info.get("MaxValue")
        if isinstance(hi, int) and hi != hi_default:
            out["max_value"] = hi

    if kind == "datetime":
        behavior = _as_dict(info.get("DateTimeBehavior")).get("Value")
        if (isinstance(behavior, str) and behavior
                and behavior != _DATETIME_DEFAULT_BEHAVIOR):
            out["behavior_name"] = behavior

    if kind == "file":
        size = info.get("MaxSizeInKB")
        if isinstance(size, int) and size != _FILE_DEFAULT_MAX_KB:
            out["max_size_kb"] = size

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

    # Calculated/rollup column: capture source_type + the live FormulaDefinition
    # (both ride on the un-cast read) so apply can re-create it (#554). A formula
    # we cannot read leaves the column exported as simple rather than dropped.
    source_type = _source_type_name(info)
    if source_type is not None:
        formula = info.get("FormulaDefinition")
        if isinstance(formula, str) and formula:
            out["source_type"] = source_type
            out["formula_definition"] = formula
        else:
            warnings.append(
                f"dropped {source_type} formula for column {attr_logical!r}: no "
                "readable FormulaDefinition; exported as a simple column"
            )

    return out


def build_entity_spec(
    backend: D365Backend,
    logical_name: str,
    *,
    with_views: bool = False,
    with_relationships: bool = False,
    with_forms: bool = False,
    solution: str | None = None,
    warnings: list[str] | None = None,
    skipped: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project a live entity into an apply-consumable desired-state spec dict.

    Reads `logical_name` over the Web API (pure GETs) and returns
    ``{"entities": [<entity>]}``, adding a top-level ``"optionsets": [...]`` key
    when one or more attributes bind a global option set.

    Only custom, apply-creatable attributes are emitted; the primary name
    attribute (carried as the entity's `primary_attr`) and system attributes are
    excluded. A publisher is never emitted — an existing entity does not know its
    publisher. A top-level ``"solution": {"unique_name": …}`` block is emitted
    only when `solution` is passed (via `metadata export-spec --solution`); `crm
    apply` requires one, so a spec exported without it is a valid but
    non-appliable document until a solution block is added.

    Args:
        with_views: When True, attach the entity's public views (via
            `views.read_entity_views`); the key is omitted when there are none.
        with_relationships: When True, attach the entity's custom 1:N
            relationships (via `relationships.read_entity_relationships`); the
            key is omitted when there are none.
        with_forms: When True, attach the entity's seedable main form as a
            `forms:` block (via `forms.project_entity_forms`), subject to the ADR
            0019 seedable invariant; the key is omitted when nothing custom
            projects. A non-seedable form (an additional main form) is recorded in
            `skipped`; a non-seedable component is recorded in `warnings`.
        warnings: Optional list to accumulate structured drop-reason strings.
            Each silently skipped attribute appends one entry. Callers that
            want to surface diagnostics pass an empty list here; callers that
            don't care may omit the argument.
        skipped: Optional list to accumulate non-seedable whole-form entries
            (`{type, objectid, reason}`, matching `build_solution_spec`'s bucket)
            when `with_forms` is set. Callers wanting form-skip diagnostics pass a
            list; when omitted, form skips are not surfaced.
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
    # apply reconciles the entity description; emit it (when non-empty) so
    # documentation metadata round-trips (#701).
    description = metadata.label_text(_as_dict(ent.get("Description")))
    if description:
        entity["description"] = description
    ownership = ent.get("OwnershipType")
    if isinstance(ownership, str) and ownership:
        entity["ownership"] = ownership
    # has_notes / has_activities: emit only when enabled (create_entity default
    # for both is False, so an entity without them adds no keys).
    if ent.get("HasNotes") is True:
        entity["has_notes"] = True
    if ent.get("HasActivities") is True:
        entity["has_activities"] = True

    optionset_acc: dict[str, dict[str, Any]] = {}

    # Enumerate attributes (shallow), keep only custom ones, deep-read each kept.
    shallow = metadata.list_attributes(backend, logical_name)
    attributes: list[dict[str, Any]] = []
    # Logical name -> AttributeType for each admitted custom column, so the forms
    # projector can both recognize a form field as custom (platform defaults are
    # omitted) and prove its control has a seedable classid.
    custom_attr_types: dict[str, str] = {}
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
        #
        # Calculated/rollup columns are read-only, so IsValidForCreate is false
        # too — but apply CAN re-create them (add_attribute --type), so a custom
        # specialized column (SourceType 1/2) is admitted despite the flag (#554).
        is_specialized = shallow_attr.get("SourceType") in (1, 2)
        if not is_primary and (
            not shallow_attr.get("IsCustomAttribute")
            or (not shallow_attr.get("IsValidForCreate") and not is_specialized)
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
            # primary_attr_max_length is a top-level entity kwarg (not nested in
            # primary_attr); emit only when it differs from the create_entity
            # default of 200.
            p_max = info.get("MaxLength")
            if isinstance(p_max, int) and p_max != 200:
                entity["primary_attr_max_length"] = p_max
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
            custom_attr_types[attr_logical] = str(info.get("AttributeType") or "")

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

    if with_forms:
        form_blocks = forms.project_entity_forms(
            backend, logical_name,
            custom_attr_types=custom_attr_types,
            warnings=warn,
            skipped=skipped if skipped is not None else [],
        )
        if form_blocks:
            entity["forms"] = form_blocks

    spec: dict[str, Any] = {}
    if solution:
        spec["solution"] = {"unique_name": solution}
    spec["entities"] = [entity]
    if optionset_acc:
        spec["optionsets"] = list(optionset_acc.values())
    return spec


# ── solution-level projection (#613) ─────────────────────────────────────────

# Solution component-type codes (see solution_components.SOLUTION_COMPONENT_TYPES).
# Only `entity` drives projection: each touched entity is projected in full via
# build_entity_spec, which subsumes that entity's attributes, views, 1:N
# relationships and referenced global option sets.
_ENTITY_COMPONENT_TYPE = 1
# Entity-rooted subcomponents (attribute / relationship / optionset /
# entityrelationship / savedquery=view): ride along inside their parent entity's
# full projection. A single column is never projected a la carte — see ADR 0019.
_ENTITY_SUBCOMPONENT_TYPES = frozenset({2, 3, 9, 10, 26})
# Plug-in component types (plugintype / pluginassembly / sdkmessageprocessingstep):
# all hinge on the assembly whose DLL bytes do not exist in live metadata.
_PLUGIN_COMPONENT_TYPES = frozenset({90, 91, 92})
# Apply-seedable non-entity members projected in full by their own projector below.
_ROLE_COMPONENT_TYPE = 20
_WEBRESOURCE_COMPONENT_TYPE = 61
# Privilege depths apply can author via set-role-privileges selectors. A live
# privilege at any other depth (e.g. RecordFilter, which has no authoring
# counterpart) cannot round-trip, so build_role_spec drops it with a warning.
_AUTHORABLE_DEPTHS: tuple[str, ...] = ("Basic", "Local", "Deep", "Global")


def _resolve_entity_logical(backend: D365Backend, metadata_id: str) -> str:
    """Resolve an entity MetadataId (a solution member's objectid) to its logical
    name — the key build_entity_spec projects on. One EntityDefinitions GET."""
    rec = as_dict(backend.get(
        f"EntityDefinitions({metadata_id})", params={"$select": "LogicalName"}))
    name = rec.get("LogicalName")
    if not isinstance(name, str) or not name:
        raise D365Error(f"entity metadata id {metadata_id!r} has no LogicalName.")
    return name


def build_webresource_spec(
    backend: D365Backend,
    webresource_id: str,
    *,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Project a web resource (a solution member's objectid) into an apply spec entry.

    Reads the web resource by id over the Web API (one GET) and emits the apply
    ``webresources`` shape with the body carried inline as base64 ``content`` —
    drifted JS/HTML/CSS is the whole point of a web-resource diff, so the content
    travels in the spec rather than as a sidecar file. ``display_name`` and
    ``webresourcetype`` round-trip too. The result passes `apply.validate_spec` and
    round-trips through `apply_spec` (apply reads inline content, see
    ``_webresource_content``).

    ``warnings`` is accepted for uniform dispatch with `build_role_spec` (see
    ``build_solution_spec._project``); a web resource has nothing to drop.
    """
    rec = as_dict(backend.get(
        f"webresourceset({webresource_id})",
        params={"$select": "name,displayname,webresourcetype,content"}))
    name = rec.get("name")
    if not isinstance(name, str) or not name:
        raise D365Error(f"web resource {webresource_id!r} has no name; cannot project.")
    spec: dict[str, Any] = {
        "name": name,
        "content": str(rec.get("content") or ""),
        "webresourcetype": rec.get("webresourcetype"),
    }
    display = rec.get("displayname")
    if isinstance(display, str) and display:
        spec["display_name"] = display
    return spec


def build_role_spec(
    backend: D365Backend,
    role_id: str,
    *,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Project a security role (a solution member's objectid) into an apply spec entry.

    Reads the role and its privileges over the Web API and emits the apply
    ``security_roles`` shape: ``{name, business_unit?, privileges}``. The role's
    live privileges (RetrieveRolePrivilegesRole) are grouped by depth into
    ``privilege_names`` selector rows — the portable, org-independent form apply
    resolves back (privilege *ids* differ per org; names do not).

    Only the four authorable depths (Basic/Local/Deep/Global) round-trip; a live
    privilege at any other depth (e.g. RecordFilter) has no authoring counterpart
    and is dropped with a warning. A role that projects to zero authorable
    privilege rows raises D365Error (apply rejects an empty privilege set), so the
    caller routes it to the `skipped` bucket.

    Note: ``business_unit`` is the source org's BU id (apply's role spec is
    GUID-keyed), so it round-trips within the same org; for a cross-org apply the
    operator drops it (apply then defaults the role to the target's own BU). And
    on-prem RetrieveRolePrivilegesRole omits PrivilegeName (see
    `security.get_role_privileges`), so an on-prem source emits privilege ids as
    names; those round-trip back to the *same* org but not by-name to another
    on-prem org. Cloud sources emit real names. (A future slice could resolve ids
    to names via the privileges catalog.)
    """
    from crm.core import security as sec_mod  # lazy import: avoid an import cycle

    warn = warnings if warnings is not None else []
    rec = as_dict(backend.get(
        f"roles({role_id})", params={"$select": "name,_businessunitid_value"}))
    name = rec.get("name")
    if not isinstance(name, str) or not name:
        raise D365Error(f"security role {role_id!r} has no name; cannot project.")

    privileges = sec_mod.get_role_privileges(backend, role_id)
    by_depth: dict[str, list[str]] = {}
    for priv in privileges:
        depth = str(priv.get("depth"))
        if depth not in _AUTHORABLE_DEPTHS:
            warn.append(
                f"role {name!r}: privilege {priv.get('name')!r} at non-authorable "
                f"depth {depth!r} dropped (cannot round-trip through apply).")
            continue
        by_depth.setdefault(depth, []).append(str(priv.get("name")))

    if not by_depth:
        raise D365Error(
            f"security role {name!r} has no apply-authorable privileges "
            "(Basic/Local/Deep/Global); nothing to project.")

    # Emit one selector row per depth, depths in canonical order, names sorted.
    rows = [{"depth": depth, "privilege_names": sorted(by_depth[depth])}
            for depth in _AUTHORABLE_DEPTHS if depth in by_depth]
    spec: dict[str, Any] = {"name": name, "privileges": rows}
    bu = rec.get("_businessunitid_value")
    if isinstance(bu, str) and bu:
        spec["business_unit"] = bu
    return spec


# ── model-driven app projection (#797, ADR 0024) ─────────────────────────────
#
# A model-driven app is a top-level, non-entity-rooted solution member the entity
# walk cannot reach; ADR 0024 fixes a separate pass over `appmodule` members that
# projects each app under a top-level `apps:` block, under the same ADR 0019
# seedable invariant. Only the app identity and its Entity-backed sitemap are
# projected — both re-seed on a fresh org (the app is created by unique name; the
# sitemap references tables by portable logical name).
_APP_COMPONENT_TYPE = 80
# The app's sitemap rides under its own `apps:` block (projected with the app), so
# a standalone SiteMap solution member is reported skipped, not projected twice.
_SITEMAP_COMPONENT_TYPE = 62
# appmodulecomponent componenttypes the projection already accounts for WITHOUT a
# warning: an entity (1) reaches the app through the sitemap's Entity subareas, and
# the sitemap (62) is projected as the app's `sitemap:` block. Every other binding
# is a record-backed component (view / chart / form / dashboard / BPF) bound by an
# org-specific id that apply's create path cannot re-seed on a fresh org.
_APP_BINDINGS_ACCOUNTED_FOR = frozenset({1, 62})


def _project_app_sitemap(
    sitemap_xml: str, app_label: str, warnings: list[str],
) -> dict[str, Any] | None:
    """Project a live SiteMapXml into apply's nested ``sitemap:`` block, or None.

    Keeps only Entity-backed SubAreas — a table's logical name is portable across
    orgs, so it round-trips through apply's create path (``build_sitemapxml`` emits
    only ``Entity=`` SubAreas). A Url / DefaultDashboard SubArea binds to an
    org-specific target apply cannot re-seed, so it is dropped with a ``warnings``
    reason (never silently — ADR 0019). A Group left with no seedable SubArea, and
    an Area left with no Group, is pruned; when nothing seedable remains the result
    is None so the caller omits the ``sitemap:`` key.

    An Area / Group ``title`` is emitted only when it differs from the node Id (the
    create path defaults a blank title to the Id, so an equal title is not drift);
    a SubArea ``title`` is emitted whenever present.
    """
    root = xml_edit.parse_xml(sitemap_xml, label="SiteMapXml")
    out_areas: list[dict[str, Any]] = []
    for area in root.findall("Area"):
        aid = area.get("Id")
        if not aid:
            continue
        out_groups: list[dict[str, Any]] = []
        for group in area.findall("Group"):
            gid = group.get("Id")
            if not gid:
                continue
            out_subs: list[dict[str, Any]] = []
            for sub in group.findall("SubArea"):
                entity = sub.get("Entity")
                if not entity:
                    ident = (sub.get("Id") or sub.get("Url")
                             or sub.get("DefaultDashboard") or "?")
                    warnings.append(
                        f"{app_label} sitemap: dropped subarea {ident!r} — only "
                        "entity-backed subareas re-seed; a url/dashboard subarea "
                        "binds to an org-specific target apply cannot re-create.")
                    continue
                sub_out: dict[str, Any] = {"entity": entity}
                title = sub.get("Title")
                if title:
                    sub_out["title"] = title
                out_subs.append(sub_out)
            if not out_subs:
                continue
            group_out: dict[str, Any] = {"id": gid, "subareas": out_subs}
            gtitle = group.get("Title")
            if gtitle and gtitle != gid:
                group_out["title"] = gtitle
            out_groups.append(group_out)
        if not out_groups:
            continue
        area_out: dict[str, Any] = {"id": aid, "groups": out_groups}
        atitle = area.get("Title")
        if atitle and atitle != aid:
            area_out["title"] = atitle
        out_areas.append(area_out)
    return {"areas": out_areas} if out_areas else None


def build_app_spec(
    backend: D365Backend,
    app_id: str,
    *,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Project a model-driven app (a solution member's objectid) into an ``apps`` entry.

    Reads the appmodule and its app-specific sitemap over the Web API (pure GETs)
    and emits the apply ``apps`` shape — ``{name, unique_name, description?,
    sitemap?}`` — the create path (ADR 0024, #795) consumes. Only the app identity
    and its Entity-backed sitemap are projected: both re-seed on a fresh org (the
    app is created by unique name; the sitemap references tables by portable
    logical name). The result passes ``apply.validate_spec`` and round-trips
    through ``apply_spec``.

    Record-backed component bindings (views / charts / forms / dashboards / BPFs)
    are NOT projected — apply's create ``components:`` block binds them by
    org-specific id, which cannot re-seed on another org (ADR 0019). Their count is
    surfaced in ``warnings`` rather than dropped silently; tables still reach the
    app through the sitemap's Entity subareas. Raises ``D365Error`` (routed to the
    caller's ``skipped`` bucket) when the app has no readable name / unique name.
    """
    warn = warnings if warnings is not None else []
    rec = as_dict(backend.get(
        f"appmodules({app_id})",
        params={"$select": "name,uniquename,description,appmoduleidunique"}))
    name = rec.get("name")
    unique_name = rec.get("uniquename")
    if not isinstance(name, str) or not name:
        raise D365Error(f"app {app_id!r} has no name; cannot project.")
    if not isinstance(unique_name, str) or not unique_name:
        raise D365Error(f"app {app_id!r} has no unique name; cannot project.")
    # Seedable invariant (ADR 0019): apply's create path (create_app) requires a
    # publisher-prefixed unique name, so an app whose unique name lacks one (e.g. a
    # first-party app like `Customerservicehub`) cannot round-trip — skip it with a
    # reason rather than emit a block validate_spec would reject.
    try:
        mc.validate_schema_name(unique_name, subject="unique_name",
                                example="cwx_crmworx")
    except D365Error as exc:
        raise D365Error(
            f"app {unique_name!r} is not apply-seedable: {exc}") from exc

    app_label = f"app {unique_name!r}"
    block: dict[str, Any] = {"name": name, "unique_name": unique_name}
    description = rec.get("description")
    if isinstance(description, str) and description:
        block["description"] = description

    # The app's sitemap is linked by sitemapnameunique == the app's uniquename
    # (the inverse of appmodule.set_sitemap). Project its Entity subareas.
    sitemaps = backend.get_collection(
        "sitemaps",
        params={"$filter": f"sitemapnameunique eq {odata_literal(unique_name)}",
                "$select": "sitemapxml"})
    sitemap_xml = str(sitemaps[0].get("sitemapxml") or "") if sitemaps else ""
    if sitemap_xml.strip():
        sitemap = _project_app_sitemap(sitemap_xml, app_label, warn)
        if sitemap is not None:
            block["sitemap"] = sitemap
    else:
        warn.append(
            f"{app_label}: no app-specific sitemap found "
            f"(sitemapnameunique {unique_name!r}); navigation not projected.")

    # Record-backed component bindings bind by org-specific id (ADR 0019): count
    # them so the omission is visible, never silent. Best-effort — a failure to
    # read the bindings must not fail the whole app projection.
    idunique = rec.get("appmoduleidunique")
    if isinstance(idunique, str) and idunique:
        try:
            comps = backend.get_collection(
                "appmodulecomponents",
                params={"$filter": f"_appmoduleidunique_value eq {idunique}",
                        "$select": "componenttype"})
        except D365Error:
            comps = []
        bound = sum(1 for c in comps
                    if c.get("componenttype") not in _APP_BINDINGS_ACCOUNTED_FOR)
        if bound:
            warn.append(
                f"{app_label}: {bound} record-backed component binding(s) "
                "(views/charts/forms/dashboards/BPFs) not projected — they bind "
                "by org-specific id and cannot re-seed on a fresh org (ADR 0019); "
                "tables reach the app through the sitemap's entity subareas.")

    return block


def _skip_reason(componenttype: int) -> str:
    """Explain why a non-entity solution member is not directly projected."""
    if componenttype in _PLUGIN_COMPONENT_TYPES:
        return ("plug-in component not projectable from a live org (its assembly "
                "DLL bytes are absent from live metadata); export emits only "
                "apply-seedable components.")
    if componenttype in _ENTITY_SUBCOMPONENT_TYPES:
        return ("entity-rooted subcomponent: never projected individually and "
                "not resolved to its parent entity (known simplification), so it "
                "is always reported here; its data is still exported when that "
                "parent entity is itself a solution member (projected in full).")
    return (f"{solution_components.component_type_name(componenttype)} is not an "
            "apply-seedable component type; export emits the apply-seedable kinds "
            "(entities, security roles, web resources).")


def build_solution_spec(
    backend: D365Backend,
    unique_name: str,
    *,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Project a whole solution into one apply-consumable desired-state spec.

    Walks `unique_name`'s members (pure GETs, read-only) and merges every entity
    the solution touches into a single spec via `build_entity_spec` per entity —
    each entity is projected in full (attributes, views, 1:N relationships, its
    seedable main form, and referenced global option sets), so an entity-rooted
    subcomponent member rides along inside its parent entity. Entity members carry ``objectid =
    MetadataId``; each is resolved to a logical name before projection. Global
    option sets referenced by more than one entity are de-duplicated by name.

    The emitted spec carries a top-level ``{"solution": {"unique_name": ...}}``
    key so a round-trip ``crm --dry-run apply -f <file>`` against another org
    auto-scopes its drift/prune report to this solution.

    Security-role members project under ``security_roles`` (name, optional
    business unit, privileges grouped by depth) and web-resource members under
    ``webresources`` (inline base64 content, display name, type) — both
    apply-seedable, so they round-trip a real apply. Model-driven app members
    (``appmodule``) project under a top-level ``apps`` block — the app identity and
    its Entity-backed sitemap, the seedable slice (ADR 0024, #797); a standalone
    ``sitemap`` member is reported skipped because it rides under its app. Every
    member that is not an apply-seedable kind is reported in a `skipped` bucket
    (``{type, objectid, reason}``) — the verb never fails on an unsupported
    component and never drops one silently. Plug-ins (assembly DLL bytes absent
    from live metadata) and other non-seedable kinds remain skipped (see ADR 0019).
    A touched entity's seedable main form rides along under its `forms:` block (ADR
    0024); a non-seedable form (an additional main form) joins the same `skipped`
    bucket. An app's record-backed component bindings (views / forms / charts /
    BPFs) are not projected (they bind by org-specific id) and are surfaced in
    `warnings`.

    Returns ``{"spec": <apply-ready spec>, "skipped": [...]}``. `spec` passes
    `apply.validate_spec` and round-trips through `apply_spec`.
    """
    from crm.core import solution as sol_mod  # lazy import: avoid an import cycle

    warn = warnings if warnings is not None else []
    members = sol_mod.solution_components(backend, unique_name)

    skipped: list[dict[str, Any]] = []
    entity_logicals: list[str] = []
    roles: list[dict[str, Any]] = []
    webresources: list[dict[str, Any]] = []
    apps: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _project(kind: str, objectid: Any,
                 projector: Callable[..., dict[str, Any]],
                 acc: list[dict[str, Any]]) -> None:
        """Project one objectid-keyed member, routing any failure to `skipped`."""
        if not isinstance(objectid, str) or not objectid:
            skipped.append({"type": kind, "objectid": objectid,
                            "reason": f"{kind} member has no objectid; cannot project."})
            return
        try:
            acc.append(projector(backend, objectid, warnings=warn))
        except D365Error as exc:
            skipped.append({"type": kind, "objectid": objectid,
                            "reason": f"could not project {kind}: {exc}"})

    for member in members:
        componenttype = member.get("componenttype")
        objectid = member.get("objectid")
        if not isinstance(componenttype, int):
            # Defensive: a well-formed solutioncomponents row always carries an
            # int componenttype. Surface anything malformed rather than drop it
            # silently (the never-drop-silently invariant, ADR 0019).
            skipped.append({
                "type": str(componenttype),
                "objectid": objectid,
                "reason": "solution member has a non-integer componenttype; "
                          "not projectable.",
            })
            continue
        if componenttype == _ENTITY_COMPONENT_TYPE:
            if not isinstance(objectid, str) or not objectid:
                skipped.append({
                    "type": "entity", "objectid": objectid,
                    "reason": "entity member has no objectid; cannot resolve.",
                })
                continue
            try:
                logical = _resolve_entity_logical(backend, objectid)
            except D365Error as exc:
                skipped.append({
                    "type": "entity", "objectid": objectid,
                    "reason": f"could not resolve entity metadata id: {exc}",
                })
                continue
            if logical not in seen:
                seen.add(logical)
                entity_logicals.append(logical)
        elif componenttype == _ROLE_COMPONENT_TYPE:
            _project("role", objectid, build_role_spec, roles)
        elif componenttype == _WEBRESOURCE_COMPONENT_TYPE:
            _project("webresource", objectid, build_webresource_spec, webresources)
        elif componenttype == _APP_COMPONENT_TYPE:
            _project("app", objectid, build_app_spec, apps)
        elif componenttype == _SITEMAP_COMPONENT_TYPE:
            # The sitemap is projected under its app's `apps:` block (ADR 0024), so
            # a standalone SiteMap member is reported here rather than emitted twice.
            skipped.append({
                "type": "sitemap", "objectid": objectid,
                "reason": ("sitemap rides under its app's apps: block (projected "
                           "there when its app is a solution member), not as a "
                           "standalone component (ADR 0024).")})
        else:
            skipped.append({
                "type": solution_components.component_type_name(componenttype),
                "objectid": objectid,
                "reason": _skip_reason(componenttype),
            })

    entity_logicals.sort()
    entities: list[dict[str, Any]] = []
    optionset_acc: dict[str, dict[str, Any]] = {}
    for logical in entity_logicals:
        es = build_entity_spec(
            backend, logical,
            with_views=True, with_relationships=True, with_forms=True,
            warnings=warn, skipped=skipped,
        )
        entities.extend(cast("list[dict[str, Any]]", es.get("entities", [])))
        for opt_set in cast("list[dict[str, Any]]", es.get("optionsets", [])):
            name = opt_set.get("name")
            if isinstance(name, str):
                optionset_acc.setdefault(name, opt_set)

    spec: dict[str, Any] = {
        "solution": {"unique_name": unique_name},
        "entities": entities,
    }
    if optionset_acc:
        spec["optionsets"] = list(optionset_acc.values())
    if webresources:
        spec["webresources"] = webresources
    if roles:
        spec["security_roles"] = roles
    if apps:
        spec["apps"] = apps
    return {"spec": spec, "skipped": skipped}
