"""Declarative desired-state apply: orchestrate the metadata cores from a spec.

`apply_spec` reads a parsed spec (publisher / solution / optionsets / entities
with attributes / relationships / views) and drives the existing per-resource
cores in dependency order, each with if_exists='skip' and the spec's solution,
forcing stage-only and calling publish_all ONCE at the end. Every step is
classified into applied / skipped / planned / failed.

Metadata POSTs are not transactional, so the first failure aborts the remaining
steps and is reported; whatever was already created stays staged-but-unpublished
(meta.staged is true) for the operator to clean up or re-apply.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from crm.core.batch import run_batched
from crm.core import metadata as meta_mod
from crm.core import metadata_attrs as attrs_mod
from crm.core.metadata_attrs import ATTRIBUTE_KINDS
from crm.core import metadata_constraints as mc
from crm.core import metadata_update as meta_update_mod
from crm.core import optionsets as os_mod
from crm.core import plugin as plugin_mod
from crm.core import relationships as rel_mod
from crm.core import security as sec_mod
from crm.core import solution as sol_mod
from crm.core import views as views_mod
from crm.core import webresource as wr_mod
from crm.utils.d365_backend import D365Backend, D365Error, as_dict, odata_literal
from crm.utils.d365_types import BatchOperation

Entry = dict[str, Any]


class _Aborted(Exception):
    """Internal signal: a metadata POST failed; stop applying the rest of the spec."""


def _as_list(value: Any) -> list[dict[str, Any]]:
    """Coerce a spec sub-collection to a list of dicts (empty when absent)."""
    return cast("list[dict[str, Any]]", value) if isinstance(value, list) else []


def _columns(value: Any) -> list[tuple[str, int]]:
    """Normalize a view's columns spec ('name' or {name,width}) to (name, width) tuples."""
    cols: list[tuple[str, int]] = []
    items = cast("list[Any]", value) if isinstance(value, list) else []
    for col in items:
        if isinstance(col, dict):
            cd = cast("dict[str, Any]", col)
            cols.append((str(cd["name"]), int(cd.get("width", 100))))
        else:
            cols.append((str(col), 100))
    return cols


def _resolve_otc(backend: D365Backend, logical: str) -> int | None:
    """Forced-real GET of an entity's ObjectTypeCode; None if not yet assigned/readable.

    A brand-new custom table's OTC is often unreadable until the apply's final
    publish, so the caller reports its views as `planned` and a second apply
    lands them. Only a 404 (entity not yet created) maps to None — any other error
    (401/403/5xx) is re-raised so real failures are not silently masked.
    """
    try:
        rb = as_dict(backend.get(
            f"EntityDefinitions(LogicalName='{logical}')",
            params={"$select": "ObjectTypeCode"}))
    except D365Error as exc:
        if exc.status == 404:
            return None
        raise
    otc = rb.get("ObjectTypeCode")
    return otc if isinstance(otc, int) and otc > 0 else None


def _require(obj: Any, keys: tuple[str, ...], label: str) -> None:
    """Raise a clear D365Error if `obj` is not a mapping or misses a required key."""
    if not isinstance(obj, dict):
        raise D365Error(f"{label} must be a mapping.")
    cobj = cast("dict[str, Any]", obj)
    for key in keys:
        if not cobj.get(key):
            raise D365Error(f"{label}: missing required field {key!r}.")


def _require_list(parent: dict[str, Any], key: str, label: str) -> None:
    """If `key` is present on `parent`, require it to be a list."""
    if key in parent and not isinstance(parent[key], list):
        raise D365Error(f"{label}: {key} must be a list.")


def _validate_option(opt: Any, label: str) -> None:
    """An option is a {label, value?} mapping with an integer value when present."""
    if not isinstance(opt, dict):
        raise D365Error(f"{label}: each option must be a mapping.")
    od = cast("dict[str, Any]", opt)
    if not od.get("label"):
        raise D365Error(f"{label}: each option needs a label.")
    if od.get("value") is not None and not isinstance(od["value"], int):
        raise D365Error(f"{label}: option value must be an integer.")


def _validate_column(col: Any, view_name: str) -> None:
    """A view column is a non-empty string or a {name[, width:int]} mapping."""
    if isinstance(col, str):
        if not col:
            raise D365Error(f"view {view_name!r}: column name must not be empty.")
        return
    if isinstance(col, dict):
        cd = cast("dict[str, Any]", col)
        if not isinstance(cd.get("name"), str) or not cd["name"]:
            raise D365Error(f"view {view_name!r}: each column needs a non-empty string name.")
        if "width" in cd and not isinstance(cd["width"], int):
            raise D365Error(f"view {view_name!r}: column width must be an integer.")
        return
    raise D365Error(f"view {view_name!r}: column must be a string or a mapping.")


# ── component-kind adapters ──────────────────────────────────────────────────
@dataclass(frozen=True)
class ReconcileCtx:
    """The capped context a component-kind adapter's ``find_live`` / ``reconcile``
    receive — deliberately small so per-kind knowledge is derived from the block,
    not smuggled through a grab-bag.

    Carries exactly the four cross-kind values the driver owns: the target
    ``solution``, the spec file's ``base_dir`` (for the file-content reads a
    reconcile may need), and the entity-scope pair — the owning entity's
    ``entity_logical`` name and its ``object_type_code`` — for the nested kinds
    (attribute, view). Anything else a reconcile needs is read from its own block.
    """

    solution: str | None
    base_dir: str | None
    entity_logical: str | None = None
    object_type_code: int | None = None


@dataclass(frozen=True)
class Adapter:
    """The complete authority for one component kind behind a single interface.

    A spec block (a dict from the parsed spec) reaches its create builder through
    exactly this object, so the set of spec keys a kind accepts *is* the adapter's
    ``map`` / ``transforms``. The four-slot interface a kind implements:

      * ``validate(block)`` — the complete up-front authority for one block:
        required keys (``required_block_keys``), constrained values routed through
        the same :mod:`metadata_constraints` primitives the builder itself calls,
        and any cross-field rule via ``extra_validate``. A malformed block fails in
        the up-front pass instead of mid-apply (closing validate/apply drift).
      * ``to_kwargs(block)`` — the single generic projection onto the builder's
        keyword arguments.
      * ``find_live(backend, block, ctx)`` — read the live component this block
        reconciles against (``None`` when there is nothing to reconcile).
      * ``reconcile(backend, block, live, ctx, entry)`` — diff the block against
        ``live`` into an ADR 0014 verdict (bucket + payload).

    Adapters are *data*: the contract test reads the same ``map`` / ``transforms``
    / ``required_block_keys`` the runtime uses, so a builder kwarg (or a required
    param) added without an adapter entry turns the test red.

    Fields:
      map        spec_key → builder_param (an entry whose key differs from its
                 value is a rename, e.g. ``"required" → "lookup_required"``).
      injected   builder params the driver supplies, never the spec (``backend``,
                 ``entity``, ``object_type_code``, ``solution``, ``if_exists``,
                 ``publish``).
      transforms builder_param → ``f(block)`` for the few values that need
                 computing (option tuples, the nested ``primary_attr`` block,
                 view columns).
      defaults   spec_key → fallback when absent (mirrors the old
                 ``block.get(key, default)`` calls).
      schema_name_keys / required_keys / cascade_keys / menu_keys
                 spec keys whose value ``validate`` checks through ``mc.*``.
      required_block_keys / block_label
                 keys ``validate`` requires present on the block (via ``_require``,
                 reported under ``block_label``).
      extra_validate   optional cross-field block rule ``validate`` runs last.
      find_live / reconcile   the probe/diff callables (see the interface above).
    """

    map: dict[str, str]
    injected: frozenset[str]
    transforms: dict[str, Callable[[dict[str, Any]], Any]]
    defaults: dict[str, Any]
    schema_name_keys: tuple[str, ...] = ()
    required_keys: tuple[str, ...] = ()
    cascade_keys: tuple[str, ...] = ()
    menu_keys: tuple[str, ...] = ()
    required_block_keys: tuple[str, ...] = ()
    block_label: str = ""
    extra_validate: Callable[[dict[str, Any]], None] | None = None
    find_live: Callable[[D365Backend, dict[str, Any], ReconcileCtx], Any] | None = None
    reconcile: Callable[
        [D365Backend, dict[str, Any], Any, ReconcileCtx, Entry], "_Verdict"
    ] | None = None

    @property
    def transform_targets(self) -> frozenset[str]:
        """The builder params produced by ``transforms`` (consumed by the contract test)."""
        return frozenset(self.transforms)

    def to_kwargs(self, block: dict[str, Any]) -> dict[str, Any]:
        """Project a spec block onto its builder's keyword arguments.

        A mapped key absent from the block falls to ``defaults`` if declared, else
        is omitted so the builder's own default applies; transforms always run.
        """
        out: dict[str, Any] = {}
        for spec_key, param in self.map.items():
            if spec_key in block:
                out[param] = block[spec_key]
            elif spec_key in self.defaults:
                out[param] = self.defaults[spec_key]
        for param, fn in self.transforms.items():
            out[param] = fn(block)
        return out

    def validate(self, block: dict[str, Any]) -> None:
        """The complete up-front authority for one block.

        Required keys first (matching the old ``_require`` call order), then the
        constrained values routed through the builders' own ``mc.*`` rules, then
        the kind's cross-field rule. A malformed block fails here rather than
        mid-apply.
        """
        if self.required_block_keys:
            _require(block, self.required_block_keys, self.block_label)
        for key in self.schema_name_keys:
            if key in block:
                mc.validate_schema_name(block[key], subject=key)
        for key in self.required_keys:
            if key in block:
                mc.validate_required(block[key], subject=key)
        for key in self.cascade_keys:
            if key in block:
                mc.validate_cascade(block[key], subject=key)
        for key in self.menu_keys:
            if key in block:
                mc.validate_menu_behavior(block[key], subject=key)
        if self.extra_validate is not None:
            self.extra_validate(block)


def validate_spec(spec: Any) -> None:
    """Validate spec shape up front so a malformed file fails before any HTTP call."""
    if not isinstance(spec, dict):
        raise D365Error(
            "spec must be a mapping with publisher / solution / entities / optionsets.")
    sp = cast("dict[str, Any]", spec)
    if sp.get("publisher") is not None:
        _require(sp["publisher"], ("unique_name", "prefix", "option_value_prefix"), "publisher")
        if not isinstance(sp["publisher"]["option_value_prefix"], int):
            raise D365Error("publisher: option_value_prefix must be an integer "
                            "(10000-99999), not a quoted string.")
    # A customization write must target an explicit unmanaged solution: the
    # spec's top-level `solution:` block is mandatory (#636). A spec exported
    # without one (`metadata export-spec` sans --solution) is a valid document
    # but not appliable until a solution block is added.
    if sp.get("solution") is None:
        raise D365Error(
            "spec must declare a top-level 'solution:' block with 'unique_name' — "
            "customization writes must target an explicit unmanaged solution "
            "(re-export with `metadata export-spec --solution`).")
    _require(sp["solution"], ("unique_name",), "solution")
    for key in ("entities", "optionsets", "webresources", "security_roles", "plugins"):
        if key in sp and not isinstance(sp[key], list):
            raise D365Error(f"{key} must be a list.")
    for ent in _as_list(sp.get("entities")):
        # Each entity-subtree block's own rules (required keys, constrained values,
        # cross-field rules) are its component-kind adapter's complete authority
        # now — validate delegates the block to the adapter. Only document-structure
        # rules (top-level shape, the mandatory solution block, sub-collections-are-
        # lists) stay in this up-front pass.
        REGISTRY["entity"].validate(ent)
        elabel = f"entity {ent['schema_name']!r}"
        for sub in ("attributes", "relationships", "views"):
            _require_list(ent, sub, elabel)
        for attr in _as_list(ent.get("attributes")):
            REGISTRY["attribute"].validate(attr)
        for rel in _as_list(ent.get("relationships")):
            REGISTRY["relationship"].validate(rel)
        for view in _as_list(ent.get("views")):
            REGISTRY["view"].validate(view)
    # optionset / web resource / security role each delegate their whole block to
    # the component-kind adapter (required keys, constrained values, option/privilege
    # shape) — the same up-front authority the entity subtree uses. A malformed block
    # fails here, before any HTTP, instead of mid-apply.
    for os_spec in _as_list(sp.get("optionsets")):
        REGISTRY["optionset"].validate(os_spec)
    for wr in _as_list(sp.get("webresources")):
        REGISTRY["webresource"].validate(wr)
    for role in _as_list(sp.get("security_roles")):
        REGISTRY["security-role"].validate(role)
    # A plug-in is compound: the assembly block and each step block delegate their
    # whole rules to the component-kind adapter (required keys, field types, the
    # isolation/stage/mode vocabularies) — the same up-front authority the entity
    # subtree uses. Its plug-in types and a step's images are create-only
    # sub-collections with no adapter, so their document shape stays here.
    for plugin in _as_list(sp.get("plugins")):
        REGISTRY["plugin-assembly"].validate(plugin)
        plabel = f"plug-in {plugin.get('assembly') or plugin['file']!r}"
        for sub in ("types", "steps"):
            _require_list(plugin, sub, plabel)
        for typ in _as_list(plugin.get("types")):
            _require(typ, ("type_name",), f"{plabel} type")
            if not isinstance(typ["type_name"], str):
                raise D365Error(f"{plabel}: type type_name must be a string.")
        for step in _as_list(plugin.get("steps")):
            REGISTRY["plugin-step"].validate(step)
            slabel = f"{plabel} step {step['name']!r}"
            _require_list(step, "images", slabel)
            for img in _as_list(step.get("images")):
                _require(img, ("alias", "image_type"), f"{slabel} image")
                for key in ("alias", "image_type", "attributes", "name",
                            "message_property_name"):
                    if img.get(key) is not None and not isinstance(img[key], str):
                        raise D365Error(f"{slabel}: image {key!r} must be a string.")


