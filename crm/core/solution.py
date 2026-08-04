"""Solution lifecycle: create-publisher / create / list / info + publish utilities.

This module owns the solution/publisher lifecycle CRUD and the publish actions. The
pure component algebra and the import/export transfer pipeline now live in
`crm.core.solution_components` and `crm.core.solution_transfer` respectively, but
every name they hold is **re-exported here** so the public surface of
`crm.core.solution` is unchanged: `from crm.core.solution import X`,
`crm.core.solution.X`, and `monkeypatch.setattr("crm.core.solution.X", ...)` all
keep resolving for every X that existed before the split.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, cast

from crm.core import dependencies, entity, metadata_cache
from crm.core.batch import run_batched
from crm.core.solution_components import (
    RESOLVE_SPECS as RESOLVE_SPECS,
)
from crm.core.solution_components import (
    ROOT_COMPONENT_BEHAVIORS as ROOT_COMPONENT_BEHAVIORS,
)

# ── Backward-compat re-exports ───────────────────────────────────────────────
#
# Homes changed, the public surface did not. These are deliberate re-exports
# (the redundant `as X` marks them intentional for pyright); callers and tests
# that reach these names via `crm.core.solution.<name>` must keep working. Note:
# a function whose body moved to one of these modules is patched on its NEW home
# module — direct-internal tests for `solution_transfer` privates patch there.
from crm.core.solution_components import (
    SOLUTION_COMPONENT_TYPES as SOLUTION_COMPONENT_TYPES,
)
from crm.core.solution_components import (
    build_audit as build_audit,
)
from crm.core.solution_components import (
    component_key as component_key,
)
from crm.core.solution_components import (
    component_type_name as component_type_name,
)
from crm.core.solution_components import (
    diff_components as diff_components,
)
from crm.core.solution_components import (
    layer_conflicts as layer_conflicts,
)
from crm.core.solution_components import (
    normalize_components as normalize_components,
)
from crm.core.solution_components import (
    root_behavior_name as root_behavior_name,
)
from crm.core.solution_transfer import (
    export_solution as export_solution,
)
from crm.core.solution_transfer import (
    import_result as import_result,
)
from crm.core.solution_transfer import (
    import_solution as import_solution,
)
from crm.core.solution_transfer import (
    parse_import_job_data as parse_import_job_data,
)
from crm.utils.d365_backend import D365Backend, D365Error, as_dict, odata_literal
from crm.utils.d365_types import BatchOperation

# ── Create publisher / solution ─────────────────────────────────────────────
#
# Both mirror appmodule.create_app: a forced-real existence GET (accurate even
# under --dry-run), --if-exists error|skip semantics, then a 204-create via
# entity.create(return_record=False) whose OData-EntityId GUID is synthesised
# into the returned record. on-prem 9.1 publisher/solution contract is verified
# against the op-9-1 docs (customizationprefix 2-8 alnum not 'mscrm';
# customizationoptionvalueprefix 10000-99999; solution publisherid@odata.bind).


def validate_customization_prefix(prefix: str) -> None:
    """Enforce the publisher customizationprefix rules before any HTTP call."""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,7}", prefix):
        raise D365Error(
            "customizationprefix must be 2-8 alphanumeric characters and start "
            f"with a letter; got {prefix!r}."
        )
    if prefix.lower().startswith("mscrm"):
        raise D365Error("customizationprefix must not start with 'mscrm' (reserved).")


def _resolve_publisher_id(backend: D365Backend, unique_name: str) -> str:
    """Look up a publisher's id by uniquename. Raises if it does not exist."""
    pub_id = backend.resolve_id_by_name(
        "publishers",
        filter_field="uniquename",
        id_field="publisherid",
        value=unique_name,
    )
    if pub_id is None:
        raise D365Error(f"Publisher not found: {unique_name}", code="PublisherNotFound")
    return pub_id


def create_publisher(
    backend: D365Backend,
    *,
    name: str,
    friendly_name: str | None = None,
    prefix: str,
    option_value_prefix: int,
    if_exists: str = "error",
) -> dict[str, Any]:
    """Create a solution publisher. Returns `{created, publisherid, ...}`.

    `name` is the uniquename; `friendly_name` defaults to it. `prefix` is the
    customizationprefix and `option_value_prefix` the customizationoptionvalueprefix
    (10000-99999). All semantic validation happens here and raises `D365Error`
    before any POST.
    """
    if not name:
        raise D365Error("name is required.")
    validate_customization_prefix(prefix)
    if not 10000 <= option_value_prefix <= 99999:
        raise D365Error(
            f"option_value_prefix must be in the range 10000-99999; got {option_value_prefix}."
        )
    if if_exists not in ("error", "skip"):
        raise D365Error("if_exists must be 'error' or 'skip'.")

    existing = backend.get_collection(
        "publishers",
        params={
            "$filter": f"uniquename eq {odata_literal(name)}",
            "$select": "publisherid,uniquename",
        },
    )
    if existing and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(f"Publisher {name!r} already exists.", code="AlreadyExists")
        return {
            "skipped": True,
            "exists": True,
            "uniquename": name,
            "publisherid": existing[0].get("publisherid"),
        }

    body: dict[str, Any] = {
        "uniquename": name,
        "friendlyname": friendly_name or name,
        "customizationprefix": prefix,
        "customizationoptionvalueprefix": option_value_prefix,
    }
    result = entity.create(backend, "publishers", body, return_record=False)
    if result.get("_dry_run"):
        result["_exists"] = bool(existing)
        result["would_skip"] = bool(existing) and if_exists == "skip"
        return result
    pub_id = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "uniquename": name,
        "friendlyname": friendly_name or name,
        "customizationprefix": prefix,
        "customizationoptionvalueprefix": option_value_prefix,
        "publisherid": pub_id,
    }
    if not pub_id:
        out["publisher_lookup_error"] = (
            f"Could not parse publisherid from response: {result.get('entity_id_url')!r}"
        )
    return out


