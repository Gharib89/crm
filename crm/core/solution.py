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
import re
import urllib.parse
from pathlib import Path
from typing import Any

from crm.core import dependencies
from crm.core import entity
from crm.core import metadata_cache
from crm.utils.d365_backend import D365Backend, D365Error, as_dict, odata_literal

# ── Backward-compat re-exports ───────────────────────────────────────────────
#
# Homes changed, the public surface did not. These are deliberate re-exports
# (the redundant `as X` marks them intentional for pyright); callers and tests
# that reach these names via `crm.core.solution.<name>` must keep working. Note:
# a function whose body moved to one of these modules is patched on its NEW home
# module — direct-internal tests for `solution_transfer` privates patch there.
from crm.core.solution_components import (
    SOLUTION_COMPONENT_TYPES as SOLUTION_COMPONENT_TYPES,
    component_type_name as component_type_name,
    diff_components as diff_components,
    layer_conflicts as layer_conflicts,
    normalize_components as normalize_components,
)
from crm.core.solution_transfer import (
    export_solution as export_solution,
    import_result as import_result,
    import_solution as import_solution,
    parse_import_job_data as parse_import_job_data,
)


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
        params={"$filter": f"uniquename eq {odata_literal(name)}",
                "$select": "publisherid,uniquename"},
    )
    if existing and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(f"Publisher {name!r} already exists.", code="AlreadyExists")
        return {"skipped": True, "exists": True, "uniquename": name,
                "publisherid": existing[0].get("publisherid")}

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
        "created": True, "uniquename": name,
        "friendlyname": friendly_name or name, "customizationprefix": prefix,
        "customizationoptionvalueprefix": option_value_prefix, "publisherid": pub_id,
    }
    if not pub_id:
        out["publisher_lookup_error"] = (
            f"Could not parse publisherid from response: {result.get('entity_id_url')!r}")
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
        params={"$filter": f"uniquename eq {odata_literal(name)}",
                "$select": "solutionid,uniquename"},
    )
    # The skip/error short-circuit below only fires on a real (non-dry) run. Every
    # path that reaches the POST — including the dry-run preview — needs the
    # publisher id to build the bind, so resolve it now unless we already know
    # we'll short-circuit.
    will_short_circuit = bool(existing) and not backend.dry_run
    pub_id = publisher_id
    if not will_short_circuit and not pub_id:
        if not publisher_unique_name:
            raise D365Error(
                "a publisher is required: pass publisher_unique_name or publisher_id.")
        pub_id = _resolve_publisher_id(backend, publisher_unique_name)
    if existing and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(f"Solution {name!r} already exists.", code="AlreadyExists")
        return {"skipped": True, "exists": True, "uniquename": name,
                "solutionid": existing[0].get("solutionid")}

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
        "created": True, "uniquename": name, "friendlyname": friendly_name or name,
        "version": version, "publisherid": pub_id, "solutionid": sol_id,
    }
    if not sol_id:
        out["solution_lookup_error"] = (
            f"Could not parse solutionid from response: {result.get('entity_id_url')!r}")
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
            f"cannot auto-bump a non 4-part dotted version {version!r}; "
            "pass an explicit version."
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
        raise D365Error(
            f"version must be a 4-part dotted numeric (e.g. 1.0.0.0); got {version!r}."
        )

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
    unknown name."""
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


def _require_unmanaged_solution(
    backend: D365Backend, solution: str, *, verb: str
) -> None:
    """Forced-real solution_info pre-flight (works under dry-run too); raise if the
    target is managed. `verb` is the action phrase, e.g. 'added to'."""
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
    return {"added": True, "solution": solution, "component_id": component_id,
            "component_type": component_type}


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
    return {"removed": True, "solution": solution, "component_id": component_id,
            "component_type": component_type}


def list_solutions(backend: D365Backend, *, managed: bool | None = None) -> list[dict[str, Any]]:
    params = {
        "$select": "uniquename,friendlyname,version,ismanaged,installedon,solutionid",
        "$orderby": "uniquename",
    }
    if managed is not None:
        params["$filter"] = f"ismanaged eq {'true' if managed else 'false'}"
    result = as_dict(backend.get("solutions", params=params))
    return result.get("value", [])


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
        "$top": "5000",
    }
    result = as_dict(backend.get("solutioncomponents", params=params))
    return result.get("value", [])


def retrieve_missing_components(
    backend: D365Backend, solution_file: "str | Path"
) -> dict[str, Any]:
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
    if result.get("_dry_run"):
        return result
    missing: list[dict[str, Any]] = result.get("MissingComponents") or []
    return {"missing_components": missing, "count": len(missing)}


# ── Publish utilities ────────────────────────────────────────────────────────


def publish_all(backend: D365Backend) -> dict[str, Any]:
    """Call PublishAllXml — publishes all unpublished customizations.

    Action returns 204 No Content on success, so we synthesize a confirmation dict.
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
    result = as_dict(backend.post(
        "PublishXml",
        json_body={"ParameterXml": parameter_xml},
    ))
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