def _solution_exists(backend: D365Backend, name: str) -> bool:
    """Forced-real existence check for a solution by uniquename (dry-run safe)."""
    rows = backend.get_collection(
        "solutions",
        params={"$filter": f"uniquename eq {odata_literal(name)}", "$select": "solutionid"},
    )
    return bool(rows)


def _classify(
    result: dict[str, Any],
    entry: Entry,
    applied: list[Entry],
    skipped: list[Entry],
    planned: list[Entry],
) -> str:
    """Sort a core result into the right bucket by its return keys; return the bucket."""
    if result.get("_dry_run"):
        if result.get("would_skip"):
            skipped.append(entry)
            return "skipped"
        planned.append(entry)
        return "planned"
    if result.get("skipped"):
        skipped.append(entry)
        return "skipped"
    applied.append(entry)
    return "applied"


def _present(result: dict[str, Any]) -> bool:
    """True when a create-builder reports the component already exists.

    A real apply short-circuits the POST and returns `skipped`; a dry-run leaves
    the POST suppressed and reports the forced-real existence probe via
    `would_skip`. Either way the component is live, so apply reconciles it against
    the spec rather than creating it — the same reconcile path runs in both modes
    (its writes are no-ops under dry-run per the reads-execute rule), which is what
    turns `--dry-run` into a full drift report (#550)."""
    return bool(result.get("skipped") or result.get("would_skip"))


_T = TypeVar("_T")


def _call(entry: Entry, fn: Callable[[], _T], failed: list[Entry]) -> _T:
    """Run a core call; on D365Error record a failed entry and signal abort."""
    try:
        return fn()
    except D365Error as exc:
        failed.append({**entry, "error": str(exc)})
        raise _Aborted from exc


# ── Convergent reconciliation ────────────────────────────────────────────────
#
# When a create-builder reports a component already exists (if_exists='skip'),
# apply does not blindly skip it: it reads the live definition, diffs it against
# the desired spec, and routes the component to one of three buckets — `skipped`
# (already matches; idempotent no-op), `updated` (in-place update of the divergent
# fields the platform allows — a retrieve-merge-write PUT or option-set action, not
# HTTP PATCH), or `replace_blocked` (an immutable/destructive
# divergence that would need a drop-and-recreate — reported, NO write). A reconcile
# returns `(bucket, entry)`; a D365Error during the read/update is a hard failure
# that aborts the run (same contract as `_call`). See ADR 0014.

_Verdict = tuple[str, Entry]


def _drift(desired: Any, live_label: Any) -> bool:
    """True when `desired` is set and differs from the live Label's text.

    A spec that omits a label field (desired is None) never reports drift — apply
    only reconciles fields the spec explicitly declares, so an unspecified field is
    left as-is rather than blanked.
    """
    if desired is None:
        return False
    current = (meta_mod.label_text(cast("dict[str, Any]", live_label))
               if isinstance(live_label, dict) else "")
    return str(desired) != current


def _with_diff(entry: Entry, update_result: dict[str, Any]) -> Entry:
    """Attach the field-level drift to an `updated` entry when one is available.

    The metadata update cores compute a before/after `diff` on their dry-run
    branch (the PUT/action body is suppressed); a real apply returns no diff. So
    under --dry-run the `updated` bucket carries what each component WOULD change,
    turning a list of names into an actual drift report; a real apply's entry is
    left unchanged."""
    diff = update_result.get("diff")
    return {**entry, "diff": diff} if diff else entry


def _reconcile(
    entry: Entry,
    thunk: Callable[[], _Verdict],
    failed: list[Entry],
    routes: dict[str, list[Entry]],
) -> None:
    """Run a reconcile thunk and route its verdict; D365Error → failed + abort."""
    try:
        bucket, payload = thunk()
    except D365Error as exc:
        failed.append({**entry, "error": str(exc)})
        raise _Aborted from exc
    routes[bucket].append(payload)


def _find_live_entity(
    backend: D365Backend, ent: dict[str, Any], ctx: ReconcileCtx,
) -> dict[str, Any]:
    """Read the live entity definition this block reconciles against."""
    return meta_mod.entity_info(
        backend, ctx.entity_logical or ent["schema_name"].lower())


def _reconcile_entity(
    backend: D365Backend, ent: dict[str, Any], live: dict[str, Any],
    ctx: ReconcileCtx, entry: Entry,
) -> _Verdict:
    """Diff an existing entity against the spec; update, skip, or block.

    Replace-blocked (immutable/destructive divergence, no write): an ownership
    change (Dataverse rejects OwnershipType edits post-create); an `is_activity`
    change (a regular table cannot become an activity table in place — identity);
    or an explicit request to DISABLE `has_notes`/`has_activities` (enable-only —
    the platform forbids disabling, so the only way to honour it is a destructive
    drop-and-recreate). Updatable: display name, display-collection name,
    description, and ENABLING `has_notes`/`has_activities` (`false -> true`, which
    the platform applies additively). Only spec-declared fields drift. See ADR 0018.
    """
    solution = ctx.solution
    logical = ctx.entity_logical or ent["schema_name"].lower()
    desired_ownership = ent.get("ownership")
    live_ownership = live.get("OwnershipType")
    if desired_ownership and live_ownership and desired_ownership != live_ownership:
        return "replace_blocked", {
            **entry,
            "reason": f"ownership change {live_ownership!r} -> {desired_ownership!r} "
                      "requires a destructive drop-and-recreate; refusing (no write).",
        }
    desired_is_activity = ent.get("is_activity")
    live_is_activity = live.get("IsActivity")
    if (desired_is_activity is not None and live_is_activity is not None
            and desired_is_activity != live_is_activity):
        return "replace_blocked", {
            **entry,
            "reason": f"is_activity change {live_is_activity!r} -> {desired_is_activity!r} "
                      "is an identity change (a table cannot be converted to/from an "
                      "activity table in place); refusing (no write).",
        }
    changes: dict[str, Any] = {}
    # has_notes / has_activities are enable-only: false->true is additive
    # (updatable), but the platform forbids disabling, so an explicit true->false
    # is replace-blocked rather than silently skipped.
    for spec_key, live_key in (("has_notes", "HasNotes"),
                               ("has_activities", "HasActivities")):
        desired = ent.get(spec_key)
        if desired is None or desired == live.get(live_key):
            continue
        if not desired:  # true -> false
            return "replace_blocked", {
                **entry,
                "reason": f"{spec_key} cannot be disabled once enabled (enable-only "
                          "capability); refusing (no write).",
            }
        changes[spec_key] = True
    if _drift(ent.get("display_name"), live.get("DisplayName")):
        changes["display_name"] = ent["display_name"]
    if _drift(ent.get("display_collection_name"), live.get("DisplayCollectionName")):
        changes["display_collection_name"] = ent["display_collection_name"]
    if _drift(ent.get("description"), live.get("Description")):
        changes["description"] = ent["description"]
    if not changes:
        return "skipped", entry
    out = meta_update_mod.update_entity(backend, logical, solution=solution, **changes)
    return "updated", _with_diff(entry, out)


def _find_live_attribute(
    backend: D365Backend, attr: dict[str, Any], ctx: ReconcileCtx,
) -> dict[str, Any] | None:
    """Read the live attribute's base projection, or None when there is nothing to
    reconcile here — a lookup/customer kind is relationship-backed (apply's create
    path delegates it to the relationship builder), so reading it would add a probe
    for a component this slice does not reconcile.

    The un-cast projection carries @odata.type and base properties
    (DisplayName/Description/RequiredLevel) but NOT type-specific ones (MaxLength).
    """
    kind = attr["kind"]
    info = mc.KINDS.get(kind)
    if info is None or kind in ("lookup", "customer"):
        return None
    attr_logical = attr["schema_name"].lower()
    return meta_mod.attribute_info(
        backend, ctx.entity_logical or "", attr_logical)


def _reconcile_attribute(
    backend: D365Backend, attr: dict[str, Any], live: dict[str, Any] | None,
    ctx: ReconcileCtx, entry: Entry,
) -> _Verdict:
    """Diff an existing attribute against the spec; update, skip, or block.

    Replace-blocked: a data-type change (Dataverse cannot retype a column in place;
    a drop-and-recreate is destructive, so apply refuses and writes nothing).
    Updatable: display name, description, required level, and string max-length
    GROWTH. Lookup/customer kinds are relationship-backed and not reconciled here
    (``find_live`` returns None for them).
    """
    kind = attr["kind"]
    info = mc.KINDS.get(kind)
    if info is None or kind in ("lookup", "customer") or live is None:
        return "skipped", entry
    solution = ctx.solution
    entity_logical = ctx.entity_logical or ""
    attr_logical = attr["schema_name"].lower()
    base = live
    live_type = base.get("@odata.type")
    live_cast = live_type.lstrip("#") if isinstance(live_type, str) else ""
    if live_cast and live_cast != info.cast:
        return "replace_blocked", {
            **entry,
            "reason": f"data-type change {live_cast!r} -> {info.cast!r} requires a "
                      "destructive drop-and-recreate; refusing (no write).",
        }
    changes: dict[str, Any] = {}
    if _drift(attr.get("display_name"), base.get("DisplayName")):
        changes["display_name"] = attr["display_name"]
    if _drift(attr.get("description"), base.get("Description")):
        changes["description"] = attr["description"]
    desired_required = attr.get("required")
    if desired_required is not None:
        live_required = cast("dict[str, Any]", base.get("RequiredLevel") or {}).get("Value")
        if desired_required != live_required:
            changes["required"] = desired_required
    desired_max = attr.get("max_length")
    if desired_max is not None and kind in ("string", "memo"):
        # MaxLength lives only on the typed cast projection — a second GET.
        typed = as_dict(backend.get(
            f"EntityDefinitions(LogicalName='{entity_logical}')"
            f"/Attributes(LogicalName='{attr_logical}')/{info.cast}"))
        live_max = typed.get("MaxLength")
        # ponytail: GROW only. Shrinking max-length truncates data and is out of
        # scope for this slice; a desired length <= the live length is left as-is.
        if isinstance(live_max, int) and desired_max > live_max:
            changes["max_length"] = desired_max
    if not changes:
        return "skipped", entry
    out = meta_update_mod.update_attribute(backend, entity_logical, attr_logical,
                                           solution=solution, **changes)
    return "updated", _with_diff(entry, out)


# Relationship adapter flat cascade key → CascadeConfiguration dimension.
_CASCADE_SPEC_TO_DIM: dict[str, str] = {
    "cascade_assign": "Assign",
    "cascade_delete": "Delete",
    "cascade_reparent": "Reparent",
    "cascade_share": "Share",
    "cascade_unshare": "Unshare",
    "cascade_merge": "Merge",
}

_ONE_TO_MANY_CAST = "Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata"


def _find_live_relationship(
    backend: D365Backend, rel: dict[str, Any], ctx: ReconcileCtx,
) -> dict[str, Any]:
    """Read the relationship's base (un-cast) projection carrying RelationshipType.

    RelationshipType is a base-type property; the un-cast projection carries it
    without a cast segment. The 1:N cast (Cascade / AssociatedMenu / referenced-
    referencing entities) is read inside ``reconcile`` only when the type matches.
    """
    schema = rel["schema_name"]
    return as_dict(backend.get(
        f"RelationshipDefinitions(SchemaName='{schema}')",
        params={"$select": "RelationshipType"}))