def create_solution(
    backend: D365Backend,
    *,
    name: str,
    friendly_name: str | None = None,
    version: str = "1.0.0.0",
    publisher_unique_name: str | None = None,
    publisher_id: str | None = None,
    if_exists: str = "error",
) -> dict[str, Any]:
    """Create an unmanaged solution bound to a publisher. Returns `{created, solutionid, ...}`.

    Exactly one of `publisher_unique_name` / `publisher_id` identifies the publisher;
    a uniquename is resolved to its id with a forced-real GET so a missing publisher
    raises before the solution POST (no orphan). `friendly_name` defaults to `name`,
    `version` to '1.0.0.0'.
    """
    if not name:
        raise D365Error("name is required.")
    if if_exists not in ("error", "skip"):
        raise D365Error("if_exists must be 'error' or 'skip'.")

    existing = backend.get_collection(
        "solutions",
        params={
            "$filter": f"uniquename eq {odata_literal(name)}",
            "$select": "solutionid,uniquename",
        },
    )
    # The skip/error short-circuit below only fires on a real (non-dry) run. Every
    # path that reaches the POST — including the dry-run preview — needs the
    # publisher id to build the bind, so resolve it now unless we already know
    # we'll short-circuit.
    will_short_circuit = bool(existing) and not backend.dry_run
    pub_id = publisher_id
    if not will_short_circuit and not pub_id:
        if not publisher_unique_name:
            raise D365Error("a publisher is required: pass publisher_unique_name or publisher_id.")
        pub_id = _resolve_publisher_id(backend, publisher_unique_name)
    if existing and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(f"Solution {name!r} already exists.", code="AlreadyExists")
        return {
            "skipped": True,
            "exists": True,
            "uniquename": name,
            "solutionid": existing[0].get("solutionid"),
        }

    body: dict[str, Any] = {
        "uniquename": name,
        "friendlyname": friendly_name or name,
        "version": version,
        "publisherid@odata.bind": f"/publishers({pub_id})",
    }
    result = entity.create(backend, "solutions", body, return_record=False)
    if result.get("_dry_run"):
        result["_exists"] = bool(existing)
        result["would_skip"] = bool(existing) and if_exists == "skip"
        return result
    sol_id = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "uniquename": name,
        "friendlyname": friendly_name or name,
        "version": version,
        "publisherid": pub_id,
        "solutionid": sol_id,
    }
    if not sol_id:
        out["solution_lookup_error"] = (
            f"Could not parse solutionid from response: {result.get('entity_id_url')!r}"
        )
    return out


def clone_as_patch(
    backend: D365Backend,
    *,
    parent_solution: str,
    display_name: str | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    """Create a solution patch from a parent solution via the CloneAsPatch action.

    A patch must share the parent's major.minor and have a higher build/revision.
    When `version` is omitted the parent's version is read and its revision (the
    4th part) is bumped by one; when `display_name` is omitted it defaults to the
    parent's friendlyname. Both defaults need the parent record, read with a
    forced-real GET so they resolve under --dry-run too.

    Returns `{cloned, parent_solution, display_name, version, patch_solutionid}`
    on a real run.
    """
    if version is None or display_name is None:
        parent = solution_info(backend, parent_solution)
        if version is None:
            version = _bump_revision(parent.get("version", ""))
        if display_name is None:
            display_name = parent.get("friendlyname") or parent_solution

    body: dict[str, Any] = {
        "ParentSolutionUniqueName": parent_solution,
        "DisplayName": display_name,
        "VersionNumber": version,
    }
    result = as_dict(backend.post("CloneAsPatch", json_body=body))
    if result.get("_dry_run"):
        return result
    return {
        "cloned": True,
        "parent_solution": parent_solution,
        "display_name": display_name,
        "version": version,
        "patch_solutionid": result.get("SolutionId"),
    }


def uninstall_solution(
    backend: D365Backend, unique_name: str, *, force: bool = False
) -> dict[str, Any]:
    """Uninstall a solution: DELETE /solutions(<id>).

    Resolves the solutionid with a forced-real GET (so the preview is accurate
    under --dry-run and a missing solution fails fast before any DELETE). Unless
    `force=True`, pre-flights RetrieveDependenciesForUninstall and refuses with
    the blocker list when any dependency would block the uninstall — turning a
    confusing server fault into an actionable error. Returns
    `{uninstalled, solution, solutionid}` on a real run, or the entity.delete
    `_dry_run` preview (plus solution / solutionid) under --dry-run.
    """
    info = solution_info(backend, unique_name)
    sol_id = info["solutionid"]

    if not force:
        deps = dependencies.retrieve_dependencies_for_uninstall(backend, unique_name)
        if deps["count"]:
            raise D365Error(
                f"Solution {unique_name!r} has {deps['count']} uninstall "
                "dependency blocker(s); resolve them or pass force=True.",
                code="UninstallBlocked",
            )

    result = entity.delete(backend, "solutions", sol_id)
    if result.get("_dry_run"):
        return {**result, "solution": unique_name, "solutionid": sol_id}
    return {"uninstalled": True, "solution": unique_name, "solutionid": sol_id}


def delete_and_promote(backend: D365Backend, unique_name: str) -> dict[str, Any]:
    """Replace a managed base solution with its staged holding upgrade.

    Calls the DeleteAndPromote action, which deletes the base solution plus all
    of its patches and renames the holding solution to the base's unique name.
    Run this after a successful `stage-and-upgrade` holding import. Returns
    `{promoted, solution, solutionid}` on a real run.
    """
    if not unique_name:
        raise D365Error("solution unique name required.")
    result = as_dict(backend.post("DeleteAndPromote", json_body={"UniqueName": unique_name}))
    if result.get("_dry_run"):
        return result
    return {"promoted": True, "solution": unique_name, "solutionid": result.get("SolutionId")}


def _bump_revision(version: str) -> str:
    """Return `version` with its 4th part (revision) incremented by one.

    A clone-as-patch version must keep the parent's major.minor and exceed its
    build/revision; bumping the revision is the smallest valid increment. Raises
    D365Error on a version that is not 4-part dotted numeric.
    """
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version):
        raise D365Error(
            f"cannot auto-bump a non 4-part dotted version {version!r}; pass an explicit version."
        )
    parts = version.split(".")
    parts[3] = str(int(parts[3]) + 1)
    return ".".join(parts)


