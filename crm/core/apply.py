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
from typing import Any, TypeVar, cast

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
    for key in ("entities", "optionsets", "webresources", "security_roles", "plugins"):
        if key in sp and not isinstance(sp[key], list):
            raise D365Error(f"{key} must be a list.")
    if not (sp.get("publisher") or sp.get("solution") or sp.get("entities")
            or sp.get("optionsets") or sp.get("webresources")
            or sp.get("security_roles") or sp.get("plugins")):
        raise D365Error("spec is empty: nothing to apply.")
    for ent in _as_list(sp.get("entities")):
        _require(ent, ("schema_name", "display_name"), "entity")
        elabel = f"entity {ent['schema_name']!r}"
        # Validate ownership up front so a typo fails cleanly here rather than being
        # misclassified as a destructive ownership change during reconciliation.
        if ent.get("ownership") is not None:
            mc.validate_ownership(ent["ownership"], subject=f"{elabel} ownership")
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
            # max_length is compared numerically during reconciliation (grow check);
            # a quoted/non-int value from the spec must fail here, not blow up later.
            if attr.get("max_length") is not None and not isinstance(attr["max_length"], int):
                raise D365Error(
                    f"attribute {name!r}: max_length must be an integer "
                    "(unquoted in YAML).")
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
    for wr in _as_list(sp.get("webresources")):
        _require(wr, ("name", "file"), "web resource")
        # name/file/display_name reach os.path.join + the Web API as strings; a
        # non-string (e.g. an unquoted YAML number) must fail here with a clean
        # D365Error, not blow up later inside os.path.join.
        for key in ("name", "file", "display_name"):
            if wr.get(key) is not None and not isinstance(wr[key], str):
                raise D365Error(f"web resource {wr.get('name')!r}: {key} must be a string.")
        if wr.get("webresourcetype") is not None and not isinstance(wr["webresourcetype"], int):
            raise D365Error(
                f"web resource {wr['name']!r}: webresourcetype must be an integer.")
        # Fail fast (before any HTTP) when no type is given and it can't be inferred
        # from the extension — otherwise the create raises mid-run, after earlier
        # components have already landed. Raises D365Error on an unknown extension.
        wr_mod.resolve_webresourcetype(wr["file"], wr.get("webresourcetype"))
    for role in _as_list(sp.get("security_roles")):
        _require(role, ("name",), "security role")
        if not isinstance(role["name"], str):
            raise D365Error("security role: name must be a string.")
        rlabel = f"security role {role['name']!r}"
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
    for plugin in _as_list(sp.get("plugins")):
        _require(plugin, ("file",), "plug-in")
        if not isinstance(plugin["file"], str):
            raise D365Error("plug-in: file must be a string.")
        if plugin.get("assembly") is not None and not isinstance(plugin["assembly"], str):
            raise D365Error("plug-in: assembly must be a string.")
        plabel = f"plug-in {plugin.get('assembly') or plugin['file']!r}"
        for sub in ("types", "steps"):
            _require_list(plugin, sub, plabel)
        for typ in _as_list(plugin.get("types")):
            _require(typ, ("type_name",), f"{plabel} type")
            if not isinstance(typ["type_name"], str):
                raise D365Error(f"{plabel}: type type_name must be a string.")
        for step in _as_list(plugin.get("steps")):
            _require(step, ("name", "message", "plugin_type"), f"{plabel} step")
            # Every string-typed step field reaches the Web API as a string; an
            # unquoted YAML scalar must fail here, not blow up mid-apply.
            for key in ("name", "message", "plugin_type", "entity", "stage", "mode",
                        "filtering_attributes", "configuration"):
                if step.get(key) is not None and not isinstance(step[key], str):
                    raise D365Error(f"{plabel}: step {key!r} must be a string.")
            if step.get("rank") is not None and not isinstance(step["rank"], int):
                raise D365Error(f"{plabel}: step rank must be an integer (unquoted in YAML).")
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
    out = meta_update_mod.update_entity(backend, logical, solution=solution, **changes)
    return "updated", _with_diff(entry, out)


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
    out = meta_update_mod.update_attribute(backend, entity_logical, attr_logical,
                                           solution=solution, **changes)
    return "updated", _with_diff(entry, out)


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
    out = os_mod.update_optionset(backend, os_spec["name"], insert=inserts, solution=solution)
    return "updated", _with_diff(entry, out)


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