def _reconcile_relationship(
    backend: D365Backend, rel: dict[str, Any], base: dict[str, Any],
    ctx: ReconcileCtx, entry: Entry,
) -> _Verdict:
    """Diff an existing 1:N relationship against the spec; update, skip, or block.

    Replace-blocked (identity divergence, no write): a relationship-type change
    (the live relationship matched by SchemaName is N:N, not 1:N) or a
    referenced/referencing-entity change — neither is editable in place, and a
    drop-and-recreate is destructive, so apply refuses. Updatable: cascade,
    associated-menu, and is_hierarchical (via update_relationship), plus the
    relationship-backed lookup attribute's display/description/required (via
    update_attribute on the referencing entity, closing ADR 0014's lookup
    deferral) — surfaced as ONE merged `updated` entry per relationship block. An
    invalid is_hierarchical toggle is rejected by the platform as a D365Error,
    surfacing as `failed` (not replace_blocked). See ADR 0018.
    """
    solution = ctx.solution
    schema = rel["schema_name"]
    if base.get("RelationshipType") != "OneToManyRelationship":
        return "replace_blocked", {
            **entry,
            "reason": f"relationship type {base.get('RelationshipType')!r} is not "
                      "one-to-many; a type change requires a destructive "
                      "drop-and-recreate; refusing (no write).",
        }
    # The 1:N cast is the only projection carrying Cascade/AssociatedMenu/the
    # referenced/referencing entities.
    live = as_dict(backend.get(
        f"RelationshipDefinitions(SchemaName='{schema}')/{_ONE_TO_MANY_CAST}",
        params={"$select": "ReferencedEntity,ReferencingEntity,ReferencingAttribute,"
                           "CascadeConfiguration,AssociatedMenuConfiguration,IsHierarchical"}))
    # Identity fields fixed at create — referenced/referencing entity, and the
    # lookup column (ReferencingAttribute, the FK created with the relationship).
    # A divergence on any of them is an immutable change: reconciling the live
    # column anyway would silently leave the spec unsatisfied, so refuse instead.
    for spec_key, live_key in (("referenced_entity", "ReferencedEntity"),
                               ("referencing_entity", "ReferencingEntity"),
                               ("lookup_schema", "ReferencingAttribute")):
        desired = rel.get(spec_key)
        current = live.get(live_key)
        if desired and current and str(desired).lower() != str(current).lower():
            return "replace_blocked", {
                **entry,
                "reason": f"{spec_key} change {current!r} -> {desired!r} requires a "
                          "destructive drop-and-recreate; refusing (no write).",
            }
    # Relationship-level drift → update_relationship kwargs.
    rel_kwargs: dict[str, Any] = {}
    cascade_live = cast("dict[str, Any]", live.get("CascadeConfiguration") or {})
    cascade_changes = {
        dim: rel[spec_key]
        for spec_key, dim in _CASCADE_SPEC_TO_DIM.items()
        if rel.get(spec_key) is not None and rel[spec_key] != cascade_live.get(dim)
    }
    if cascade_changes:
        rel_kwargs["cascade"] = cascade_changes
    menu_live = cast("dict[str, Any]", live.get("AssociatedMenuConfiguration") or {})
    if rel.get("menu_behavior") is not None and rel["menu_behavior"] != menu_live.get("Behavior"):
        rel_kwargs["menu_behavior"] = rel["menu_behavior"]
    if _drift(rel.get("menu_label"), menu_live.get("Label")):
        rel_kwargs["menu_label"] = rel["menu_label"]
    if rel.get("menu_order") is not None and rel["menu_order"] != menu_live.get("Order"):
        rel_kwargs["menu_order"] = rel["menu_order"]
    if rel.get("is_hierarchical") is not None and rel["is_hierarchical"] != live.get("IsHierarchical"):
        rel_kwargs["is_hierarchical"] = rel["is_hierarchical"]
    # Lookup-attribute drift (display / description / required) on the referencing
    # entity — the relationship-backed lookup column, matched by ReferencingAttribute.
    lookup_changes: dict[str, Any] = {}
    referencing = str(live.get("ReferencingEntity") or rel.get("referencing_entity") or "")
    lookup_logical = str(live.get("ReferencingAttribute") or rel.get("lookup_schema") or "").lower()
    if referencing and lookup_logical:
        info = meta_mod.attribute_info(backend, referencing, lookup_logical)
        if _drift(rel.get("lookup_display"), info.get("DisplayName")):
            lookup_changes["display_name"] = rel["lookup_display"]
        if _drift(rel.get("lookup_description"), info.get("Description")):
            lookup_changes["description"] = rel["lookup_description"]
        desired_required = rel.get("required")
        if desired_required is not None:
            live_required = cast("dict[str, Any]", info.get("RequiredLevel") or {}).get("Value")
            if desired_required != live_required:
                lookup_changes["required"] = desired_required
    if not rel_kwargs and not lookup_changes:
        return "skipped", entry
    # Merge the relationship and lookup field-level diffs into one `updated` entry
    # (a real apply returns no diff; under --dry-run each carries its drift).
    diff: dict[str, Any] = {}
    if rel_kwargs:
        out = meta_update_mod.update_relationship(backend, schema, solution=solution, **rel_kwargs)
        diff.update(cast("dict[str, Any]", out.get("diff") or {}))
    if lookup_changes:
        out = meta_update_mod.update_attribute(backend, referencing, lookup_logical,
                                               solution=solution, **lookup_changes)
        diff.update(cast("dict[str, Any]", out.get("diff") or {}))
    return "updated", ({**entry, "diff": diff} if diff else entry)


def _find_live_view(
    backend: D365Backend, view: dict[str, Any], ctx: ReconcileCtx,
) -> list[dict[str, Any]]:
    """Read the saved view(s) sharing this block's identity tuple.

    A view is matched by ``(returnedtypecode, name, querytype)`` — savedquery has no
    alternate key, so that tuple is its identity. Returns every row sharing the
    tuple (``reconcile`` treats 0 as no-live and >1 as an ambiguous skip).
    """
    name = view["name"]
    query_type = view.get("query_type") or "public"
    querytype = views_mod.QUERY_TYPES[query_type]
    return backend.get_collection(
        "savedqueries",
        params={
            "$filter": (f"name eq {odata_literal(name)} "
                        f"and returnedtypecode eq {odata_literal(ctx.entity_logical or '')} "
                        f"and querytype eq {querytype}"),
            "$select": "savedqueryid,name,fetchxml,layoutxml,isdefault,description",
        })


def _reconcile_view(
    backend: D365Backend, view: dict[str, Any], rows: list[dict[str, Any]],
    ctx: ReconcileCtx, entry: Entry,
) -> _Verdict:
    """Diff an existing saved view against the spec; update in place or skip.

    A drifted description / default / columns / sort / active-filter is reconciled
    by a record PATCH of the regenerated fetchxml + layoutxml (and the scalar
    isdefault / description), per ADR 0018's reads-execute rule (the write is
    suppressed under --dry-run).

    Never blocks: there is no destructive divergence on the in-place PATCH path. A
    changed ``name`` or ``query_type`` does NOT reach here — it has no live match,
    so the create path makes a NEW view and leaves the old one for ``--prune``
    (a documented limitation, not a replace-block). An ambiguous match (>1 live
    view sharing the identity tuple) is skipped with a reason rather than patching
    an arbitrary row. An omitted spec field never drifts.
    """
    solution = ctx.solution
    entity_logical = ctx.entity_logical or ""
    otc = ctx.object_type_code or 0
    name = view["name"]
    query_type = view.get("query_type") or "public"
    if len(rows) > 1:
        return "skipped", {
            **entry,
            "reason": f"{len(rows)} {query_type} views named {name!r} on "
                      f"{entity_logical} share the (name, query_type) identity; "
                      "refusing to reconcile an arbitrary one (resolve the "
                      "duplicate, or edit by savedqueryid).",
        }
    if not rows:
        # No live match (the create path owns creation, and a rename creates a new
        # view); nothing to reconcile in place.
        return "skipped", entry
    row = rows[0]
    sqid = str(row.get("savedqueryid"))
    # Live state first, so a field the spec omits can fall back to it.
    live_columns = _columns(views_mod.parse_layout_columns(row.get("layoutxml") or ""))
    live_order_by, live_order_desc, live_filter_active = \
        views_mod.parse_fetch_order_filter(row.get("fetchxml") or "")
    live_is_default = bool(row.get("isdefault", False))
    live_description = row.get("description")

    # Desired state: a field the spec OMITS falls back to the live value, so
    # omission never drifts — only a spec-declared field reconciles (issue #606).
    # `columns` is required by validate_spec, so it is always declared; the
    # optional fields default to live, not to the create-path default (else an
    # omitted is_default would silently demote a live default view, and an omitted
    # filter_active/order would strip a live sort/filter on a columns-only edit).
    desired_columns = _columns(view.get("columns"))
    desired_order_by = view["order_by"] if "order_by" in view else live_order_by
    desired_order_desc = (bool(view["order_desc"]) if "order_desc" in view
                          else live_order_desc)
    desired_filter_active = (bool(view["filter_active"]) if "filter_active" in view
                             else live_filter_active)
    desired_is_default = (bool(view["is_default"]) if "is_default" in view
                          else live_is_default)
    desired_description = view.get("description")

    diff: dict[str, Any] = {}
    changes: dict[str, Any] = {}
    # An omitted description never drifts (None → no change, like _drift).
    if desired_description is not None and desired_description != live_description:
        diff["description"] = {"old": live_description, "new": desired_description}
        changes["description"] = desired_description
    if desired_is_default != live_is_default:
        diff["is_default"] = {"old": live_is_default, "new": desired_is_default}
        changes["isdefault"] = desired_is_default
    # Columns drive layoutxml (names + widths) and the fetchxml attribute set
    # (names only); a sort/active-filter change drives fetchxml alone.
    if desired_columns != live_columns:
        diff["columns"] = {"old": live_columns, "new": desired_columns}
        changes["layoutxml"] = views_mod.build_layoutxml(
            entity_logical, otc, desired_columns)
    if desired_order_by != live_order_by:
        diff["order_by"] = {"old": live_order_by, "new": desired_order_by}
    if desired_order_desc != live_order_desc:
        diff["order_desc"] = {"old": live_order_desc, "new": desired_order_desc}
    if desired_filter_active != live_filter_active:
        diff["filter_active"] = {"old": live_filter_active, "new": desired_filter_active}
    fetch_drift = (
        [n for n, _ in desired_columns] != [n for n, _ in live_columns]
        or desired_order_by != live_order_by
        or desired_order_desc != live_order_desc
        or desired_filter_active != live_filter_active)
    if fetch_drift:
        changes["fetchxml"] = views_mod.build_fetchxml(
            entity_logical, desired_columns, desired_order_by,
            desired_filter_active, desired_order_desc)
    if not changes:
        return "skipped", entry
    views_mod.update_view(backend, savedqueryid=sqid, changes=changes, solution=solution)
    return "updated", ({**entry, "diff": diff} if backend.dry_run else entry)


# ── per-kind cross-field validation (adapter.extra_validate) ─────────────────
# Each function is the entity-subtree kind's cross-field block rule — the rules
# that a data field (required_block_keys / schema_name_keys / …) cannot express.
# adapter.validate runs them last, after the required-key and mc.* checks. Every
# error MESSAGE is preserved verbatim from the old inline validate_spec pass; the
# only order change is entity ownership, which now validates after (not before)
# the schema-name check — unobservable in practice, since each rule trips on its
# own field and a block rarely violates two at once.
def _validate_entity_block(ent: dict[str, Any]) -> None:
    """Ownership vocabulary + the nested primary-attribute schema name."""
    elabel = f"entity {ent['schema_name']!r}"
    # Validate ownership up front so a typo fails cleanly here rather than being
    # misclassified as a destructive ownership change during reconciliation.
    if ent.get("ownership") is not None:
        mc.validate_ownership(ent["ownership"], subject=f"{elabel} ownership")
    # The primary attribute's schema_name is nested under primary_attr (the adapter
    # forwards it via a transform), so it isn't a top-level schema-name key —
    # validate it here too, matching create_entity's own check.
    primary = ent.get("primary_attr")
    if isinstance(primary, dict):
        pa = cast("dict[str, Any]", primary)
        if pa.get("schema_name"):
            mc.validate_schema_name(pa["schema_name"], subject="primary_attr_schema")