def update_solution(
    backend: D365Backend,
    unique_name: str,
    *,
    version: str | None = None,
    friendly_name: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Update an unmanaged solution's version / friendlyname / description in place.

    Resolves the solutionid via solution_info, builds a payload of only the
    supplied fields, and delegates to entity.update (If-Match:* + --dry-run reused;
    no new HTTP path). Returns `{updated, uniquename, solutionid, <changed fields>}`
    on a real run, or the entity.update `_dry_run` preview dict (plus uniquename /
    solutionid) under --dry-run.
    """
    if version is None and friendly_name is None and description is None:
        raise D365Error("nothing to update: pass version, friendly_name, or description.")
    if version is not None and not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version):
        raise D365Error(f"version must be a 4-part dotted numeric (e.g. 1.0.0.0); got {version!r}.")

    info = solution_info(backend, unique_name)
    sol_id = info["solutionid"]
    # Fail fast before the PATCH: the server rejects a version/metadata change on a
    # managed solution, and on a patch with CannotUpdateSolutionPatch.
    if info.get("ismanaged"):
        raise D365Error(
            f"Solution {unique_name!r} is managed; its version/metadata cannot be updated.",
            code="CannotUpdateManagedSolution",
        )
    if info.get("_parentsolutionid_value"):
        raise D365Error(
            f"Solution {unique_name!r} is a patch; the server rejects version/metadata "
            "updates on a patch (CannotUpdateSolutionPatch).",
            code="CannotUpdateSolutionPatch",
        )

    payload: dict[str, Any] = {}
    if version is not None:
        payload["version"] = version
    if friendly_name is not None:
        payload["friendlyname"] = friendly_name
    if description is not None:
        payload["description"] = description

    result = entity.update(backend, "solutions", sol_id, payload)
    if result.get("_dry_run"):
        return {**result, "uniquename": unique_name, "solutionid": sol_id}
    return {"updated": True, "uniquename": unique_name, "solutionid": sol_id, **payload}


# ── Solution components (#71) ────────────────────────────────────────────────
#
# The friendly-name → integer type map lives in solution_components
# (SOLUTION_COMPONENT_TYPES, re-exported above). resolve_component_type stays here
# alongside the add/remove lifecycle verbs that consume it.


def resolve_component_type(value: str | int) -> int:
    """Resolve a component-type `value` (int, numeric string, or friendly name)
    to its `componenttype` integer. Names are matched case- and separator-
    insensitively against SOLUTION_COMPONENT_TYPES. Raises D365Error on an
    unknown name.
    """
    if isinstance(value, int):
        return value
    text = value.strip()
    if text.lstrip("-").isdigit():
        return int(text)
    key = re.sub(r"[\s_-]+", "", text).lower()
    try:
        return SOLUTION_COMPONENT_TYPES[key]
    except KeyError:
        known = ", ".join(sorted(SOLUTION_COMPONENT_TYPES))
        raise D365Error(
            f"unknown component type {value!r}; pass an integer or one of: {known}."
        ) from None


# The `entity` componenttype (1) is the only root for which the platform accepts
# DoNotIncludeSubcomponents:true — and the cascade vector the #916 audit reasons
# about.
_ENTITY_TYPE = SOLUTION_COMPONENT_TYPES["entity"]


def _reject_non_entity_no_subcomponents(components: list[dict[str, Any]]) -> None:
    """Reject any component asking to exclude subcomponents on a non-entity root.

    AddSolutionComponent's ``DoNotIncludeSubcomponents:true`` is accepted by the
    platform only on Entity (type 1) roots; sent for any other type it returns
    ``HTTP 500: DoNotIncludeSubcomponents can not be set to true on non Entity
    root <id> of type <n>`` and rolls the whole transactional ``$batch`` back
    (#941). Catch that client-side, before any request, naming every offending
    row so a mixed-type add fails fast instead of surfacing as a misleadingly
    retryable server 500.
    """
    offending = [
        (c["component_type"], c["component_id"])
        for c in components
        if c.get("do_not_include_subcomponents") and c["component_type"] != _ENTITY_TYPE
    ]
    if offending:
        rows = ", ".join(f"type {t} id {i}" for t, i in offending)
        raise D365Error(
            "DoNotIncludeSubcomponents (--no-subcomponents, or a per-row "
            '"no_subcomponents": true) is only valid for entity components; '
            f"offending row(s): {rows}."
        )


def _require_unmanaged_solution(backend: D365Backend, solution: str, *, verb: str) -> None:
    """Forced-real solution_info pre-flight (works under dry-run too); raise if the
    target is managed. `verb` is the action phrase, e.g. 'added to'.
    """
    info = solution_info(backend, solution)
    if info.get("ismanaged"):
        raise D365Error(
            f"Solution {solution!r} is managed; components can only be {verb} an "
            "unmanaged solution.",
            code="CannotModifyManagedSolution",
        )


def add_solution_component(
    backend: D365Backend,
    *,
    solution: str,
    component_id: str,
    component_type: int,
    add_required_components: bool = True,
    do_not_include_subcomponents: bool = False,
) -> dict[str, Any]:
    """Add an existing component to an unmanaged solution via AddSolutionComponent.

    Pre-flights solution_info (forced-real even under dry-run) and refuses a
    managed target — AddSolutionComponent is unmanaged-only. Returns
    `{added, solution, component_id, component_type}` on a real run.
    """
    _reject_non_entity_no_subcomponents(
        [
            {
                "component_id": component_id,
                "component_type": component_type,
                "do_not_include_subcomponents": do_not_include_subcomponents,
            }
        ]
    )
    _require_unmanaged_solution(backend, solution, verb="added to")

    body: dict[str, Any] = {
        "ComponentId": component_id,
        "ComponentType": component_type,
        "SolutionUniqueName": solution,
        "AddRequiredComponents": add_required_components,
        "DoNotIncludeSubcomponents": do_not_include_subcomponents,
    }
    result = as_dict(backend.post("AddSolutionComponent", json_body=body))
    if result.get("_dry_run"):
        result["solution"] = solution
        result["component_id"] = component_id
        result["component_type"] = component_type
        return result
    return {
        "added": True,
        "solution": solution,
        "component_id": component_id,
        "component_type": component_type,
    }


def remove_solution_component(
    backend: D365Backend,
    *,
    solution: str,
    component_id: str,
    component_type: int,
) -> dict[str, Any]:
    """Remove a component from an unmanaged solution via RemoveSolutionComponent.

    Pre-flights solution_info (forced-real even under dry-run) and refuses a
    managed target — a managed solution cannot be edited. Returns
    `{removed, solution, component_id, component_type}` on a real run.
    """
    _require_unmanaged_solution(backend, solution, verb="removed from")

    # Unlike AddSolutionComponent, the RemoveSolutionComponent Web API action
    # has no ComponentId parameter — it takes a SolutionComponent entity
    # reference whose solutioncomponentid carries the component objectid
    # (live-verified contract, #181).
    body: dict[str, Any] = {
        "SolutionComponent": {
            "solutioncomponentid": component_id,
            "@odata.type": "Microsoft.Dynamics.CRM.solutioncomponent",
        },
        "ComponentType": component_type,
        "SolutionUniqueName": solution,
    }
    result = as_dict(backend.post("RemoveSolutionComponent", json_body=body))
    if result.get("_dry_run"):
        result["solution"] = solution
        result["component_id"] = component_id
        result["component_type"] = component_type
        return result
    return {
        "removed": True,
        "solution": solution,
        "component_id": component_id,
        "component_type": component_type,
    }


def parse_components_file(
    path: str | Path,
    *,
    for_add: bool,
    default_no_add_required: bool = False,
    default_no_subcomponents: bool = False,
) -> list[dict[str, Any]]:
    """Load a batch-component JSON file into resolved core-component dicts (#914).

    The file is a JSON list of ``{"type": <int|name>, "id": <guid>}`` rows; an
    add file may additionally carry per-row ``"no_add_required"`` /
    ``"no_subcomponents"`` booleans (remove has no such flags). Unknown keys are
    rejected rather than silently dropped — the issue example's ``behavior`` key
    has no RootComponentBehavior parameter on the actions, so it errors here. Each
    row's ``type`` is resolved through :func:`resolve_component_type`. Returns the
    same dict shape the add/remove batch cores consume.

    For an add file, ``default_no_add_required`` / ``default_no_subcomponents``
    are the batch-wide defaults (the command-level ``--no-add-required`` /
    ``--no-subcomponents`` flags); a row's own boolean key overrides its default.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise D365Error(f"Could not read {p}: {exc}") from exc
    try:
        data: Any = json.loads(text)
    except ValueError as exc:
        raise D365Error(f"Could not parse {p}: {exc}") from exc
    if not isinstance(data, list):
        raise D365Error(f"{p}: expected a JSON list at root, got {type(data).__name__}")
    raw_rows = cast(list[Any], data)
    if not raw_rows:
        raise D365Error(f"{p}: component list is empty")

    allowed = {"type", "id"}
    if for_add:
        allowed |= {"no_add_required", "no_subcomponents"}
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            raise D365Error(f"{p} row #{i}: expected an object, got {type(raw).__name__}")
        row = cast(dict[str, Any], raw)
        extra = set(row) - allowed
        if extra:
            raise D365Error(
                f"{p} row #{i}: unknown key(s) {sorted(extra)}; allowed: {sorted(allowed)}"
            )
        if "type" not in row or "id" not in row:
            raise D365Error(f"{p} row #{i}: 'type' and 'id' are required")
        cid = row["id"]
        if not isinstance(cid, str) or not cid:
            raise D365Error(f"{p} row #{i}: 'id' must be a non-empty string")
        component: dict[str, Any] = {
            "component_id": cid,
            "component_type": resolve_component_type(row["type"]),
        }
        if for_add:
            component["add_required_components"] = not _row_bool(
                p, i, row, "no_add_required", default_no_add_required
            )
            no_sub = _row_bool(p, i, row, "no_subcomponents", default_no_subcomponents)
            # DoNotIncludeSubcomponents is entity-only (#941). As a batch-wide
            # *default* it applies to entity rows only — a non-entity row silently
            # gets False rather than 500ing the whole batch. An *explicit* per-row
            # "no_subcomponents":true on a non-entity flows through unchanged; the
            # add core rejects it client-side, so the explicit request is surfaced,
            # not silently dropped.
            from_default = "no_subcomponents" not in row
            if no_sub and from_default and component["component_type"] != _ENTITY_TYPE:
                no_sub = False
            component["do_not_include_subcomponents"] = no_sub
        out.append(component)
    return out


def _row_bool(path: Path, i: int, row: dict[str, Any], key: str, default: bool) -> bool:
    """Read an optional boolean row flag, falling back to `default`; reject non-bools."""
    val = row.get(key, default)
    if not isinstance(val, bool):
        raise D365Error(f"{path} row #{i}: {key!r} must be a boolean, got {type(val).__name__}")
    return val


def add_solution_components(
    backend: D365Backend,
    *,
    solution: str,
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    """Batch AddSolutionComponent for many components in one transactional $batch.

    Each entry in `components` is a dict of
    ``{component_id, component_type[, add_required_components,
    do_not_include_subcomponents]}`` (the per-row flags default to on/off). The
    whole batch runs as one changeset — a mid-batch failure rolls every row back.
    See :func:`_run_component_batch` for the returned per-row summary shape.
    """
    _reject_non_entity_no_subcomponents(components)
    _require_unmanaged_solution(backend, solution, verb="added to")
    ops: list[BatchOperation] = [
        {
            "method": "POST",
            "url": "AddSolutionComponent",
            "body": {
                "ComponentId": c["component_id"],
                "ComponentType": c["component_type"],
                "SolutionUniqueName": solution,
                "AddRequiredComponents": c.get("add_required_components", True),
                "DoNotIncludeSubcomponents": c.get("do_not_include_subcomponents", False),
            },
        }
        for c in components
    ]
    return _run_component_batch(
        backend, solution=solution, components=components, ops=ops, verb="add"
    )


def remove_solution_components(
    backend: D365Backend,
    *,
    solution: str,
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    """Batch RemoveSolutionComponent for many components in one transactional $batch.

    Each entry in `components` is ``{component_id, component_type}``. Like the
    singular core, each row uses the SolutionComponent entity reference (#181).
    Runs as one changeset — a mid-batch failure rolls every row back.
    """
    _require_unmanaged_solution(backend, solution, verb="removed from")
    ops: list[BatchOperation] = [
        {
            "method": "POST",
            "url": "RemoveSolutionComponent",
            "body": {
                "SolutionComponent": {
                    "solutioncomponentid": c["component_id"],
                    "@odata.type": "Microsoft.Dynamics.CRM.solutioncomponent",
                },
                "ComponentType": c["component_type"],
                "SolutionUniqueName": solution,
            },
        }
        for c in components
    ]
    return _run_component_batch(
        backend, solution=solution, components=components, ops=ops, verb="remove"
    )


def _run_component_batch(
    backend: D365Backend,
    *,
    solution: str,
    components: list[dict[str, Any]],
    ops: list[BatchOperation],
    verb: str,
) -> dict[str, Any]:
    """Run component `ops` as one transactional $batch and summarise per row.

    Under `--dry-run` the $batch is short-circuited (the pre-flight solution GET
    still ran) and a `would_add`/`would_remove` preview is returned. On a real
    run, returns ``{solution, added|removed: [{type, id, ok, status, error}],
    count, succeeded, failed, rolled_back}``. `rolled_back` is true whenever any
    row failed, because the changeset is atomic — the offending row carries the
    server error; its siblings surface as not-applied.
    """
    key = "added" if verb == "add" else "removed"
    if backend.dry_run:
        return {
            "_dry_run": True,
            "solution": solution,
            f"would_{verb}": [
                {"type": c["component_type"], "id": c["component_id"]} for c in components
            ],
            "count": len(components),
        }
    results = run_batched(backend, ops, transactional=True, continue_on_error=False)
    rows: list[dict[str, Any]] = []
    failed = 0
    for c, r in zip(components, results, strict=True):
        status = int(r.get("status") or 0)
        ok = 200 <= status < 300
        if not ok:
            failed += 1
        rows.append(
            {
                "type": c["component_type"],
                "id": c["component_id"],
                "ok": ok,
                "status": status,
                "error": None if ok else r.get("error"),
            }
        )
    return {
        "solution": solution,
        key: rows,
        "count": len(rows),
        "succeeded": len(rows) - failed,
        "failed": failed,
        "rolled_back": failed > 0,
    }


def list_solutions(backend: D365Backend, *, managed: bool | None = None) -> list[dict[str, Any]]:
    params = {
        "$select": "uniquename,friendlyname,version,ismanaged,installedon,solutionid",
        "$orderby": "uniquename",
    }
    if managed is not None:
        params["$filter"] = f"ismanaged eq {'true' if managed else 'false'}"
    return backend.get_collection("solutions", params=params)


def solution_info(backend: D365Backend, unique_name: str) -> dict[str, Any]:
    if not unique_name:
        raise D365Error("solution unique name required.")
    params = {"$filter": f"uniquename eq {odata_literal(unique_name)}"}
    result = as_dict(backend.get("solutions", params=params))
    items = result.get("value", [])
    if not items:
        raise D365Error(f"Solution not found: {unique_name}")
    return items[0]


def solution_components(backend: D365Backend, unique_name: str) -> list[dict[str, Any]]:
    sol = solution_info(backend, unique_name)
    solution_id = sol["solutionid"]
    params = {
        "$select": "componenttype,objectid,rootcomponentbehavior",
        "$filter": f"_solutionid_value eq {solution_id}",
    }
    # No `$top`: it is a hard limit that suppresses `@odata.nextLink`, capping a
    # large solution's inventory at the server page ceiling. get_collection
    # follows the cursor to exhaustion instead.
    return backend.get_collection("solutioncomponents", params=params)


def _resolve_attribute_names(
    backend: D365Backend, objectids: list[str]
) -> dict[str, dict[str, Any]]:
    """Resolve attribute ``objectid`` (MetadataId) → ``{"name", "entity"}``.

    An attribute has no top-level by-id path — the Web API only exposes it under
    its owning entity (``EntityDefinitions(..)/Attributes(..)``). So rather than
    one request per attribute, pull them in a single metadata query per chunk,
    filtering the expanded ``Attributes`` down to the wanted ``MetadataId`` set
    (metadata rows always carry ``MetadataId`` even when unselected). Entities
    with no matching attribute come back with an empty ``Attributes`` and are
    skipped.
    """
    wanted = {oid for oid in objectids if oid}
    out: dict[str, dict[str, Any]] = {}
    ids = sorted(wanted)
    for start in range(0, len(ids), 20):
        chunk = ids[start : start + 20]
        flt = " or ".join(f"MetadataId eq {g}" for g in chunk)
        try:
            rows = backend.get_collection(
                "EntityDefinitions",
                params={
                    "$select": "LogicalName",
                    "$expand": f"Attributes($select=LogicalName,MetadataId;$filter={flt})",
                },
            )
        except D365Error:
            # Graceful fallback (matches the by-id path): a failed chunk leaves
            # its attributes unresolved (raw GUID) rather than aborting --resolve.
            continue
        for ent in rows:
            entity_name = ent.get("LogicalName")
            attrs = cast("list[dict[str, Any]]", ent.get("Attributes") or [])
            for attr in attrs:
                mid = str(attr.get("MetadataId", "")).lower()
                if mid in wanted:
                    out[mid] = {"name": attr.get("LogicalName"), "entity": entity_name}
    return out


def resolve_component_names(
    backend: D365Backend, items: list[dict[str, Any]]
) -> dict[tuple[int, str], dict[str, Any]]:
    """Resolve each component's ``objectid`` to a friendly name.

    Returns a map keyed by ``(componenttype, objectid.lower())`` whose values are
    ``{"name": str|None}`` plus an ``"entity"`` key for entity-scoped components
    (forms, views, attributes, workflows). Directly-resolvable types go through a
    single ``$batch`` of by-id GETs; attributes go through
    :func:`_resolve_attribute_names`. A component whose type is unknown or whose
    lookup errors is simply absent from the map — the caller falls back to the
    raw GUID, so an unresolvable id never raises.
    """
    ops: list[BatchOperation] = []
    op_keys: list[tuple[int, str, Any]] = []
    attr_ids: list[str] = []
    for it in items:
        objectid = str(it.get("objectid") or "").strip()
        if not objectid:
            continue
        componenttype = int(it.get("componenttype") or 0)
        oid_lower = objectid.lower()
        if componenttype == 2:  # attribute — entity-scoped, resolved in bulk
            attr_ids.append(oid_lower)
            continue
        spec = RESOLVE_SPECS.get(componenttype)
        if spec is None:
            continue
        url = f"{spec.path.format(id=objectid)}?$select={spec.select}"
        ops.append({"method": "GET", "url": url})
        op_keys.append((componenttype, oid_lower, spec))

    resolved: dict[tuple[int, str], dict[str, Any]] = {}

    if ops:
        # $batch is refused under read-only and short-circuited under --dry-run;
        # fall back to per-item GETs there (reads still run live). Elsewhere the
        # whole fan-out collapses into ceil(N/100) round trips.
        if backend.dry_run or backend.read_only:
            bodies = [
                _safe_get_by_id(backend, spec.path.format(id=oid), spec.select)
                for _, oid, spec in op_keys
            ]
        else:
            bodies = [
                res.get("body") if not res.get("error") else None
                for res in run_batched(backend, ops, continue_on_error=True)
            ]
        for (componenttype, oid_lower, spec), body in zip(op_keys, bodies, strict=True):
            if not isinstance(body, dict):
                continue
            entry: dict[str, Any] = {"name": body.get(spec.name_field)}
            if spec.entity_field:
                parent = body.get(spec.entity_field)
                if parent:
                    entry["entity"] = parent
            resolved[component_key(componenttype, oid_lower)] = entry

    for mid, entry in _resolve_attribute_names(backend, attr_ids).items():
        resolved[component_key(2, mid)] = entry

    return resolved


def _safe_get_by_id(backend: D365Backend, path: str, select: str) -> dict[str, Any] | None:
    """One by-id GET for the read-only / dry-run fallback; ``None`` on any error."""
    try:
        return as_dict(backend.get(path, params={"$select": select}))
    except D365Error:
        return None


# ── solution audit + add-time cascade preview (#916) ─────────────────────────


def _extract_required(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull required ``(componenttype, objectid)`` keys from a
    ``RetrieveRequiredComponents`` response. Objectids are lowercased for stable
    matching; a record missing either field is skipped.
    """
    records = cast("list[dict[str, Any]]", result.get("value") or [])
    out: list[dict[str, Any]] = []
    for r in records:
        rtype = r.get("requiredcomponenttype")
        rid = r.get("requiredcomponentobjectid")
        if rtype is None or not rid:
            continue
        out.append({"componenttype": int(rtype), "objectid": str(rid).lower()})
    return out


def required_component_ids(
    backend: D365Backend, component_id: str, component_type: int
) -> list[dict[str, Any]]:
    """Directly-required components of ``(component_id, component_type)``.

    One ``RetrieveRequiredComponents`` GET (fires under --dry-run — reads-execute).
    Returns ``[{"componenttype": int, "objectid": str}]`` (objectids lowercased);
    an empty list means the component requires nothing.
    """
    path = dependencies.build_dependency_path(component_id, component_type, for_="required")
    return _extract_required(as_dict(backend.get(path)))


def _required_edges(
    backend: D365Backend, entities: list[dict[str, Any]]
) -> dict[tuple[int, str], list[str]]:
    """Map each in-solution component to the entities that require it (#916).

    For every entity ``E`` in ``entities`` (the cascade vector) fetch what ``E``
    directly requires and record ``E``'s objectid against each required key,
    excluding a self-requirement. Returns
    ``{(componenttype, objectid): [requirer_objectid, ...]}`` with each requirer
    list de-duplicated and sorted.

    Fans the per-entity ``RetrieveRequiredComponents`` GETs into one ``$batch``;
    under --dry-run / read-only ``$batch`` is refused, so it falls back to per-item
    GETs (reads still run) — mirrors :func:`resolve_component_names`.
    """
    ids = [str(e.get("objectid") or "") for e in entities]
    self_keys = [component_key(e.get("componenttype"), e.get("objectid")) for e in entities]
    if not ids:
        return {}
    if backend.dry_run or backend.read_only:
        required_lists = [_safe_required(backend, oid) for oid in ids]
    else:
        ops: list[BatchOperation] = [
            {
                "method": "GET",
                "url": dependencies.build_dependency_path(oid, _ENTITY_TYPE, for_="required"),
            }
            for oid in ids
        ]
        required_lists: list[list[dict[str, Any]]] = []
        for res in run_batched(backend, ops, continue_on_error=True):
            body = res.get("body")
            required_lists.append(_extract_required(body if isinstance(body, dict) else {}))

    required_by: dict[tuple[int, str], list[str]] = {}
    for self_key, reqs in zip(self_keys, required_lists, strict=True):
        for req in reqs:
            rkey = component_key(req["componenttype"], req["objectid"])
            if rkey == self_key:  # a component requiring itself is not a cascade
                continue
            required_by.setdefault(rkey, []).append(self_key[1])
    return {k: sorted(set(v)) for k, v in required_by.items()}


def _safe_required(backend: D365Backend, object_id: str) -> list[dict[str, Any]]:
    """`required_component_ids` for an entity, swallowing errors to ``[]`` (the
    per-item fallback is best-effort, like `_safe_get_by_id`).
    """
    try:
        return required_component_ids(backend, object_id, _ENTITY_TYPE)
    except D365Error:
        return []


def audit_solution(backend: D365Backend, unique_name: str) -> dict[str, Any]:
    """Audit a solution for AddRequiredComponents cascade / whole-entity drift (#916).

    Fetches the component inventory, builds the required-by graph over its entity
    components (the cascade vector), resolves friendly names, and classifies via
    :func:`build_audit`. Returns the ``build_audit`` report plus ``"solution"``.
    """
    components = normalize_components(solution_components(backend, unique_name))
    entities = [c for c in components if c["componenttype"] == _ENTITY_TYPE]
    required_by_ids = _required_edges(backend, entities)
    names = resolve_component_names(backend, components)
    name_by_oid = {
        oid: (info.get("name") or oid) for (t, oid), info in names.items() if t == _ENTITY_TYPE
    }
    required_by = {
        key: [name_by_oid.get(r, r) for r in requirers]
        for key, requirers in required_by_ids.items()
    }
    report = build_audit(components, required_by=required_by, names=names)
    return {"solution": unique_name, **report}


def preview_required_components(
    backend: D365Backend, components: list[tuple[str, int]]
) -> list[dict[str, Any]]:
    """Aggregate the components an add-time cascade would pull in (#916).

    ``components`` is ``[(component_id, component_type), ...]`` that will cascade
    (AddRequiredComponents on). Returns a de-duplicated, sorted list of
    ``{"componenttype", "type_name", "objectid"}`` the server may add beyond the
    requested components — the components themselves are excluded. Each component's
    directly-required set is one ``RetrieveRequiredComponents`` GET.
    """
    requested = {component_key(ct, cid) for cid, ct in components}
    seen: dict[tuple[int, str], dict[str, Any]] = {}
    for component_id, component_type in components:
        for req in required_component_ids(backend, component_id, component_type):
            key = component_key(req["componenttype"], req["objectid"])
            if key in requested or key in seen:
                continue
            seen[key] = {
                "componenttype": req["componenttype"],
                "type_name": component_type_name(req["componenttype"]),
                "objectid": req["objectid"],
            }
    return sorted(seen.values(), key=lambda r: (r["componenttype"], r["objectid"]))


def retrieve_missing_components(backend: D365Backend, solution_file: str | Path) -> dict[str, Any]:
    """List components an exported solution needs that the connected org lacks.

    ``solution_file`` is a path to an exported solution ``.zip``. Its bytes are
    sent as the ``CustomizationFile`` (Edm.Binary) parameter of the
    ``RetrieveMissingComponents`` Web API function and checked against the
    connected org (the import target). An empty result means the org already has
    everything the solution requires.

    The binary parameter is passed as a ``binary'<base64>'`` parameter-alias
    literal in the query string — a bare base64 alias is rejected by the server
    (verified live). The file rides in the URL, so a very large solution can hit
    the server's URL-length limit; that is an inherent constraint of this function.

    Returns ``{"missing_components": [...], "count": int}``.
    """
    data = Path(solution_file).read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    alias = urllib.parse.quote(f"binary'{b64}'", safe="")
    path = f"RetrieveMissingComponents(CustomizationFile=@p1)?@p1={alias}"
    result = as_dict(backend.get(path))
    missing: list[dict[str, Any]] = result.get("MissingComponents") or []
    return {"missing_components": missing, "count": len(missing)}


# ── Publish utilities ────────────────────────────────────────────────────────


def publish_all(backend: D365Backend) -> dict[str, Any]:
    """Call PublishAllXml — publishes all unpublished customizations.

    Action returns 204 No Content on success, so we synthesize a confirmation dict.
    The org-wide customization-lock error is retried centrally by the backend for
    every customization write (see ``_customization_lock_code`` in the backend), so
    this path no longer carries its own retry loop (#741).
    """
    result = as_dict(backend.post("PublishAllXml"))
    # Bust the cache on any successful non-dry-run publish, regardless of whether
    # the action returned a body (dry-run yields a truthy preview dict — its body
    # must NOT trigger invalidation, hence the guard before the early return).
    if not backend.dry_run:
        metadata_cache.invalidate(backend.profile)
    if result:
        return result
    return {"published": True, "action": "PublishAllXml"}


def publish_xml(backend: D365Backend, parameter_xml: str) -> dict[str, Any]:
    """Call PublishXml with a Publish Request Schema XML payload.

    Example parameter_xml:
        '<importexportxml><entities><entity>account</entity></entities></importexportxml>'

    Reference: https://learn.microsoft.com/power-apps/developer/model-driven-apps/publish-customizations
    """
    if not parameter_xml or "<" not in parameter_xml:
        raise D365Error("parameter_xml must be a Publish Request XML document.")
    result = as_dict(
        backend.post(
            "PublishXml",
            json_body={"ParameterXml": parameter_xml},
        )
    )
    # Bust the cache on any successful non-dry-run publish, regardless of body
    # (see publish_all — the dry-run preview is truthy and must not invalidate).
    if not backend.dry_run:
        metadata_cache.invalidate(backend.profile)
    if result:
        return result
    return {"published": True, "action": "PublishXml"}


def service_document(backend: D365Backend) -> dict[str, Any]:
    """GET the root service document — lists all entity sets exposed by the server."""
    return as_dict(backend.get(""))
