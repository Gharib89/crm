"""Global option set CRUD.

`update_optionset` is granular: insert/update/delete/reorder dispatch
to `InsertOptionValue`, `UpdateOptionValue`, `DeleteOptionValue`,
`OrderOption` bound actions in that order. Partial failure stops and
re-raises `D365Error` with `.completed_steps` and `.stage` attached
so callers can inspect what already landed — no rollback.
"""

from __future__ import annotations

import re
from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import label, maybe_publish, target_exists
from crm.core import dependencies as dep_mod


def _option_label(label_obj: dict[str, Any]) -> str | None:
    """Best-effort display label from a Dataverse Label payload.

    Prefers UserLocalizedLabel.Label, falls back to LocalizedLabels[0].Label.
    """
    ull: dict[str, Any] = label_obj.get("UserLocalizedLabel") or {}
    if ull.get("Label"):
        return str(ull["Label"])
    locs: list[dict[str, Any]] = label_obj.get("LocalizedLabels") or []
    if locs:
        return str(locs[0].get("Label") or "") or None
    return None


def _parse_optionset_id(entity_id_url: str | None) -> str | None:
    if not entity_id_url:
        return None
    match = re.search(r"GlobalOptionSetDefinitions\(([0-9a-fA-F-]{36})\)", entity_id_url)
    return match.group(1) if match else None


def list_optionsets(
    backend: D365Backend,
    *,
    custom_only: bool = False,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """List global option set definitions. Client-side $top slice."""
    result = as_dict(backend.get(
        "GlobalOptionSetDefinitions",
        params={"$select": "Name,DisplayName,IsCustomOptionSet,IsGlobal,IsManaged"},
    ))
    items = result.get("value", [])
    if custom_only:
        items = [it for it in items if it.get("IsCustomOptionSet") is True]
    if top is not None:
        if top < 1:
            raise D365Error("--top must be >= 1")
        items = items[:top]
    return items


def get_optionset(backend: D365Backend, name: str) -> dict[str, Any]:
    """Retrieve a global option set with its options expanded."""
    if not name:
        raise D365Error("name is required.")
    return as_dict(backend.get(
        f"GlobalOptionSetDefinitions(Name='{name}')",
        params={"$expand": "Options"},
    ))


def create_optionset(
    backend: D365Backend,
    *,
    name: str,
    display_name: str,
    description: str | None = None,
    options: list[tuple[int | None, str]] | None = None,
    is_global: bool = True,
    publish: bool = False,
    solution: str | None = None,
    if_exists: str = "error",
) -> dict[str, Any]:
    """Create a global option set. Returns `{created, name, metadata_id_url, ...}`."""
    if not name or "_" not in name:
        raise D365Error("name must include a publisher prefix, e.g. 'new_priority'.")
    if if_exists not in ("error", "skip"):
        raise D365Error("if_exists must be 'error' or 'skip'.")

    option_list: list[dict[str, Any]] = []
    if options:
        seen: set[int] = set()
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

    exists = target_exists(backend, f"GlobalOptionSetDefinitions(Name='{name}')")
    if exists and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(
                f"Global option set {name!r} already exists.",
                code="AlreadyExists",
            )
        return {"skipped": True, "exists": True, "name": name}

    body: dict[str, Any] = {
        "@odata.type": "Microsoft.Dynamics.CRM.OptionSetMetadata",
        "Name": name,
        "DisplayName": label(display_name),
        "IsGlobal": is_global,
        "OptionSetType": "Picklist",
        "Options": option_list,
    }
    if description:
        body["Description"] = label(description)

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(
        "GlobalOptionSetDefinitions",
        json_body=body,
        extra_headers=headers,
    ))
    if result.get("_dry_run"):
        result["_exists"] = exists
        result["would_skip"] = exists and if_exists == "skip"
        return result

    entity_id_url = result.get("_entity_id_url")
    os_id = _parse_optionset_id(entity_id_url)
    lookup_error: str | None = None
    name_readback: str | None = None
    if not os_id:
        lookup_error = (
            f"Could not parse MetadataId from response: {entity_id_url!r}"
        )
    else:
        try:
            rb = as_dict(backend.get(
                f"GlobalOptionSetDefinitions({os_id})",
                params={"$select": "Name,IsCustomOptionSet"},
            ))
            name_readback = rb.get("Name")
        except D365Error as exc:
            lookup_error = f"Read-back failed: {exc}"

    out: dict[str, Any] = {
        "created": True,
        "name": name_readback or name,
        "metadata_id_url": entity_id_url,
        "solution": solution,
    }
    if lookup_error:
        out["optionset_lookup_error"] = lookup_error
    maybe_publish(backend, out, publish)
    return out