def _validate_attribute_block(attr: dict[str, Any]) -> None:
    """Kind vocabulary, kind-specific requirements, option shape, and the
    calculated/rollup source_type ↔ formula_definition cross-rules (#554)."""
    kind, name = attr["kind"], attr["schema_name"]
    if kind not in ATTRIBUTE_KINDS:
        raise D365Error(f"attribute {name!r}: unknown kind {kind!r}.")
    if kind == "lookup" and not attr.get("target_entity"):
        raise D365Error(f"lookup attribute {name!r} requires target_entity.")
    if kind in ("picklist", "multiselect") and not (
            attr.get("optionset_name") or attr.get("options")):
        raise D365Error(
            f"{kind} attribute {name!r} requires optionset_name or options.")
    if "options" in attr:
        _require_list(attr, "options", f"attribute {name!r}")
        for opt in cast("list[Any]", attr["options"] or []):
            _validate_option(opt, f"attribute {name!r}")
    # max_length is compared numerically during reconciliation (grow check);
    # a quoted/non-int value from the spec must fail here, not blow up later.
    if attr.get("max_length") is not None and not isinstance(attr["max_length"], int):
        raise D365Error(
            f"attribute {name!r}: max_length must be an integer (unquoted in YAML).")
    # source_type / formula_definition (calculated & rollup columns, #554):
    # mirror add_attribute's contract — a non-simple source needs a formula and is
    # invalid for the relationship-backed kinds; a formula is invalid on a simple
    # column. Gate on key PRESENCE, not truthiness, so an explicit null source_type
    # or an empty formula fails HERE rather than slipping through to a mid-apply
    # add_attribute raise.
    source_type = attr.get("source_type")
    if "source_type" in attr and source_type not in mc.SOURCE_TYPES:
        raise D365Error(
            f"attribute {name!r}: source_type must be one of "
            f"{sorted(mc.SOURCE_TYPES)}.")
    formula = attr.get("formula_definition")
    if formula is not None and not isinstance(formula, str):
        raise D365Error(
            f"attribute {name!r}: formula_definition must be a string.")
    if source_type in ("calculated", "rollup"):
        if kind in ("lookup", "customer"):
            raise D365Error(
                f"attribute {name!r}: source_type {source_type!r} is not "
                f"valid for kind {kind!r}.")
        if not formula:
            raise D365Error(
                f"attribute {name!r}: source_type {source_type!r} requires "
                "formula_definition.")
    elif "formula_definition" in attr:
        raise D365Error(
            f"attribute {name!r}: formula_definition is only valid with "
            "source_type 'calculated' or 'rollup'.")


def _validate_relationship_block(rel: dict[str, Any]) -> None:
    """The associated-menu UseLabel cross-field rule create_one_to_many enforces."""
    # An associated-menu label is mandatory under UseLabel. Mirror it up front so a
    # malformed menu config fails before the relationship phase writes (it runs
    # after the entity/attribute phases have already landed).
    if rel.get("menu_behavior") == "UseLabel" and not rel.get("menu_label"):
        raise D365Error(
            f"relationship {rel['schema_name']!r}: menu_behavior 'UseLabel' "
            "requires menu_label.")


def _validate_view_block(view: dict[str, Any]) -> None:
    """query_type vocabulary and the non-empty, well-shaped columns list."""
    # query_type is spec-expressible; validate it against the same vocabulary
    # create_view checks so an unknown value fails up front rather than in the
    # views phase (the last phase to write).
    if view.get("query_type") is not None and view["query_type"] not in views_mod.QUERY_TYPES:
        raise D365Error(
            f"view {view['name']!r}: unknown query_type {view['query_type']!r}; "
            f"choose from {sorted(views_mod.QUERY_TYPES)}.")
    if not isinstance(view["columns"], list) or not view["columns"]:
        raise D365Error(f"view {view['name']!r}: columns must be a non-empty list.")
    for col in cast("list[Any]", view["columns"]):
        _validate_column(col, view["name"])


def _reconcile_via_adapter(
    adapter: "Adapter", backend: D365Backend, block: dict[str, Any],
    ctx: ReconcileCtx, entry: Entry,
    routes: dict[str, list[Entry]], failed: list[Entry],
) -> None:
    """Present-branch reconcile through a component-kind adapter.

    ``find_live`` and ``reconcile`` run inside the abort-guarded thunk, so a
    D365Error in the live read or the diff routes to ``failed`` and aborts —
    exactly as when the read lived inside the free reconcile function.
    """
    find_live = adapter.find_live
    reconcile = adapter.reconcile
    assert find_live is not None and reconcile is not None  # populated for these kinds

    def thunk() -> _Verdict:
        live = find_live(backend, block, ctx)
        return reconcile(backend, block, live, ctx, entry)

    _reconcile(entry, thunk, failed, routes)


# ── optionset / web resource / security role adapter functions ───────────────
# The find_live / reconcile / extra_validate slots for the three kinds migrated in
# #646 (PRD slice 2/3). Defined above REGISTRY so the dict literal can reference
# them; the helpers they call at run time (_webresource_content,
# _desired_role_privileges, _privilege_diff) live further down, unchanged.
def _find_live_optionset(
    backend: D365Backend, os_spec: dict[str, Any], ctx: ReconcileCtx,
) -> dict[str, Any]:
    """Read the live global option set this block reconciles against."""
    return os_mod.get_optionset(backend, os_spec["name"])


def _validate_optionset_block(os_spec: dict[str, Any]) -> None:
    """A non-empty, well-shaped options list (create_optionset's own option rules)."""
    _require_list(os_spec, "options", f"optionset {os_spec['name']!r}")
    for opt in cast("list[Any]", os_spec.get("options") or []):
        _validate_option(opt, f"optionset {os_spec['name']!r}")


def _reconcile_optionset(
    backend: D365Backend, os_spec: dict[str, Any], live: dict[str, Any],
    ctx: ReconcileCtx, entry: Entry,
) -> _Verdict:
    """Diff an existing global option set; insert spec-declared options it lacks.

    Only options with an explicit value are reconciled — an auto-valued option
    (value omitted) cannot be matched against the live set, so re-applying it would
    insert a duplicate on every run; those are left to the create path. There is no
    destructive divergence for option sets, so this never blocks.
    """
    live_options = cast("list[dict[str, Any]]", live.get("Options") or [])
    live_values = {o.get("Value") for o in live_options if isinstance(o.get("Value"), int)}
    inserts: list[tuple[int | None, str]] = [
        (o["value"], o["label"]) for o in _as_list(os_spec.get("options"))
        if isinstance(o.get("value"), int) and o["value"] not in live_values
    ]
    if not inserts:
        return "skipped", entry
    out = os_mod.update_optionset(
        backend, os_spec["name"], insert=inserts, solution=ctx.solution)
    return "updated", _with_diff(entry, out)


def _find_live_webresource(
    backend: D365Backend, wr: dict[str, Any], ctx: ReconcileCtx,
) -> dict[str, Any] | None:
    """Read the live web resource by name, or None when it must be created."""
    return wr_mod.find_webresource(backend, wr["name"])


def _validate_webresource_block(wr: dict[str, Any]) -> None:
    """Body source (a `file` xor inline `content`), string-typed fields, and a
    resolvable web-resource type — the up-front rules create_webresource assumes."""
    # A web resource's body comes from either a `file` on disk or an inline
    # base64 `content` string (export-spec emits the latter so the spec is
    # self-contained). Exactly one source is needed.
    if wr.get("file") is None and wr.get("content") is None:
        raise D365Error(
            f"web resource {wr.get('name')!r}: needs 'file' or inline 'content'.")
    # name/file/display_name/content reach os.path.join + base64 + the Web API
    # as strings; a non-string (e.g. an unquoted YAML number) must fail here with
    # a clean D365Error, not blow up later inside os.path.join / base64.
    for key in ("name", "file", "display_name", "content"):
        if wr.get(key) is not None and not isinstance(wr[key], str):
            raise D365Error(f"web resource {wr.get('name')!r}: {key} must be a string.")
    if wr.get("webresourcetype") is not None and not isinstance(wr["webresourcetype"], int):
        raise D365Error(
            f"web resource {wr['name']!r}: webresourcetype must be an integer.")
    # Fail fast (before any HTTP) on a type that can't be resolved. With a `file`,
    # an unknown extension and no explicit type is the error; with inline
    # `content` there is no extension to infer from, so webresourcetype is
    # required outright.
    if wr.get("file") is not None:
        wr_mod.resolve_webresourcetype(wr["file"], wr.get("webresourcetype"))
    elif wr.get("webresourcetype") is None:
        raise D365Error(
            f"web resource {wr['name']!r}: webresourcetype is required when the "
            "body is inline 'content' (no file extension to infer the type from).")


def _reconcile_webresource(
    backend: D365Backend, wr: dict[str, Any], live: dict[str, Any],
    ctx: ReconcileCtx, entry: Entry,
) -> _Verdict:
    """Diff an existing web resource against the spec; update content/display or skip.

    The spec's body bytes — read here from the block + ``ctx.base_dir`` — are
    base64-compared against the live `content` column; a declared `display_name`
    is compared too. There is no destructive divergence for a web resource, so this
    never blocks. The update defers publishing (publish=False) — apply publishes
    once at the end.
    """
    content = _webresource_content(ctx.base_dir, wr)
    desired_b64 = base64.b64encode(content).decode("ascii")
    changes: dict[str, Any] = {}
    if live.get("content") != desired_b64:
        changes["content"] = content
    desired_display = wr.get("display_name")
    if desired_display is not None and desired_display != live.get("displayname"):
        changes["display_name"] = desired_display
    if not changes:
        return "skipped", entry
    wr_mod.update_webresource(
        backend, wr["name"], content=changes.get("content"),
        display_name=changes.get("display_name"), solution=ctx.solution, publish=False)
    return "updated", {**entry, "diff": {"fields": sorted(changes)}}


def _find_live_security_role(
    backend: D365Backend, role_spec: dict[str, Any], ctx: ReconcileCtx,
) -> str | None:
    """The existing role's id (scoped to its business unit), or None to create."""
    return sec_mod.find_role(
        backend, role_spec["name"], business_unit=role_spec.get("business_unit"))


def _validate_security_role_block(role: dict[str, Any]) -> None:
    """String-typed name / business_unit and the privilege-matrix shape (≥1 row,
    each row a well-formed set-role-privileges selector group)."""
    rlabel = f"security role {role['name']!r}"
    if not isinstance(role["name"], str):
        raise D365Error("security role: name must be a string.")
    if role.get("business_unit") is not None and not isinstance(role["business_unit"], str):
        raise D365Error(f"{rlabel}: business_unit must be a string (GUID).")
    _require_list(role, "privileges", rlabel)
    # A role with no declared privileges would send an empty ReplacePrivilegesRole,
    # wiping the role's removable privileges — almost certainly a spec mistake.
    if not _as_list(role.get("privileges")):
        raise D365Error(f"{rlabel}: at least one privilege row is required.")
    for row in _as_list(role.get("privileges")):
        _require(row, ("depth",), f"{rlabel} privilege")
        if not isinstance(row["depth"], str):
            raise D365Error(f"{rlabel}: privilege depth must be a string.")
        # The selectors are passed straight to set-role-privileges, which expects
        # lists of strings (each item is later .strip()/.lower()'d); validate the
        # shape up front so a malformed spec fails with a clean D365Error here
        # rather than an AttributeError mid-apply.
        for key in ("access", "entities", "privilege_names"):
            if key in row:
                if not isinstance(row[key], list):
                    raise D365Error(f"{rlabel}: privilege {key!r} must be a list.")
                if not all(isinstance(item, str) for item in cast("list[Any]", row[key])):
                    raise D365Error(f"{rlabel}: privilege {key!r} items must be strings.")
        if not (row.get("access") or row.get("privilege_names")):
            raise D365Error(
                f"{rlabel}: each privilege row needs 'access' (with 'entities' or "
                "'all_entities') or 'privilege_names'.")


def _reconcile_security_role(
    backend: D365Backend, role_spec: dict[str, Any], role_id: str,
    ctx: ReconcileCtx, entry: Entry,
) -> _Verdict:
    """Reconcile an existing role's privileges to the declared set.

    ``role_id`` is the ``find_live`` slot's value (the live role id). The role's
    live privileges (RetrieveRolePrivilegesRole) are compared to the declared
    matrix by (privilege id -> depth). When every declared privilege is already
    present at its declared depth it is a no-op; otherwise ReplacePrivilegesRole
    sets the role to the declared set — authoritative within the declared role, so
    unlisted *removable* privileges are dropped (distinct from `--prune`, which is
    about whole components). There is no destructive divergence, so this never
    blocks; the replace write is suppressed under dry-run (reads-execute rule), so
    the same path yields a drift report.

    Convergence note: Dataverse auto-grants every role a small set of immovable
    baseline privileges (e.g. SharePoint document management) that
    ReplacePrivilegesRole cannot remove (verified live). A strict set-equality check
    would therefore never converge — it would re-report the role as drifted every
    run. So the skip test is "are all declared privileges present at the declared
    depth?" (a satisfied lower bound), not "is the live set exactly the declared
    set?". A re-applied unchanged spec is a true no-op.

    Consequence: a removal-only spec change — dropping a privilege while every
    remaining declared privilege is still satisfied at its depth — does NOT
    reconcile (the skip test passes). Unlisted privileges are dropped only when the
    replace fires because some declared privilege is missing or at the wrong depth.
    """
    desired, _ = _desired_role_privileges(backend, role_spec)
    live = sec_mod.get_role_privileges(backend, role_id)
    live_map = {p["privilegeid"]: p["depth"] for p in live}
    if all(live_map.get(p["privilegeid"]) == p["depth"] for p in desired):
        return "skipped", entry
    diff = _privilege_diff(live, desired)
    sec_mod.replace_role_privileges(backend, role_id, desired)
    return "updated", {**entry, "diff": diff}


