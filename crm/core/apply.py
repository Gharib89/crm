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

from collections.abc import Callable
from typing import Any, cast

from crm.core import metadata as meta_mod
from crm.core import metadata_attrs as attrs_mod
from crm.core.metadata_attrs import ATTRIBUTE_KINDS
from crm.core import metadata_constraints as mc
from crm.core import metadata_update as meta_update_mod
from crm.core import optionsets as os_mod
from crm.core import relationships as rel_mod
from crm.core import solution as sol_mod
from crm.core import views as views_mod
from crm.utils.d365_backend import D365Backend, D365Error, as_dict, odata_literal

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
    publish, so the caller reports its views as planned-create and a second apply
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
    if sp.get("solution") is not None:
        _require(sp["solution"], ("unique_name",), "solution")
    for key in ("entities", "optionsets"):
        if key in sp and not isinstance(sp[key], list):
            raise D365Error(f"{key} must be a list.")
    if not (sp.get("publisher") or sp.get("solution")
            or sp.get("entities") or sp.get("optionsets")):
        raise D365Error("spec is empty: nothing to apply.")
    for ent in _as_list(sp.get("entities")):
        _require(ent, ("schema_name", "display_name"), "entity")
        elabel = f"entity {ent['schema_name']!r}"
        for sub in ("attributes", "relationships", "views"):
            _require_list(ent, sub, elabel)
        for attr in _as_list(ent.get("attributes")):
            _require(attr, ("kind", "schema_name", "display_name"), "attribute")
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
        for rel in _as_list(ent.get("relationships")):
            _require(rel, ("schema_name", "referenced_entity", "referencing_entity",
                           "lookup_schema", "lookup_display"), "relationship")
        for view in _as_list(ent.get("views")):
            _require(view, ("name", "columns"), "view")
            if not isinstance(view["columns"], list) or not view["columns"]:
                raise D365Error(f"view {view['name']!r}: columns must be a non-empty list.")
            for col in cast("list[Any]", view["columns"]):
                _validate_column(col, view["name"])
    for os_spec in _as_list(sp.get("optionsets")):
        _require(os_spec, ("name", "display_name"), "optionset")
        _require_list(os_spec, "options", f"optionset {os_spec['name']!r}")
        for opt in cast("list[Any]", os_spec.get("options") or []):
            _validate_option(opt, f"optionset {os_spec['name']!r}")


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


def _call(entry: Entry, fn: Callable[[], dict[str, Any]], failed: list[Entry]) -> dict[str, Any]:
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


def _reconcile_entity(
    backend: D365Backend, ent: dict[str, Any], logical: str,
    solution: str | None, entry: Entry,
) -> _Verdict:
    """Diff an existing entity against the spec; update, skip, or block.

    Replace-blocked: an explicit ownership change (Dataverse rejects OwnershipType
    edits post-create — a drop-and-recreate is destructive, so apply refuses and
    writes nothing). Updatable: display name, display-collection name, description.
    """
    live = meta_mod.entity_info(backend, logical)
    desired_ownership = ent.get("ownership")
    live_ownership = live.get("OwnershipType")
    if desired_ownership and live_ownership and desired_ownership != live_ownership:
        return "replace_blocked", {
            **entry,
            "reason": f"ownership change {live_ownership!r} -> {desired_ownership!r} "
                      "requires a destructive drop-and-recreate; refusing (no write).",
        }
    changes: dict[str, Any] = {}
    if _drift(ent.get("display_name"), live.get("DisplayName")):
        changes["display_name"] = ent["display_name"]
    if _drift(ent.get("display_collection_name"), live.get("DisplayCollectionName")):
        changes["display_collection_name"] = ent["display_collection_name"]
    if _drift(ent.get("description"), live.get("Description")):
        changes["description"] = ent["description"]
    if not changes:
        return "skipped", entry
    meta_update_mod.update_entity(backend, logical, solution=solution, **changes)
    return "updated", entry


def _reconcile_attribute(
    backend: D365Backend, attr: dict[str, Any], entity_logical: str,
    solution: str | None, entry: Entry,
) -> _Verdict:
    """Diff an existing attribute against the spec; update, skip, or block.

    Replace-blocked: a data-type change (Dataverse cannot retype a column in place;
    a drop-and-recreate is destructive, so apply refuses and writes nothing).
    Updatable: display name, description, required level, and string max-length
    GROWTH. Lookup/customer kinds are relationship-backed and not reconciled here.
    """
    kind = attr["kind"]
    info = mc.KINDS.get(kind)
    # lookup/customer are relationship-backed (apply's create path delegates them to
    # the relationship builder); reconciling them belongs with the relationship slice.
    if info is None or kind in ("lookup", "customer"):
        return "skipped", entry
    attr_logical = attr["schema_name"].lower()
    # The un-cast attribute projection carries @odata.type and base properties
    # (DisplayName/Description/RequiredLevel) but NOT type-specific ones (MaxLength).
    base = meta_mod.attribute_info(backend, entity_logical, attr_logical)
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
    meta_update_mod.update_attribute(backend, entity_logical, attr_logical,
                                     solution=solution, **changes)
    return "updated", entry