def _option_diff(
    current: list[dict[str, Any]],
    insert: list[tuple[int | None, str]] | None,
    update: list[tuple[int, str]] | None,
    delete: list[int] | None,
    reorder: list[int] | None,
) -> dict[str, Any]:
    """Classify pending option changes against the current live options.

    Returns:
        inserts: list of {value, label} for options to add
        updates: list of {value, old_label, new_label} — old_label from current, None if absent
        deletes: list of {value, old_label} — old_label from current, None if absent
        reorder: {old, new} — only present when reorder is requested
    """
    # Build lookup: value → current label
    current_labels: dict[int, str | None] = {}
    for opt in current:
        v = opt.get("Value")
        if isinstance(v, int):
            lbl_obj: dict[str, Any] = opt.get("Label") or {}
            current_labels[v] = _option_label(lbl_obj)

    diff: dict[str, Any] = {}

    if insert:
        diff["inserts"] = [{"value": v, "label": lbl} for v, lbl in insert]

    if update:
        diff["updates"] = [
            {"value": v, "old_label": current_labels.get(v), "new_label": lbl}
            for v, lbl in update
        ]

    if delete:
        diff["deletes"] = [
            {"value": v, "old_label": current_labels.get(v)}
            for v in delete
        ]

    if reorder:
        diff["reorder"] = {
            "old": list(current_labels),
            "new": list(reorder),
        }

    return diff