# ── plug-in assembly / plug-in step adapter functions ────────────────────────
# The find_live / reconcile / extra_validate slots for the compound plug-in
# migrated in #647 (PRD slice 3/3). The assembly is the top-level block and its
# steps nest under it (same shape as entity/attribute in the walk); a plug-in's
# types and a step's images are create-only sub-collections with no reconcile, so
# they are not registry kinds. Defined above REGISTRY so the dict literal can
# reference them; the helpers they call at run time (_read_file_bytes, _expanded)
# live further down, unchanged.
def _assembly_name(plugin: dict[str, Any]) -> str:
    """The assembly's registration name: explicit ``assembly`` or the DLL basename."""
    return plugin.get("assembly") or os.path.splitext(
        os.path.basename(plugin["file"]))[0]


def _find_live_plugin_assembly(
    backend: D365Backend, plugin: dict[str, Any], ctx: ReconcileCtx,
) -> dict[str, Any] | None:
    """Probe the live plug-in assembly this block reconciles against (or None)."""
    return plugin_mod.find_assembly(backend, _assembly_name(plugin))


def _validate_plugin_assembly_block(plugin: dict[str, Any]) -> None:
    """Assembly file/name rules plus the isolation_mode vocabulary.

    ``file`` is required (the DLL to register) but is not a builder param — the
    driver derives ``path`` / ``name`` from it — so it lives here rather than in
    ``required_block_keys``. isolation_mode is routed through the same
    ``ISOLATION_MODE_VALUES`` map register_assembly checks, so an unknown value
    fails up front instead of mid-apply.
    """
    _require(plugin, ("file",), "plug-in")
    if not isinstance(plugin["file"], str):
        raise D365Error("plug-in: file must be a string.")
    if plugin.get("assembly") is not None and not isinstance(plugin["assembly"], str):
        raise D365Error("plug-in: assembly must be a string.")
    # Gate on key PRESENCE, not truthiness: an explicit `isolation_mode: null` is
    # invalid (register_assembly rejects None — it is not in the map) and must fail
    # here, not slip through to_kwargs and blow up mid-apply. An omitted key falls
    # to the builder's own default.
    if "isolation_mode" in plugin and plugin["isolation_mode"] not in plugin_mod.ISOLATION_MODE_VALUES:
        raise D365Error(
            f"plug-in {_assembly_name(plugin)!r}: unknown isolation_mode "
            f"{plugin['isolation_mode']!r}; "
            f"choose from {sorted(plugin_mod.ISOLATION_MODE_VALUES)}.")


def _reconcile_plugin_assembly(
    backend: D365Backend, plugin: dict[str, Any], live: dict[str, Any],
    ctx: ReconcileCtx, entry: Entry,
) -> _Verdict:
    """Diff an existing plug-in assembly against the rebuilt DLL; update or skip.

    The spec file's bytes — read here from the block's ``file`` against
    ``ctx.base_dir`` — are base64-compared against the live ``content`` column; on
    drift the content is PATCHed in place (register_assembly's update path). There
    is no destructive divergence for an assembly, so this never blocks. The update
    is suppressed under dry-run (reads-execute rule).
    """
    name = _assembly_name(plugin)
    content = _read_file_bytes(ctx.base_dir, plugin["file"])
    desired_b64 = base64.b64encode(content).decode("ascii")
    if live.get("content") == desired_b64:
        return "skipped", entry
    asm_path = os.path.join(ctx.base_dir or "", plugin["file"])
    plugin_mod.register_assembly(
        backend, path=asm_path, name=name, update=True, solution=ctx.solution)
    return "updated", {**entry, "diff": {"fields": ["content"]}}


# Step string-typed fields: every one reaches the Web API as a string, so an
# unquoted YAML scalar must fail up front, not blow up mid-apply.
_STEP_STRING_KEYS = ("name", "message", "plugin_type", "entity", "stage", "mode",
                     "filtering_attributes", "configuration")


def _find_live_plugin_step(
    backend: D365Backend, step: dict[str, Any], ctx: ReconcileCtx,
) -> dict[str, Any] | None:
    """Probe the live plug-in step (by unique name) this block reconciles against."""
    return plugin_mod.find_step(backend, step["name"])


def _validate_plugin_step_block(step: dict[str, Any]) -> None:
    """Step binding requirements, field types, and the stage/mode vocabulary.

    ``name`` and ``plugin_type`` are required by apply (a step is keyed by its
    unique name and binds to a plug-in type) though register_step defaults them, so
    they live here rather than in ``required_block_keys`` — which mirrors the
    builder's own required params (only ``message``). stage/mode are routed through
    the same STAGE_VALUES / MODE_VALUES maps register_step checks, and the
    async⇒postoperation rule mirrors its cross-field guard; all fail up front.
    """
    _require(step, ("name", "plugin_type"), "plug-in step")
    slabel = f"plug-in step {step['name']!r}"
    for key in _STEP_STRING_KEYS:
        if step.get(key) is not None and not isinstance(step[key], str):
            raise D365Error(f"{slabel}: {key!r} must be a string.")
    # Presence-gated like the vocabularies below: an explicit `rank: null` is
    # invalid (to_kwargs would forward rank=None into register_step's POST body),
    # so reject a present non-int here; an omitted key falls to the builder default.
    if "rank" in step and not isinstance(step["rank"], int):
        raise D365Error(f"{slabel}: rank must be an integer (unquoted in YAML).")
    # Gate the vocabularies on key PRESENCE, not truthiness: an explicit
    # `stage: null` / `mode: null` is invalid (register_step rejects None — it is
    # not in the map) and must fail here rather than slip through to_kwargs and
    # blow up mid-apply. An omitted key falls to the builder's own default.
    if "stage" in step and step["stage"] not in plugin_mod.STAGE_VALUES:
        raise D365Error(f"{slabel}: unknown stage {step['stage']!r}; "
                        f"choose from {sorted(plugin_mod.STAGE_VALUES)}.")
    if "mode" in step and step["mode"] not in plugin_mod.MODE_VALUES:
        raise D365Error(f"{slabel}: unknown mode {step['mode']!r}; "
                        f"choose from {sorted(plugin_mod.MODE_VALUES)}.")
    if step.get("mode") == "async" and step.get("stage", "postoperation") != "postoperation":
        raise D365Error(f"{slabel}: asynchronous mode requires the postoperation "
                        f"stage (got {step['stage']!r}).")


def _reconcile_plugin_step(
    backend: D365Backend, step: dict[str, Any], live: dict[str, Any],
    ctx: ReconcileCtx, entry: Entry,
) -> _Verdict:
    """Diff an existing plug-in step against the spec; update, skip, or block.

    Replace-blocked: a binding change — the SDK message, the primary entity, or
    the plug-in type the step handles. The platform fixes those at creation, so
    changing one needs a destructive delete-and-recreate; apply refuses (no
    write) so the operator makes it deliberately. Updatable in place: the runtime
    config (stage, mode, rank, filtering attributes, unsecure configuration) —
    only the fields the spec explicitly declares are reconciled, so an omitted
    field is left as-is. MS Learn recommends updating a step over delete-recreate.
    """
    if (step["message"] != _expanded(live, "sdkmessageid", "name")
            or step.get("entity") != _expanded(live, "sdkmessagefilterid",
                                                "primaryobjecttypecode")
            or step["plugin_type"] != _expanded(live, "plugintypeid", "typename")):
        return "replace_blocked", {
            **entry,
            "reason": "step binding change (message / entity / plug-in type) "
                      "requires a destructive delete-and-recreate; refusing (no write).",
        }
    changes: dict[str, Any] = {}
    if "stage" in step and plugin_mod.STAGE_VALUES.get(step["stage"]) != live.get("stage"):
        changes["stage"] = step["stage"]
    if "mode" in step and plugin_mod.MODE_VALUES.get(step["mode"]) != live.get("mode"):
        changes["mode"] = step["mode"]
    if "rank" in step and step["rank"] != live.get("rank"):
        changes["rank"] = step["rank"]
    # filteringattributes is only meaningful on Update (register_step ignores it
    # otherwise), so only reconcile it there — else a declared filter on a
    # non-Update step would re-update on every run.
    if ("filtering_attributes" in step and step["message"].lower() == "update"
            and step["filtering_attributes"] != live.get("filteringattributes")):
        changes["filtering_attributes"] = step["filtering_attributes"]
    if "configuration" in step and step["configuration"] != live.get("configuration"):
        changes["configuration"] = step["configuration"]
    if not changes:
        return "skipped", entry
    plugin_mod.update_step(
        backend, step_id=str(live["sdkmessageprocessingstepid"]),
        solution=ctx.solution, **changes)
    return "updated", {**entry, "diff": {"fields": sorted(changes)}}


# One component-kind adapter per component kind — the complete authority for its
# kind (validate / to_kwargs / find_live / reconcile). All nine reconciled kinds
# are registry-driven: the entity subtree (entity, attribute, relationship, view),
# optionset, web resource, security role, and the compound plug-in (assembly +
# step). Publisher and solution stay out of the registry permanently — they are
# the target preamble a customization write files into, not reconciled components.
REGISTRY: dict[str, Adapter] = {
    "attribute": Adapter(
        map={
            "kind": "kind",
            "schema_name": "schema_name",
            "display_name": "display_name",
            "description": "description",
            "required": "required",
            "max_length": "max_length",
            "format_name": "format_name",
            "auto_number_format": "auto_number_format",
            "behavior_name": "behavior_name",
            "min_value": "min_value",
            "max_value": "max_value",
            "precision": "precision",
            "default_value": "default_value",
            "true_label": "true_label",
            "false_label": "false_label",
            "optionset_name": "optionset_name",
            "target_entity": "target_entity",
            "relationship_schema": "relationship_schema",
            "max_size_kb": "max_size_kb",
            "source_type": "source_type",
            "formula_definition": "formula_definition",
        },
        transforms={
            "options": lambda b: (
                [(o.get("value"), o["label"]) for o in _as_list(b.get("options"))] or None
            ),
        },
        injected=frozenset({"backend", "entity", "solution", "if_exists", "publish"}),
        defaults={"required": "None", "source_type": "simple"},
        schema_name_keys=("schema_name",),
        required_keys=("required",),
        required_block_keys=("kind", "schema_name", "display_name"),
        block_label="attribute",
        extra_validate=_validate_attribute_block,
        find_live=_find_live_attribute,
        reconcile=_reconcile_attribute,
    ),
    "relationship": Adapter(
        map={
            "schema_name": "schema_name",
            "referenced_entity": "referenced_entity",
            "referencing_entity": "referencing_entity",
            "lookup_schema": "lookup_schema",
            "lookup_display": "lookup_display",
            "required": "lookup_required",
            "lookup_description": "lookup_description",
            "cascade_assign": "cascade_assign",
            "cascade_delete": "cascade_delete",
            "cascade_reparent": "cascade_reparent",
            "cascade_share": "cascade_share",
            "cascade_unshare": "cascade_unshare",
            "cascade_merge": "cascade_merge",
            "menu_label": "menu_label",
            "menu_behavior": "menu_behavior",
            "menu_order": "menu_order",
            "is_hierarchical": "is_hierarchical",
        },
        transforms={},
        injected=frozenset({"backend", "solution", "if_exists", "publish"}),
        defaults={"required": "None"},
        schema_name_keys=("schema_name", "lookup_schema"),
        required_keys=("required",),
        cascade_keys=(
            "cascade_assign", "cascade_delete", "cascade_reparent",
            "cascade_share", "cascade_unshare", "cascade_merge",
        ),
        menu_keys=("menu_behavior",),
        required_block_keys=(
            "schema_name", "referenced_entity", "referencing_entity",
            "lookup_schema", "lookup_display",
        ),
        block_label="relationship",
        extra_validate=_validate_relationship_block,
        find_live=_find_live_relationship,
        reconcile=_reconcile_relationship,
    ),
    "entity": Adapter(
        map={
            "schema_name": "schema_name",
            "display_name": "display_name",
            "display_collection_name": "display_collection_name",
            "primary_attr_max_length": "primary_attr_max_length",
            "description": "description",
            "ownership": "ownership",
            "has_activities": "has_activities",
            "has_notes": "has_notes",
            "is_activity": "is_activity",
            "data_provider_id": "data_provider_id",
            "data_source_id": "data_source_id",
            "external_name": "external_name",
            "external_collection_name": "external_collection_name",
        },
        transforms={
            "primary_attr_schema": lambda b: cast(
                "dict[str, Any]", b.get("primary_attr") or {}).get("schema_name"),
            "primary_attr_label": lambda b: cast(
                "dict[str, Any]", b.get("primary_attr") or {}).get("label"),
        },
        injected=frozenset({"backend", "solution", "if_exists"}),
        defaults={"ownership": "UserOwned"},
        schema_name_keys=("schema_name",),
        required_block_keys=("schema_name", "display_name"),
        block_label="entity",
        extra_validate=_validate_entity_block,
        find_live=_find_live_entity,
        reconcile=_reconcile_entity,
    ),
    "view": Adapter(
        map={
            "name": "name",
            "order_by": "order_by",
            "order_desc": "order_desc",
            "filter_active": "filter_active",
            "is_default": "is_default",
            "query_type": "query_type",
            "description": "description",
        },
        transforms={"columns": lambda b: _columns(b.get("columns"))},
        defaults={"is_default": False},
        injected=frozenset(
            {"backend", "entity", "object_type_code", "solution", "if_exists", "publish"}
        ),
        required_block_keys=("name", "columns"),
        block_label="view",
        extra_validate=_validate_view_block,
        find_live=_find_live_view,
        reconcile=_reconcile_view,
    ),
    "optionset": Adapter(
        map={
            "name": "name",
            "display_name": "display_name",
            "description": "description",
        },
        transforms={
            "options": lambda b: (
                [(o.get("value"), o["label"]) for o in _as_list(b.get("options"))] or None
            ),
        },
        injected=frozenset({"backend", "is_global", "publish", "solution", "if_exists"}),
        defaults={},
        schema_name_keys=("name",),
        required_block_keys=("name", "display_name"),
        block_label="optionset",
        extra_validate=_validate_optionset_block,
        find_live=_find_live_optionset,
        reconcile=_reconcile_optionset,
    ),
    "webresource": Adapter(
        map={
            "name": "name",
            "display_name": "display_name",
        },
        transforms={},
        # content (read from file/base64 against base_dir) and webresourcetype
        # (resolved from the extension or an override) are computed by the driver
        # and passed in, so both are injected rather than mapped/transformed.
        injected=frozenset(
            {"backend", "content", "webresourcetype", "solution", "publish"}),
        defaults={},
        required_block_keys=("name",),
        block_label="web resource",
        extra_validate=_validate_webresource_block,
        find_live=_find_live_webresource,
        reconcile=_reconcile_webresource,
    ),
    "security-role": Adapter(
        map={
            "name": "name",
            "business_unit": "business_unit",
        },
        transforms={},
        injected=frozenset({"backend", "if_exists", "solution"}),
        defaults={},
        required_block_keys=("name",),
        block_label="security role",
        extra_validate=_validate_security_role_block,
        find_live=_find_live_security_role,
        reconcile=_reconcile_security_role,
    ),
    "plugin-assembly": Adapter(
        map={
            "isolation_mode": "isolation_mode",
            "version": "version",
            "culture": "culture",
            "public_key_token": "public_key_token",
            "description": "description",
        },
        transforms={},
        # path and name are derived by the driver from the block's `file`, so both
        # are injected; update is the driver's create-vs-reconcile flag.
        injected=frozenset({"backend", "path", "name", "solution", "update"}),
        defaults={},
        # `file` is required but is not a builder param (path/name derive from it),
        # so the requirement lives in extra_validate, not required_block_keys —
        # which mirrors register_assembly's own required params (only path/backend,
        # both injected).
        required_block_keys=(),
        block_label="plug-in",
        extra_validate=_validate_plugin_assembly_block,
        find_live=_find_live_plugin_assembly,
        reconcile=_reconcile_plugin_assembly,
    ),
    "plugin-step": Adapter(
        map={
            "name": "name",
            "message": "message",
            "plugin_type": "plugin_type",
            "entity": "entity",
            "stage": "stage",
            "mode": "mode",
            "rank": "rank",
            "filtering_attributes": "filtering_attributes",
            "configuration": "configuration",
        },
        transforms={},
        # assembly (the parent's name) is supplied by the driver; asyncautodelete
        # and service_endpoint are register_step params the apply spec does not
        # expose, so the driver leaves them at their builder defaults — injected
        # (driver-owned, never spec-sourced), exactly as `publish` is for attribute.
        injected=frozenset(
            {"backend", "assembly", "solution", "asyncautodelete", "service_endpoint"}),
        defaults={},
        required_block_keys=("message",),
        block_label="plug-in step",
        extra_validate=_validate_plugin_step_block,
        find_live=_find_live_plugin_step,
        reconcile=_reconcile_plugin_step,
    ),
}