def _reconcile_webresource(
    backend: D365Backend, wr: dict[str, Any], content: bytes, live: dict[str, Any],
    solution: str | None, entry: Entry,
) -> _Verdict:
    """Diff an existing web resource against the spec; update content/display or skip.

    The spec's file bytes are base64-compared against the live `content` column;
    a declared `display_name` is compared too. There is no destructive divergence
    for a web resource, so this never blocks. The update defers publishing
    (publish=False) — apply publishes once at the end.
    """
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
        display_name=changes.get("display_name"), solution=solution, publish=False)
    return "updated", {**entry, "diff": {"fields": sorted(changes)}}


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


def _reconcile_security_role(
    backend: D365Backend, role_spec: dict[str, Any], role_id: str, entry: Entry,
) -> _Verdict:
    """Reconcile an existing role's privileges to the declared set.

    The role's live privileges (RetrieveRolePrivilegesRole) are compared to the
    declared matrix by (privilege id -> depth). When every declared privilege is
    already present at its declared depth it is a no-op; otherwise
    ReplacePrivilegesRole sets the role to the declared set — authoritative within
    the declared role, so unlisted *removable* privileges are dropped (distinct
    from `--prune`, which is about whole components). There is no destructive
    divergence, so this never blocks; the replace write is suppressed under dry-run
    (reads-execute rule), so the same path yields a drift report.

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


def _reconcile_plugin_assembly(
    backend: D365Backend, name: str, content: bytes, asm_path: str,
    live: dict[str, Any], solution: str | None, entry: Entry,
) -> _Verdict:
    """Diff an existing plug-in assembly against the rebuilt DLL; update or skip.

    The spec file's bytes are base64-compared against the live `content` column;
    on drift the assembly content is PATCHed in place (register_assembly's update
    path). There is no destructive divergence for an assembly, so this never
    blocks. The update is suppressed under dry-run (reads-execute rule).
    """
    desired_b64 = base64.b64encode(content).decode("ascii")
    if live.get("content") == desired_b64:
        return "skipped", entry
    plugin_mod.register_assembly(
        backend, path=asm_path, name=name, update=True, solution=solution)
    return "updated", {**entry, "diff": {"fields": ["content"]}}


def _reconcile_plugin_step(
    backend: D365Backend, step: dict[str, Any], live: dict[str, Any],
    solution: str | None, entry: Entry,
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
        solution=solution, **changes)
    return "updated", {**entry, "diff": {"fields": sorted(changes)}}


def _apply_plugin(
    backend: D365Backend, plugin: dict[str, Any], *,
    solution: str | None, base_dir: str | None,
    applied: list[Entry], skipped: list[Entry], planned: list[Entry],
    failed: list[Entry], routes: dict[str, list[Entry]],
) -> None:
    """Apply one declared plug-in: assembly, then its types, steps, and images.

    Reuses the plugin core (register_assembly with content PATCH on update,
    register_type / register_step / register_image) and diffs each component
    convergently. A fresh content-only assembly carries zero plug-in types, so
    when this run just created the assembly every declared type is registered
    without a per-type existence probe; a pre-existing assembly is listed once to
    skip the types it already has. Under dry-run a greenfield assembly does not
    resolve, so its whole subtree is reported `planned` without calling a core
    that would network-resolve the missing assembly and raise. D365Error in any
    core call aborts the whole apply (the _Aborted contract).
    """
    name: str = plugin.get("assembly") or os.path.splitext(
        os.path.basename(plugin["file"]))[0]
    asm_path = os.path.join(base_dir or "", plugin["file"])
    asm_entry: Entry = {"kind": "plugin-assembly", "name": name}

    live_asm = _call(asm_entry, lambda: plugin_mod.find_assembly(backend, name), failed)
    assembly_planned = False
    assembly_created = False
    if live_asm is None:
        result = _call(asm_entry, lambda: plugin_mod.register_assembly(
            backend, path=asm_path, name=name,
            isolation_mode=plugin.get("isolation_mode", "sandbox"),
            version=plugin.get("version"), culture=plugin.get("culture"),
            public_key_token=plugin.get("public_key_token"),
            description=plugin.get("description"),
            solution=solution, update=False), failed)
        bucket = _classify(result, asm_entry, applied, skipped, planned)
        assembly_planned = bucket == "planned"
        assembly_created = bucket == "applied"
    else:
        content = _call(asm_entry, lambda: _read_file_bytes(base_dir, plugin["file"]),
                        failed)
        _reconcile(asm_entry, lambda: _reconcile_plugin_assembly(
            backend, name, content, asm_path, live_asm, solution, asm_entry),
            failed, routes)

    # Types. A just-created assembly has none, so register each declared type
    # directly; a pre-existing one is listed once to skip what it already has.
    live_typenames: set[str] | None = None
    for typ in _as_list(plugin.get("types")):
        t_entry: Entry = {"kind": "plugin-type", "name": typ["type_name"]}
        if assembly_planned:
            planned.append(t_entry)
            continue
        if not assembly_created:
            if live_typenames is None:
                listing = _call(
                    t_entry, lambda: plugin_mod.list_types(backend, assembly=name), failed)
                live_typenames = {str(r.get("typename"))
                                  for r in listing.get("value", [])}
            if typ["type_name"] in live_typenames:
                skipped.append(t_entry)
                continue
        result = _call(t_entry, lambda typ=typ: plugin_mod.register_type(
            backend, assembly=name, type_name=typ["type_name"],
            friendly_name=typ.get("friendly_name"), solution=solution), failed)
        _classify(result, t_entry, applied, skipped, planned)

    # Steps (with their images). Each step is keyed by its (unique) name.
    for step in _as_list(plugin.get("steps")):
        s_entry: Entry = {"kind": "plugin-step", "name": step["name"]}
        if assembly_planned:
            planned.append(s_entry)
            for img in _as_list(step.get("images")):
                planned.append({"kind": "plugin-image", "name": img["alias"]})
            continue
        live_step = _call(
            s_entry, lambda step=step: plugin_mod.find_step(backend, step["name"]), failed)
        step_id: str | None = None
        step_blocked = False
        if live_step is None:
            result = _call(s_entry, lambda step=step: plugin_mod.register_step(
                backend, message=step["message"], plugin_type=step["plugin_type"],
                entity=step.get("entity"), stage=step.get("stage", "postoperation"),
                mode=step.get("mode", "sync"), rank=step.get("rank", 1),
                filtering_attributes=step.get("filtering_attributes"),
                name=step["name"], configuration=step.get("configuration"),
                assembly=name, solution=solution), failed)
            _classify(result, s_entry, applied, skipped, planned)
            step_id = result.get("sdkmessageprocessingstepid")  # None under dry-run
        else:
            try:
                verdict, payload = _reconcile_plugin_step(
                    backend, step, live_step, solution, s_entry)
            except D365Error as exc:
                failed.append({**s_entry, "error": str(exc)})
                raise _Aborted from exc
            routes[verdict].append(payload)
            step_id = str(live_step["sdkmessageprocessingstepid"])
            step_blocked = verdict == "replace_blocked"

        for img in _as_list(step.get("images")):
            img_entry: Entry = {"kind": "plugin-image", "name": img["alias"]}
            if step_blocked:
                continue  # blocked step: leave its image subtree until it is recreated
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
                               solution=solution), failed)
            _classify(result, img_entry, applied, skipped, planned)


def apply_spec(
    backend: D365Backend,
    spec: dict[str, Any],
    *,
    solution: str | None = None,
    stage_only: bool = False,
    include_referenced_optionsets: bool = True,
    base_dir: str | None = None,
) -> dict[str, Any]:
    """Apply a desired-state spec convergently.

    Returns {ok, applied, updated, skipped, replace_blocked, pruned, planned,
    failed, staged}. Existing components are reconciled against the spec —
    `skipped` (matches), `updated` (in-place update of drifted fields), or
    `replace_blocked` (destructive divergence: reported, no write). `ok` is false
    when anything failed or was replace-blocked; `pruned` is reserved (empty) for
    the pruning slice. See ADR 0014.

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
            if _present(result):
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
            if _present(result):
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
                if _present(result):
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

        # Phase: web resources. No if_exists on the core, so probe existence
        # directly (forced-real, dry-run safe); created/updated with publish
        # deferred — the end-of-run PublishAllXml publishes them once.
        for wr in _as_list(spec.get("webresources")):
            name: str = wr["name"]
            entry = {"kind": "webresource", "name": name}
            content = _call(entry, lambda wr=wr: _read_file_bytes(base_dir, wr["file"]),
                            failed)
            live_wr = wr_mod.find_webresource(backend, name)
            if live_wr is None:
                result = _call(entry, lambda wr=wr, name=name, content=content:
                               wr_mod.create_webresource(
                                   backend,
                                   name=name,
                                   content=content,
                                   webresourcetype=wr_mod.resolve_webresourcetype(
                                       wr["file"], wr.get("webresourcetype")),
                                   display_name=wr.get("display_name"),
                                   solution=solution_name,
                                   publish=False,
                               ), failed)
                _classify(result, entry, applied, skipped, planned)
            else:
                _reconcile(entry, lambda wr=wr, content=content, live_wr=live_wr, entry=entry:
                           _reconcile_webresource(backend, wr, content, live_wr,
                                                  solution_name, entry),
                           failed, routes)

        # Phase: security roles. Create (if_exists=skip) then reconcile privileges
        # to the declared set. A fresh role gets the declared set applied; an
        # existing role is reconciled by _reconcile_security_role (convergent subset
        # satisfaction — a replace drops removable extras but the platform keeps an
        # immovable baseline). Roles are not publishable, so they never trigger the
        # end-of-run publish.
        for role_spec in _as_list(spec.get("security_roles")):
            entry = {"kind": "security-role", "name": role_spec["name"]}
            result = _call(entry, lambda role_spec=role_spec: sec_mod.create_role(
                backend,
                role_spec["name"],
                business_unit=role_spec.get("business_unit"),
                if_exists="skip",
                solution=solution_name,
            ), failed)
            if result.get("_dry_run"):
                planned.append(entry)  # greenfield: role + privileges would be created
                continue
            role_id: str = result["roleid"]
            if result.get("existed"):
                _reconcile(entry, lambda role_spec=role_spec, role_id=role_id, entry=entry:
                           _reconcile_security_role(backend, role_spec, role_id, entry),
                           failed, routes)
            else:
                # Freshly created: apply the declared set. ReplacePrivilegesRole drops
                # the removable default privileges and applies the declared ones; the
                # platform's immovable baseline (see _reconcile_security_role) stays.
                desired = _call(entry, lambda role_spec=role_spec:
                                _desired_role_privileges(backend, role_spec)[0], failed)
                _call(entry, lambda role_id=role_id, desired=desired:
                      sec_mod.replace_role_privileges(backend, role_id, desired), failed)
                applied.append(entry)

        # Phase: plug-ins (assembly + types + steps + images). On-prem
        # extensibility is provisioned from the spec (#552); reuses the plugin
        # core — apply orchestrates and diffs, it does not reimplement
        # registration. Plug-in components are not publishable (see below).
        for plugin in _as_list(spec.get("plugins")):
            _apply_plugin(backend, plugin, solution=solution_name, base_dir=base_dir,
                          applied=applied, skipped=skipped, planned=planned,
                          failed=failed, routes=routes)
    except _Aborted:
        pass

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
        "pruned": [],  # populated by the pruning slice (#547); empty here.
        "planned": planned,
        "failed": failed,
        "staged": wrote and not published,
    }