def update_optionset(
    backend: D365Backend,
    name: str,
    *,
    insert: list[tuple[int | None, str]] | None = None,
    update: list[tuple[int, str]] | None = None,
    delete: list[int] | None = None,
    reorder: list[int] | None = None,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Granular global-option-set update.

    Dispatch order: insert → update → delete → reorder. Stops on the
    first per-stage HTTP error and re-raises a `D365Error` whose
    `.completed_steps` and `.stage` attributes record what already
    landed on the server — partial mutation is observable but not
    silently swallowed.
    """
    if not (insert or update or delete or reorder):
        raise D365Error("nothing to update — pass at least one of insert/update/delete/reorder.")

    # Validate labels before dry-run branch so invalid input always raises.
    if insert:
        for _, lbl in insert:
            if not lbl:
                _exc = D365Error("insert label must not be empty.")
                _exc.completed_steps = []
                _exc.stage = "insert"
                raise _exc
    if update:
        for _, lbl in update:
            if not lbl:
                _exc = D365Error("update label must not be empty.")
                _exc.completed_steps = []
                _exc.stage = "update"
                raise _exc

    if backend.dry_run:
        # Force a real GET to build the before/after diff (writes stay suppressed).
        was_dry = backend.dry_run
        backend.dry_run = False
        try:
            current_os = get_optionset(backend, name)
        finally:
            backend.dry_run = was_dry
        current_opts: list[dict[str, Any]] = current_os.get("Options") or []

        actions: list[dict[str, Any]] = []
        if insert:
            for value, lbl in insert:
                body: dict[str, Any] = {"OptionSetName": name, "Label": label(lbl)}
                if value is not None:
                    body["Value"] = value
                actions.append(body)
        if update:
            for value, lbl in update:
                actions.append({
                    "OptionSetName": name,
                    "Value": value,
                    "Label": label(lbl),
                    "MergeLabels": False,
                })
        if delete:
            for value in delete:
                actions.append({"OptionSetName": name, "Value": value})
        if reorder:
            actions.append({"OptionSetName": name, "Values": list(reorder)})

        diff = _option_diff(current_opts, insert, update, delete, reorder)
        return {
            "_dry_run": True,
            "name": name,
            "diff": diff,
            "actions": actions,
        }

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    completed: list[str] = []

    def _attach_partial(exc: D365Error, stage: str) -> D365Error:
        exc.completed_steps = list(completed)
        exc.stage = stage
        return exc

    if insert:
        for value, lbl in insert:
            body: dict[str, Any] = {"OptionSetName": name, "Label": label(lbl)}
            if value is not None:
                body["Value"] = value
            try:
                backend.post("InsertOptionValue", json_body=body, extra_headers=headers)
            except D365Error as exc:
                raise _attach_partial(exc, "insert")
            completed.append(f"insert:{value if value is not None else 'auto'}")

    if update:
        for value, lbl in update:
            body = {
                "OptionSetName": name,
                "Value": value,
                "Label": label(lbl),
                "MergeLabels": False,
            }
            try:
                backend.post("UpdateOptionValue", json_body=body, extra_headers=headers)
            except D365Error as exc:
                raise _attach_partial(exc, "update")
            completed.append(f"update:{value}")

    if delete:
        for value in delete:
            body = {"OptionSetName": name, "Value": value}
            try:
                backend.post("DeleteOptionValue", json_body=body, extra_headers=headers)
            except D365Error as exc:
                raise _attach_partial(exc, "delete")
            completed.append(f"delete:{value}")

    if reorder:
        body = {"OptionSetName": name, "Values": list(reorder)}
        try:
            backend.post("OrderOption", json_body=body, extra_headers=headers)
        except D365Error as exc:
            raise _attach_partial(exc, "reorder")
        completed.append("reorder")

    out: dict[str, Any] = {
        "updated": True,
        "name": name,
        "completed_steps": completed,
        "solution": solution,
    }
    maybe_publish(backend, out, publish)
    return out


def delete_optionset(
    backend: D365Backend,
    name: str,
    *,
    solution: str | None = None,
    check_dependencies: bool = False,
) -> dict[str, Any]:
    """Delete a custom global option set.

    Refuses if `IsCustomOptionSet=False` or `IsManaged=True`. Server
    rejects with 400 if any picklist still references the option set.

    Args:
        check_dependencies: When True, call RetrieveDependenciesForDelete
            before the DELETE and fold ``can_delete`` + ``blockers`` into the
            result. Informational only — does not abort the delete.
    """
    if not name:
        raise D365Error("name is required.")
    path = f"GlobalOptionSetDefinitions(Name='{name}')"
    was_dry = backend.dry_run
    backend.dry_run = False
    try:
        rb = as_dict(backend.get(
            path, params={"$select": "IsCustomOptionSet,IsManaged,MetadataId"},
        ))
    finally:
        backend.dry_run = was_dry
    if rb.get("IsCustomOptionSet") is False:
        raise D365Error(
            f"{name!r} is not a custom option set; refusing to delete.",
            code="NotCustomOptionSet",
        )
    if rb.get("IsManaged") is True:
        raise D365Error(
            f"{name!r} is managed; uninstall the parent solution to remove it.",
            code="ManagedOptionSet",
        )
    deps = None
    if check_dependencies:
        _mid = rb.get("MetadataId")
        if isinstance(_mid, str) and _mid:
            deps = dep_mod.dependencies_by_id(backend, _mid, 9, for_="delete", kind="optionset")
        else:
            deps = dep_mod.retrieve_dependencies(backend, "optionset", name, for_="delete")
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    preview = backend.delete(path, extra_headers=headers)
    if isinstance(preview, dict) and preview.get("_dry_run"):
        result: dict[str, Any] = {
            "_dry_run": True,
            "would_delete": True,
            "name": name,
            "solution": solution,
        }
    else:
        result = {
            "deleted": True,
            "name": name,
            "solution": solution,
        }
    if deps is not None:
        result["can_delete"] = deps["can_delete"]
        result["blockers"] = deps["blockers"]
    return result