def _read_file_bytes(base_dir: str | None, file: str) -> bytes:
    """Read a file's bytes from `file`, resolved against base_dir.

    An absolute `file` is used as-is; a relative one is joined to base_dir (the
    spec file's directory) so content can live next to the spec. OSError is mapped
    to a D365Error so apply records a clean failure entry rather than crashing.
    Used for a web resource's content and a plug-in assembly's DLL.
    """
    path = os.path.join(base_dir or "", file)
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError as exc:
        raise D365Error(f"cannot read file {path!r}: {exc}") from exc


def _webresource_content(base_dir: str | None, wr: dict[str, Any]) -> bytes:
    """Web resource body bytes from inline base64 `content` or a `file` on disk.

    export-spec emits a web resource's body as an inline base64 `content` string so
    the spec round-trips without sidecar files; an authored spec may instead point
    `file` at a path. validate_spec guarantees exactly one source is present.
    """
    inline = wr.get("content")
    if inline is not None:
        try:
            return base64.b64decode(inline, validate=True)
        except ValueError as exc:  # binascii.Error subclasses ValueError
            raise D365Error(
                f"web resource {wr['name']!r}: content is not valid base64: {exc}"
            ) from exc
    return _read_file_bytes(base_dir, wr["file"])


def _desired_role_privileges(
    backend: D365Backend, role_spec: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve a role's privilege matrix to the union of its rows (highest depth wins).

    Each row is a `set-role-privileges` selector group (access+entities/all_entities
    and/or privilege_names, at one depth); the rows are resolved independently and
    merged so the role can mix depths/scopes. Returns (privileges, warnings).
    """
    sets: list[list[dict[str, Any]]] = []
    warnings: list[str] = []
    for row in _as_list(role_spec.get("privileges")):
        privs, warns = sec_mod.resolve_role_privileges(
            backend,
            access=row.get("access"),
            entities=row.get("entities"),
            all_entities=bool(row.get("all_entities")),
            privilege_names=row.get("privilege_names"),
            depth=row["depth"],
        )
        sets.append(privs)
        warnings.extend(warns)
    return sec_mod.merge_privilege_sets(sets), warnings


def _privilege_diff(
    live: list[dict[str, Any]], desired: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Field-level drift between a role's live and desired privilege sets."""
    lmap = {p["privilegeid"]: p for p in live}
    dmap = {p["privilegeid"]: p for p in desired}
    added = [d["name"] for pid, d in dmap.items() if pid not in lmap]
    removed = [lp["name"] for pid, lp in lmap.items() if pid not in dmap]
    changed = [f"{d['name']}: {lmap[pid]['depth']} -> {d['depth']}"
               for pid, d in dmap.items() if pid in lmap and lmap[pid]["depth"] != d["depth"]]
    return {"added": sorted(added), "removed": sorted(removed), "changed": sorted(changed)}


# Kinds whose create/update needs no PublishAllXml: security roles are not
# publishable customizations, and plug-in registration (assembly/type/step/image)
# activates immediately — they are not published-XML customizations. Gating the
# end-of-run publish on the publishable writes only keeps a plugin-only apply from
# issuing a pointless PublishAllXml.
_NON_PUBLISHABLE = {"security-role", "plugin-assembly", "plugin-type",
                    "plugin-step", "plugin-image"}


def _expanded(row: dict[str, Any], nav: str, field: str) -> Any:
    """Read `field` from an $expand'd single-valued navigation property, or None.

    A message-level step has no entity filter, so `sdkmessagefilterid` expands to
    null; this returns None there rather than raising.
    """
    obj = row.get(nav)
    return cast("dict[str, Any]", obj).get(field) if isinstance(obj, dict) else None


# ── Prune (#553): solution-bounded detection + gated deletion ────────────────
#
# A prune-candidate is a component that is a MEMBER OF THE TARGET SOLUTION but is
# not declared in the spec — limited to the six prune-eligible kinds below.
# Solution membership bounds the blast radius: prune can never reach a component
# outside the solution apply manages. Every other component type a solution can
# hold (option sets, relationships, plug-in types/assemblies, forms) is out of
# scope. Detection runs only under --prune or --dry-run (see apply_spec).

# Top-level kinds resolved straight from a solution objectid:
# componenttype -> (record-path template keyed by objectid, name column, kind).
_PRUNE_TOPLEVEL: dict[int, tuple[str, str, str]] = {
    1:  ("EntityDefinitions({id})",         "LogicalName", "entity"),
    20: ("roles({id})",                     "name",        "security-role"),
    61: ("webresourceset({id})",            "name",        "webresource"),
    92: ("sdkmessageprocessingsteps({id})", "name",        "plugin-step"),
}
# Entity-scoped kinds — diffed per declared entity, not globally, because their
# names are unique only within an owning entity.
_PRUNE_ATTRIBUTE_TYPE = 2
_PRUNE_VIEW_TYPE = 26
# Deleting these destroys row data, so they need an extra --allow-data-loss force.
_DATA_BEARING_PRUNE = {"entity", "attribute"}


def _prune_candidates(
    backend: D365Backend, spec: dict[str, Any], solution_name: str,
) -> list[dict[str, Any]]:
    """Solution members absent from the spec (the six prune-eligible kinds).

    Returns internal candidate dicts ``{kind, name, ref, entity}`` where ``ref``
    is the id/logical name the per-kind deleter needs and ``entity`` is the owning
    entity's logical name (attributes only). Read-only: every call is a GET, so it
    runs unchanged under --dry-run.
    """
    by_type: dict[int, set[str]] = {}
    for comp in sol_mod.solution_components(backend, solution_name):
        ct, oid = comp.get("componenttype"), comp.get("objectid")
        if isinstance(ct, int) and isinstance(oid, str):
            by_type.setdefault(ct, set()).add(oid.lower())

    out: list[dict[str, Any]] = []

    # Top-level kinds: resolve each in-solution objectid to its name; keep the
    # ones the spec does not declare. Names are matched case-INSENSITIVELY: the
    # Web API's `name eq` is case-insensitive, so the create/reconcile phase would
    # already have matched a differently-cased declared component — prune must use
    # the same loose match or it would treat a *declared* component as an extra and
    # delete it.
    declared_top: dict[str, set[str]] = {
        "entity": {e["schema_name"].lower() for e in _as_list(spec.get("entities"))},
        "security-role": {r["name"].lower() for r in _as_list(spec.get("security_roles"))},
        "webresource": {w["name"].lower() for w in _as_list(spec.get("webresources"))},
        "plugin-step": {s["name"].lower() for p in _as_list(spec.get("plugins"))
                        for s in _as_list(p.get("steps"))},
    }
    # Resolve every in-solution objectid's name in one $batch instead of one GET
    # per candidate (issue #703). `top_specs` keeps (kind, name_col, oid) in the
    # deterministic order the reads are issued so each result maps back to its
    # candidate. $batch is short-circuited under --dry-run and refused on a
    # read-only profile, so in both cases the reads run directly (pure GETs).
    top_specs: list[tuple[str, str, str, str]] = []  # (kind, name_col, oid, url)
    for ct, (path_tmpl, name_col, kind) in _PRUNE_TOPLEVEL.items():
        for oid in sorted(by_type.get(ct, set())):
            top_specs.append(
                (kind, name_col, oid, f"{path_tmpl.format(id=oid)}?$select={name_col}"))

    top_rows: list[dict[str, Any]] = []
    if backend.dry_run or backend.read_only:
        top_rows = [as_dict(backend.get(url)) for _, _, _, url in top_specs]
    else:
        ops: list[BatchOperation] = [
            {"method": "GET", "url": url} for _, _, _, url in top_specs
        ]
        # Fail-fast: continue-on-error off so the server stops at the first
        # failing read and we never iterate past it.
        for res in run_batched(backend, ops, continue_on_error=False):
            err = res.get("error")
            if err:
                # A failing name-resolution read aborts planning, exactly as the
                # per-request `backend.get` did (fail-fast, not partial prune).
                raise D365Error(str(err), status=res.get("status"))
            body = res.get("body")
            top_rows.append(body if isinstance(body, dict) else {})

    for (kind, name_col, oid, _url), row in zip(top_specs, top_rows):
        name = row.get(name_col)
        if isinstance(name, str) and name and name.lower() not in declared_top[kind]:
            # The id-keyed deleters (role/webresource/step) take the objectid;
            # delete_entity takes the logical name, which IS this name.
            ref = name if kind == "entity" else oid
            out.append({"kind": kind, "name": name, "ref": ref, "entity": None})

    # Entity-scoped kinds: only entities the spec declares, and only collections
    # it declares (presence of the key = the spec is authoritative over it, so an
    # entity with no `attributes:`/`views:` key never has its children pruned).
    attr_ids = by_type.get(_PRUNE_ATTRIBUTE_TYPE, set())
    view_ids = by_type.get(_PRUNE_VIEW_TYPE, set())
    for ent in _as_list(spec.get("entities")):
        logical = ent["schema_name"].lower()
        if attr_ids and isinstance(ent.get("attributes"), list):
            declared = {a["schema_name"].lower() for a in _as_list(ent.get("attributes"))}
            for attr in meta_mod.list_attributes(backend, logical):
                mid = str(attr.get("MetadataId") or "").lower()
                lname = attr.get("LogicalName")
                if (attr.get("IsCustomAttribute") and mid in attr_ids
                        and isinstance(lname, str) and lname.lower() not in declared):
                    out.append({"kind": "attribute", "name": lname,
                                "ref": lname, "entity": logical})
        if view_ids and isinstance(ent.get("views"), list):
            declared = {v["name"].lower() for v in _as_list(ent.get("views"))}
            for view in views_mod.read_entity_views(backend, logical):
                vid = str(view.get("savedqueryid") or "").lower()
                vname = view.get("name")
                if (vid in view_ids and isinstance(vname, str)
                        and vname and vname.lower() not in declared):
                    out.append({"kind": "view", "name": vname, "ref": vid, "entity": None})
    return out


def _prune_delete(backend: D365Backend, cand: dict[str, Any]) -> None:
    """Delete one prune candidate through the per-kind core deleter."""
    kind = cand["kind"]
    if kind == "entity":
        meta_mod.delete_entity(backend, cand["ref"])
    elif kind == "attribute":
        attrs_mod.delete_attribute(backend, cand["entity"], cand["ref"])
    elif kind == "webresource":
        wr_mod.delete_webresource(backend, cand["ref"])
    elif kind == "plugin-step":
        plugin_mod.unregister_step(backend, cand["ref"])
    elif kind == "view":
        backend.delete(f"savedqueries({cand['ref']})")
    elif kind == "security-role":
        backend.delete(f"roles({cand['ref']})")
    else:  # pragma: no cover - kind comes from the closed _PRUNE_* tables
        raise D365Error(f"prune: unsupported kind {kind!r}")


def apply_spec(
    backend: D365Backend,
    spec: dict[str, Any],
    *,
    stage_only: bool = False,
    include_referenced_optionsets: bool = True,
    base_dir: str | None = None,
    prune: bool = False,
    allow_data_loss: bool = False,
) -> dict[str, Any]:
    """Apply a desired-state spec convergently.

    Returns {ok, applied, updated, skipped, replace_blocked, pruned, planned,
    failed, staged}. Existing components are reconciled against the spec —
    `skipped` (matches), `updated` (in-place update of drifted fields), or
    `replace_blocked` (destructive divergence: reported, no write). `ok` is false
    when anything failed or was replace-blocked. See ADR 0014.

    Pruning (#553) is opt-in and solution-bounded. A component that is a member of
    the target solution but is not declared in the spec is a *prune-candidate*
    (one of six kinds: entity, attribute, view, security-role, webresource,
    plugin-step). Detection runs only under `prune` or `backend.dry_run`; a plain
    apply reads no solution components and leaves `pruned` empty. Each `pruned`
    entry is `{kind, name, deleted}` (+ `reason` when refused, + `would_prune`
    under dry-run). With `prune=True` (real run) schema-only extras are deleted;
    data-bearing extras (entity/attribute) are refused unless `allow_data_loss` is
    also set. `prune` requires a target solution. A delete that errors lands in
    `failed` (so `ok` goes false); a reported candidate or refusal does not.

    Under `backend.dry_run` the same reconcile runs read-only — the reads-execute
    rule suppresses every write while live GETs still fire — so the result is a
    full drift report classifying each declared component into the four buckets:
    `planned` (would create), `updated` (would update, each entry carrying a field
    `diff`), `replace_blocked` (unconvergeable divergence), and `pruned`. Nothing
    is written or staged (#550).
    """
    validate_spec(spec)

    applied: list[Entry] = []
    updated: list[Entry] = []
    skipped: list[Entry] = []
    replace_blocked: list[Entry] = []
    planned: list[Entry] = []
    failed: list[Entry] = []
    # Reconcile verdicts ("updated"/"skipped"/"replace_blocked") route here.
    routes: dict[str, list[Entry]] = {
        "updated": updated, "skipped": skipped, "replace_blocked": replace_blocked,
    }
    # Names of resources this run would create but that do not exist yet (dry-run
    # greenfield). Dependents of a planned resource are reported planned without
    # calling their core, which would otherwise network-resolve the missing
    # dependency and raise (publisher id for a solution, MetadataId for a picklist's
    # option set). In a real apply nothing is ever planned, so this stays empty.
    planned_names: set[str] = set()

    # validate_spec (above) guarantees a solution block with unique_name, so the
    # target is always explicit — customization writes never fall back to the
    # system Default Solution silently (#636). --prune is scoped to it.
    sol = spec["solution"]
    solution_name = sol["unique_name"]
    pub = spec.get("publisher")
    pub_id: str | None = None
    entity_logicals: dict[str, str] = {}

    try:
        # Phase: publisher.
        if pub:
            entry: Entry = {"kind": "publisher", "name": pub["unique_name"]}
            result = _call(entry, lambda: sol_mod.create_publisher(
                backend,
                name=pub["unique_name"],
                friendly_name=pub.get("friendly_name"),
                prefix=pub["prefix"],
                option_value_prefix=pub["option_value_prefix"],
                if_exists="skip",
            ), failed)
            pub_id = result.get("publisherid")
            if _classify(result, entry, applied, skipped, planned) == "planned":
                planned_names.add(pub["unique_name"])

        # Phase: solution (bound to the publisher).
        if sol:
            entry = {"kind": "solution", "name": sol["unique_name"]}
            if pub and pub["unique_name"] in planned_names:
                planned.append(entry)
            elif not pub and backend.dry_run:
                # No publisher to build the bind: create_solution resolves a publisher
                # before its dry-run short-circuit and would raise even when the
                # solution already exists. Probe existence directly instead.
                (skipped if _solution_exists(backend, sol["unique_name"])
                 else planned).append(entry)
            else:
                result = _call(entry, lambda: sol_mod.create_solution(
                    backend,
                    name=sol["unique_name"],
                    friendly_name=sol.get("friendly_name"),
                    version=sol.get("version", "1.0.0.0"),
                    publisher_id=pub_id,
                    publisher_unique_name=pub["unique_name"] if pub else None,
                    if_exists="skip",
                ), failed)
                _classify(result, entry, applied, skipped, planned)

        # Phase: entities. Capture each schema_name -> logical_name for later phases.
        for ent in _as_list(spec.get("entities")):
            entry = {"kind": "entity", "name": ent["schema_name"]}
            result = _call(entry, lambda ent=ent: meta_mod.create_entity(
                backend,
                **REGISTRY["entity"].to_kwargs(ent),
                solution=solution_name,
                if_exists="skip",
            ), failed)
            logical_name: str = result.get("logical_name") or ent["schema_name"].lower()
            entity_logicals[ent["schema_name"]] = logical_name
            if _present(result):
                ctx = ReconcileCtx(solution=solution_name, base_dir=base_dir,
                                   entity_logical=logical_name)
                _reconcile_via_adapter(REGISTRY["entity"], backend, ent, ctx,
                                       entry, routes, failed)
            elif _classify(result, entry, applied, skipped, planned) == "planned":
                planned_names.add(logical_name)

        # Phase: global option sets (before the attributes that reference them).
        # Track names that were created (or planned in dry-run) to skip them in
        # the solution-component phase below (they already carry the solution header).
        os_created: set[str] = set()
        for os_spec in _as_list(spec.get("optionsets")):
            entry = {"kind": "optionset", "name": os_spec["name"]}
            result = _call(entry, lambda os_spec=os_spec: os_mod.create_optionset(
                backend,
                **REGISTRY["optionset"].to_kwargs(os_spec),
                is_global=True,
                solution=solution_name,
                if_exists="skip",
            ), failed)
            if _present(result):
                # Pre-existing: reconcile (insert missing options). Left out of
                # os_created so the solution-component phase still ensures membership.
                ctx = ReconcileCtx(solution=solution_name, base_dir=base_dir)
                _reconcile_via_adapter(REGISTRY["optionset"], backend, os_spec, ctx,
                                       entry, routes, failed)
                continue
            bucket = _classify(result, entry, applied, skipped, planned)
            if bucket == "planned":
                planned_names.add(os_spec["name"])
            if bucket in ("applied", "planned"):
                os_created.add(os_spec["name"])

        # Phase: ensure referenced global option sets are solution components (#146e).
        # create_optionset adds a NEWLY created set to the solution via the
        # MSCRM.SolutionUniqueName POST header, but a pre-existing global it skips
        # is never made a member. Add each referenced set explicitly so a picklist's
        # option set is not silently absent from the built solution. Default ON.
        if include_referenced_optionsets and solution_name:
            for os_spec in _as_list(spec.get("optionsets")):
                os_name: str = os_spec["name"]
                if os_name in os_created:
                    continue  # created this run: MSCRM.SolutionUniqueName header handled it
                comp_entry: dict[str, Any] = {"kind": "solution-component", "name": os_name}
                if backend.dry_run:
                    planned.append(comp_entry)
                    continue
                try:
                    raw = as_dict(backend.get(
                        f"GlobalOptionSetDefinitions(Name='{os_name}')",
                        params={"$select": "MetadataId"},
                    ))
                    metadata_id = raw.get("MetadataId")
                    if not isinstance(metadata_id, str) or not metadata_id:
                        raise D365Error(
                            f"option set {os_name!r} has no MetadataId; cannot add to solution."
                        )
                    sol_mod.add_solution_component(
                        backend,
                        solution=solution_name,
                        component_id=metadata_id,
                        component_type=sol_mod.SOLUTION_COMPONENT_TYPES["optionset"],
                    )
                    # Report as skipped (not applied): the optionset pre-existed so
                    # we cannot tell without an extra GET whether it was already a
                    # solution member. Reporting applied would trigger publish and
                    # show a change on every re-apply even when nothing changed.
                    skipped.append(comp_entry)
                except D365Error as exc:
                    # Best-effort: a membership-add failure must not abort an apply
                    # whose entities/attrs already landed. Record, do not raise.
                    failed.append({**comp_entry, "error": str(exc)})

        # Phase: attributes (across all entities; lookups delegate to a relationship).
        for ent in _as_list(spec.get("entities")):
            logical: str = entity_logicals.get(ent["schema_name"]) or ent["schema_name"].lower()
            for attr in _as_list(ent.get("attributes")):
                entry = {"kind": "attribute", "name": attr["schema_name"]}
                deps: set[str] = {logical}
                if attr.get("optionset_name"):
                    deps.add(attr["optionset_name"])
                if attr["kind"] == "lookup" and attr.get("target_entity"):
                    deps.add(attr["target_entity"])
                if deps & planned_names:
                    planned.append(entry)
                    continue
                result = _call(
                    entry,
                    lambda attr=attr, logical=logical: attrs_mod.add_attribute(
                        backend,
                        **REGISTRY["attribute"].to_kwargs(attr),
                        entity=logical,
                        solution=solution_name,
                        if_exists="skip",
                    ), failed)
                if _present(result):
                    ctx = ReconcileCtx(solution=solution_name, base_dir=base_dir,
                                       entity_logical=logical)
                    _reconcile_via_adapter(REGISTRY["attribute"], backend, attr, ctx,
                                           entry, routes, failed)
                else:
                    _classify(result, entry, applied, skipped, planned)

        # Phase: explicit relationships (both entities exist by now).
        for ent in _as_list(spec.get("entities")):
            for rel in _as_list(ent.get("relationships")):
                entry = {"kind": "relationship", "name": rel["schema_name"]}
                if {rel["referenced_entity"], rel["referencing_entity"]} & planned_names:
                    planned.append(entry)
                    continue
                result = _call(entry, lambda rel=rel: rel_mod.create_one_to_many(
                    backend,
                    **REGISTRY["relationship"].to_kwargs(rel),
                    solution=solution_name,
                    if_exists="skip",
                ), failed)
                if _present(result):
                    ctx = ReconcileCtx(solution=solution_name, base_dir=base_dir)
                    _reconcile_via_adapter(REGISTRY["relationship"], backend, rel, ctx,
                                           entry, routes, failed)
                else:
                    _classify(result, entry, applied, skipped, planned)

        # Phase: views. ObjectTypeCode is resolved once per entity; when it is not
        # yet readable (greenfield pre-publish) the views are planned, not failed.
        for ent in _as_list(spec.get("entities")):
            views = _as_list(ent.get("views"))
            if not views:
                continue
            logical_v: str = entity_logicals.get(ent["schema_name"]) or ent["schema_name"].lower()
            try:
                otc = _resolve_otc(backend, logical_v)
            except D365Error as exc:
                for view in views:
                    failed.append({"kind": "view", "name": view["name"], "error": str(exc)})
                raise _Aborted from exc
            for view in views:
                entry = {"kind": "view", "name": view["name"]}
                if otc is None:
                    planned.append(entry)
                    continue
                result = _call(
                    entry,
                    lambda view=view, logical_v=logical_v, otc=otc: views_mod.create_view(
                        backend,
                        **REGISTRY["view"].to_kwargs(view),
                        entity=logical_v,
                        object_type_code=otc,
                        solution=solution_name,
                        if_exists="skip",
                    ), failed)
                if _present(result):
                    ctx = ReconcileCtx(solution=solution_name, base_dir=base_dir,
                                       entity_logical=logical_v, object_type_code=otc)
                    _reconcile_via_adapter(REGISTRY["view"], backend, view, ctx,
                                           entry, routes, failed)
                else:
                    _classify(result, entry, applied, skipped, planned)

        # Phase: web resources. No if_exists on the core, so the adapter's find_live
        # probes existence directly (forced-real, dry-run safe): absent ⇒ create,
        # present ⇒ reconcile the already-probed live (a single read — unlike the
        # if_exists='skip' kinds, whose create probes and reconcile re-reads). Create
        # and update defer publishing — the end-of-run PublishAllXml publishes once.
        for wr in _as_list(spec.get("webresources")):
            name: str = wr["name"]
            entry = {"kind": "webresource", "name": name}
            ctx = ReconcileCtx(solution=solution_name, base_dir=base_dir)
            live_wr = _call(entry, lambda wr=wr, ctx=ctx:
                            _find_live_webresource(backend, wr, ctx), failed)
            if live_wr is None:
                content = _call(entry, lambda wr=wr: _webresource_content(base_dir, wr),
                                failed)
                result = _call(entry, lambda wr=wr, content=content:
                               wr_mod.create_webresource(
                                   backend,
                                   **REGISTRY["webresource"].to_kwargs(wr),
                                   content=content,
                                   webresourcetype=wr_mod.resolve_webresourcetype(
                                       wr.get("file") or "", wr.get("webresourcetype")),
                                   solution=solution_name,
                                   publish=False,
                               ), failed)
                _classify(result, entry, applied, skipped, planned)
            else:
                _reconcile(entry, lambda wr=wr, live_wr=live_wr, ctx=ctx, entry=entry:
                           _reconcile_webresource(backend, wr, live_wr, ctx, entry),
                           failed, routes)

        # Phase: security roles. Create (if_exists=skip) then reconcile privileges
        # to the declared set. A fresh role gets the declared set applied; an
        # existing role is reconciled by _reconcile_security_role (convergent subset
        # satisfaction — a replace drops removable extras but the platform keeps an
        # immovable baseline). Roles are not publishable, so they never trigger the
        # end-of-run publish.
        for role_spec in _as_list(spec.get("security_roles")):
            entry = {"kind": "security-role", "name": role_spec["name"]}
            ctx = ReconcileCtx(solution=solution_name, base_dir=base_dir)
            # Uniform probe→create/reconcile: find_live reads the live role id.
            # Present ⇒ reconcile its privileges to the declared set; absent ⇒ create
            # the role (create_role only seeds it — it has no reconcile of its own)
            # and apply the declared set. The probe is a read, so it runs under
            # dry-run too; a greenfield role is then reported planned.
            role_id = _call(entry, lambda role_spec=role_spec, ctx=ctx:
                            _find_live_security_role(backend, role_spec, ctx), failed)
            if role_id is not None:
                _reconcile(entry, lambda role_spec=role_spec, role_id=role_id, ctx=ctx,
                           entry=entry: _reconcile_security_role(
                               backend, role_spec, role_id, ctx, entry),
                           failed, routes)
                continue
            if backend.dry_run:
                planned.append(entry)  # greenfield: role + privileges would be created
                continue
            # Absent at probe time: create the role (if_exists='skip' still guards a
            # create race — a concurrent apply may have created it between the probe
            # and this call). On that race create_role reports `existed`, so fall back
            # to reconcile (subset-satisfaction) rather than authoritatively replacing
            # its privileges, matching the present-branch semantics above. Otherwise
            # ReplacePrivilegesRole drops the removable default privileges and applies
            # the declared ones; the platform's immovable baseline stays.
            result = _call(entry, lambda role_spec=role_spec: sec_mod.create_role(
                backend,
                **REGISTRY["security-role"].to_kwargs(role_spec),
                if_exists="skip",
                solution=solution_name,
            ), failed)
            role_id = result["roleid"]
            if result.get("existed"):
                _reconcile(entry, lambda role_spec=role_spec, role_id=role_id, ctx=ctx,
                           entry=entry: _reconcile_security_role(
                               backend, role_spec, role_id, ctx, entry),
                           failed, routes)
                continue
            desired = _call(entry, lambda role_spec=role_spec:
                            _desired_role_privileges(backend, role_spec)[0], failed)
            _call(entry, lambda role_id=role_id, desired=desired:
                  sec_mod.replace_role_privileges(backend, role_id, desired), failed)
            applied.append(entry)

        # Phase: plug-ins. A declared plug-in is compound: the assembly is the
        # top-level block (registry-driven, probe→create/reconcile like a web
        # resource — no if_exists on the core) and its steps nest under it
        # (registry-driven, like attributes under an entity). Its plug-in types and
        # a step's entity images are create-only sub-collections with no reconcile,
        # so they stay inline sequencing rather than registry kinds. On-prem
        # extensibility is provisioned from the spec (#552); apply orchestrates and
        # diffs through the plugin core, it does not reimplement registration.
        # Plug-in components are not publishable (see _NON_PUBLISHABLE below).
        for plugin in _as_list(spec.get("plugins")):
            name = _assembly_name(plugin)
            asm_path = os.path.join(base_dir or "", plugin["file"])
            asm_entry = {"kind": "plugin-assembly", "name": name}
            ctx = ReconcileCtx(solution=solution_name, base_dir=base_dir)

            # Assembly: probe → absent ⇒ register, present ⇒ reconcile content drift.
            live_asm = _call(asm_entry, lambda plugin=plugin, ctx=ctx:
                             _find_live_plugin_assembly(backend, plugin, ctx), failed)
            assembly_planned = False
            assembly_created = False
            if live_asm is None:
                result = _call(asm_entry, lambda plugin=plugin:
                               plugin_mod.register_assembly(
                                   backend,
                                   **REGISTRY["plugin-assembly"].to_kwargs(plugin),
                                   path=asm_path, name=name, solution=solution_name,
                                   update=False), failed)
                bucket = _classify(result, asm_entry, applied, skipped, planned)
                assembly_planned = bucket == "planned"
                assembly_created = bucket == "applied"
            else:
                _reconcile(asm_entry, lambda plugin=plugin, live_asm=live_asm, ctx=ctx:
                           _reconcile_plugin_assembly(
                               backend, plugin, live_asm, ctx, asm_entry),
                           failed, routes)

            # Types (create-only, nested). A just-created assembly has none, so
            # register each declared type directly; a pre-existing one is listed
            # once to skip what it already has.
            live_typenames: set[str] | None = None
            for typ in _as_list(plugin.get("types")):
                t_entry: Entry = {"kind": "plugin-type", "name": typ["type_name"]}
                if assembly_planned:
                    planned.append(t_entry)
                    continue
                if not assembly_created:
                    if live_typenames is None:
                        listing = _call(t_entry, lambda: plugin_mod.list_types(
                            backend, assembly=name), failed)
                        live_typenames = {str(r.get("typename"))
                                          for r in listing.get("value", [])}
                    if typ["type_name"] in live_typenames:
                        skipped.append(t_entry)
                        continue
                result = _call(t_entry, lambda typ=typ: plugin_mod.register_type(
                    backend, assembly=name, type_name=typ["type_name"],
                    friendly_name=typ.get("friendly_name"), solution=solution_name), failed)
                _classify(result, t_entry, applied, skipped, planned)

            # Steps (registry-driven, nested) with their images (create-only).
            for step in _as_list(plugin.get("steps")):
                s_entry: Entry = {"kind": "plugin-step", "name": step["name"]}
                if assembly_planned:
                    planned.append(s_entry)
                    for img in _as_list(step.get("images")):
                        planned.append({"kind": "plugin-image", "name": img["alias"]})
                    continue
                live_step = _call(s_entry, lambda step=step, ctx=ctx:
                                  _find_live_plugin_step(backend, step, ctx), failed)
                step_id: str | None = None
                step_blocked = False
                if live_step is None:
                    result = _call(s_entry, lambda step=step: plugin_mod.register_step(
                        backend, **REGISTRY["plugin-step"].to_kwargs(step),
                        assembly=name, solution=solution_name), failed)
                    _classify(result, s_entry, applied, skipped, planned)
                    step_id = result.get("sdkmessageprocessingstepid")  # None under dry-run
                else:
                    # A step reconcile needs its verdict here (a replace-blocked step
                    # leaves its image subtree untouched), so drive the adapter's
                    # reconcile directly rather than through the routing wrapper.
                    try:
                        verdict, payload = _reconcile_plugin_step(
                            backend, step, live_step, ctx, s_entry)
                    except D365Error as exc:
                        failed.append({**s_entry, "error": str(exc)})
                        raise _Aborted from exc
                    routes[verdict].append(payload)
                    step_id = str(live_step["sdkmessageprocessingstepid"])
                    step_blocked = verdict == "replace_blocked"

                for img in _as_list(step.get("images")):
                    img_entry: Entry = {"kind": "plugin-image", "name": img["alias"]}
                    if step_blocked:
                        continue  # blocked step: leave its images until it is recreated
                    if step_id is None:
                        planned.append(img_entry)  # dry-run: step would be created → image too
                        continue
                    existing_img = _call(
                        img_entry, lambda img=img, step_id=step_id:
                        plugin_mod.find_step_image(backend, step_id, img["alias"]), failed)
                    if existing_img is not None:
                        skipped.append(img_entry)
                        continue
                    result = _call(img_entry, lambda img=img, step_id=step_id:
                                   plugin_mod.register_image(
                                       backend, step=step_id, image_type=img["image_type"],
                                       alias=img["alias"], attributes=img.get("attributes"),
                                       name=img.get("name"),
                                       message_property_name=img.get("message_property_name"),
                                       solution=solution_name), failed)
                    _classify(result, img_entry, applied, skipped, planned)
    except _Aborted:
        pass

    # Phase: prune (#553). Solution-bounded, opt-in, gated. Detection (read-only)
    # runs under --prune or --dry-run; a plain apply skips it entirely. It needs
    # the target solution to already exist — a greenfield run (the solution is
    # created this pass, or, under dry-run, not at all) has no members to prune.
    # Deletes are suppressed when the convergence itself failed — a partial-failure
    # run must not also start deleting org-extras.
    pruned: list[Entry] = []
    if (solution_name and (prune or backend.dry_run)
            and _solution_exists(backend, solution_name)):
        suppressed = bool(failed or replace_blocked)
        for cand in _prune_candidates(backend, spec, solution_name):
            kind, name = cand["kind"], cand["name"]
            data_bearing = kind in _DATA_BEARING_PRUNE
            would_delete = prune and (not data_bearing or allow_data_loss)
            if backend.dry_run:
                entry: Entry = {"kind": kind, "name": name, "deleted": False}
                # Mirror the real run exactly: it would delete only when not
                # suppressed by a failed/replace-blocked convergence.
                if would_delete and not suppressed:
                    entry["would_prune"] = True
                elif prune and data_bearing and not allow_data_loss:
                    entry["reason"] = "data-bearing; pass --allow-data-loss to delete"
                pruned.append(entry)
            elif prune and data_bearing and not allow_data_loss:
                pruned.append({"kind": kind, "name": name, "deleted": False,
                               "reason": "data-bearing; pass --allow-data-loss to delete"})
            elif would_delete and not suppressed:
                try:
                    _prune_delete(backend, cand)
                    pruned.append({"kind": kind, "name": name, "deleted": True})
                except D365Error as exc:
                    failed.append({"kind": kind, "name": name, "error": str(exc)})
            else:
                pruned.append({"kind": kind, "name": name, "deleted": False})

    # Publish ONCE at the end. The per-resource cores were all called with their
    # default publish=False, so nothing published mid-run. Skip when staging, on a
    # dry run, on any failure (hard error or replace-blocked divergence), or when
    # nothing was actually written (created or updated). Under --dry-run `applied`
    # is always empty and `updated` is a would-update preview (writes suppressed),
    # so nothing was written — `wrote` is false and the run never stages.
    # Security roles and plug-in components are not publishable customizations; a
    # change to them writes but needs no PublishAllXml. Gate publish/staged on the
    # publishable writes only.
    publishable = [e for e in applied + updated if e.get("kind") not in _NON_PUBLISHABLE]
    wrote = bool(publishable) and not backend.dry_run
    published = (wrote and not stage_only and not failed
                 and not replace_blocked and not backend.dry_run)
    if published:
        sol_mod.publish_all(backend)

    return {
        "ok": not failed and not replace_blocked,
        "applied": applied,
        "updated": updated,
        "skipped": skipped,
        "replace_blocked": replace_blocked,
        "pruned": pruned,
        "planned": planned,
        "failed": failed,
        "staged": wrote and not published,
    }