def _reconcile_optionset(
    backend: D365Backend, os_spec: dict[str, Any], solution: str | None, entry: Entry,
) -> _Verdict:
    """Diff an existing global option set; insert spec-declared options it lacks.

    Only options with an explicit value are reconciled — an auto-valued option
    (value omitted) cannot be matched against the live set, so re-applying it would
    insert a duplicate on every run; those are left to the create path. There is no
    destructive divergence for option sets, so this never blocks.
    """
    live = os_mod.get_optionset(backend, os_spec["name"])
    live_options = cast("list[dict[str, Any]]", live.get("Options") or [])
    live_values = {o.get("Value") for o in live_options if isinstance(o.get("Value"), int)}
    inserts: list[tuple[int | None, str]] = [
        (o["value"], o["label"]) for o in _as_list(os_spec.get("options"))
        if isinstance(o.get("value"), int) and o["value"] not in live_values
    ]
    if not inserts:
        return "skipped", entry
    os_mod.update_optionset(backend, os_spec["name"], insert=inserts, solution=solution)
    return "updated", entry


def apply_spec(
    backend: D365Backend,
    spec: dict[str, Any],
    *,
    solution: str | None = None,
    stage_only: bool = False,
    include_referenced_optionsets: bool = True,
) -> dict[str, Any]:
    """Apply a desired-state spec; return {ok, applied, skipped, planned, failed, staged}."""
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

    sol = spec.get("solution")
    solution_name = solution or (sol["unique_name"] if sol else None)
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
            primary: dict[str, Any] = ent.get("primary_attr") or {}
            entry = {"kind": "entity", "name": ent["schema_name"]}
            result = _call(entry, lambda ent=ent, primary=primary: meta_mod.create_entity(
                backend,
                schema_name=ent["schema_name"],
                display_name=ent["display_name"],
                display_collection_name=ent.get("display_collection_name"),
                primary_attr_schema=primary.get("schema_name"),
                primary_attr_label=primary.get("label"),
                ownership=ent.get("ownership", "UserOwned"),
                solution=solution_name,
                if_exists="skip",
            ), failed)
            logical_name: str = result.get("logical_name") or ent["schema_name"].lower()
            entity_logicals[ent["schema_name"]] = logical_name
            if not backend.dry_run and result.get("skipped"):
                _reconcile(entry, lambda ent=ent, logical_name=logical_name:
                           _reconcile_entity(backend, ent, logical_name, solution_name, entry),
                           failed, routes)
            elif _classify(result, entry, applied, skipped, planned) == "planned":
                planned_names.add(logical_name)

        # Phase: global option sets (before the attributes that reference them).
        # Track names that were created (or planned in dry-run) to skip them in
        # the solution-component phase below (they already carry the solution header).
        os_created: set[str] = set()
        for os_spec in _as_list(spec.get("optionsets")):
            options = [(o.get("value"), o["label"]) for o in _as_list(os_spec.get("options"))]
            entry = {"kind": "optionset", "name": os_spec["name"]}
            result = _call(entry, lambda os_spec=os_spec, options=options: os_mod.create_optionset(
                backend,
                name=os_spec["name"],
                display_name=os_spec["display_name"],
                options=options or None,
                is_global=True,
                solution=solution_name,
                if_exists="skip",
            ), failed)
            if not backend.dry_run and result.get("skipped"):
                # Pre-existing: reconcile (insert missing options). Left out of
                # os_created so the solution-component phase still ensures membership.
                _reconcile(entry, lambda os_spec=os_spec, entry=entry:
                           _reconcile_optionset(backend, os_spec, solution_name, entry),
                           failed, routes)
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
                opts = [(o.get("value"), o["label"])
                        for o in _as_list(attr.get("options"))] or None
                result = _call(
                    entry,
                    lambda attr=attr, logical=logical, opts=opts: attrs_mod.add_attribute(
                        backend,
                        entity=logical,
                        kind=attr["kind"],
                        schema_name=attr["schema_name"],
                        display_name=attr["display_name"],
                        description=attr.get("description"),
                        required=attr.get("required", "None"),
                        max_length=attr.get("max_length"),
                        precision=attr.get("precision"),
                        format_name=attr.get("format_name"),
                        optionset_name=attr.get("optionset_name"),
                        options=opts,
                        target_entity=attr.get("target_entity"),
                        solution=solution_name,
                        if_exists="skip",
                    ), failed)
                if not backend.dry_run and result.get("skipped"):
                    _reconcile(entry, lambda attr=attr, logical=logical, entry=entry:
                               _reconcile_attribute(backend, attr, logical, solution_name, entry),
                               failed, routes)
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
                    schema_name=rel["schema_name"],
                    referenced_entity=rel["referenced_entity"],
                    referencing_entity=rel["referencing_entity"],
                    lookup_schema=rel["lookup_schema"],
                    lookup_display=rel["lookup_display"],
                    lookup_required=rel.get("required", "None"),
                    solution=solution_name,
                    if_exists="skip",
                ), failed)
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
                        entity=logical_v,
                        object_type_code=otc,
                        name=view["name"],
                        columns=_columns(view.get("columns")),
                        order_by=view.get("order_by"),
                        is_default=view.get("is_default", False),
                        solution=solution_name,
                        if_exists="skip",
                    ), failed)
                _classify(result, entry, applied, skipped, planned)
    except _Aborted:
        pass

    # Publish ONCE at the end. The per-resource cores were all called with their
    # default publish=False, so nothing published mid-run. Skip when staging, on a
    # dry run, on any failure (hard error or replace-blocked divergence), or when
    # nothing was actually written (created or updated).
    wrote = bool(applied or updated)
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
        "pruned": [],  # populated by the pruning slice (#547); empty here.
        "planned": planned,
        "failed": failed,
        "staged": wrote and not published,
    }
