"""Workflow / process operations.

D365 workflows live in the `workflow` entity. This module wraps the common
read/state/trigger flows.

Reference:
  https://learn.microsoft.com/power-apps/developer/data-platform/webapi/use-web-api-actions
  https://learn.microsoft.com/dynamics365/customerengagement/on-premises/developer/entities/workflow
"""

from __future__ import annotations

import json as _json
import re
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from crm.core import entity as entity_ops
from crm.core import solution as solution_ops
from crm.utils.d365_backend import D365Backend, D365Error, as_dict


# `workflow.category` values per the SDK
CATEGORY_WORKFLOW = 0
CATEGORY_DIALOG = 1
CATEGORY_BUSINESS_RULE = 2
CATEGORY_ACTION = 3
CATEGORY_BPF = 4
CATEGORY_MODERN_FLOW = 5

# `workflow.type` values
TYPE_DEFINITION = 1
TYPE_ACTIVATION = 2

# `workflow.mode` values
MODE_BACKGROUND = 0
MODE_REALTIME = 1

# Migration-readiness blocker codes (issue #199). Anchored to the MS Learn
# capability table "Replace classic Dataverse workflows with flows": cloud
# flows cannot run synchronously, cannot use wait conditions, and cannot run
# custom (non-out-of-box) workflow activities.
MIGRATION_BLOCKER_REAL_TIME = "real_time"
MIGRATION_BLOCKER_WAIT = "wait_condition"
MIGRATION_BLOCKER_CUSTOM_ACTIVITY = "custom_activity"

# The single assembly that hosts out-of-box workflow activities. Any
# ActivityReference whose AssemblyQualifiedName names a different assembly is a
# custom workflow activity (verified live: first-party Microsoft.Dynamics.* /
# Microsoft.PowerPages.* solution activities and third-party assemblies alike).
_OOB_ACTIVITY_ASSEMBLY = "Microsoft.Crm.Workflow"

# Classic wait/wait-timeout conditions compile to the `Postpone` activity in the
# Microsoft.Xrm.Sdk.Workflow.Activities namespace (alias `mxswa` in practice).
# Match the element local name with or without a namespace prefix so a
# default-namespaced `<Postpone>` is still caught; ground-truthed against live
# workflows (which always carry the `mxswa:` prefix).
_WAIT_ACTIVITY_RE = re.compile(r"<(?:\w+:)?Postpone[\s/>]")
# `AssemblyQualifiedName="<type>, <assembly>, Version=..."` — capture the assembly.
_ACTIVITY_ASSEMBLY_RE = re.compile(r'AssemblyQualifiedName="[^,"]+,\s*([^,"]+)')

# Activation state pairs
STATE_DRAFT = (0, 1)        # (statecode, statuscode)
STATE_ACTIVATED = (1, 2)


def retarget_xaml(
    xaml: str,
    *,
    src_entity: str,
    dst_entity: str,
    src_id: str,
    dst_id: str,
) -> str:
    """Rewrite a workflow xaml definition to target a new entity and a new id.

    - `XrmWorkflow<src_id-no-dashes>` (the `x:Class` and the matching
      `<this:XrmWorkflow...>` element tags) -> `XrmWorkflow<dst_id-no-dashes>`.
    - Whole-token references to `src_entity` -> `dst_entity`. Word-boundary
      matching protects tokens that merely start with the entity name
      (e.g. `cwx_ticketcategory` is left intact).
    Attribute logical names are not touched.
    """
    src_class = "XrmWorkflow" + src_id.replace("-", "").lower()
    dst_class = "XrmWorkflow" + dst_id.replace("-", "").lower()
    out = xaml.replace(src_class, dst_class)
    out = re.sub(rf"\b{re.escape(src_entity)}\b", dst_entity, out)
    return out


_WORKFLOW_SELECT = (
    "workflowid,name,category,primaryentity,type,xaml,"
    "mode,scope,ondemand,subprocess,languagecode,statecode,statuscode,"
    "triggeroncreate,triggerondelete,triggeronupdateattributelist,"
    "asyncautodelete,runas,syncworkflowlogonfailure,istransacted"
)


def get_workflow(backend: D365Backend, workflow_id: str) -> dict[str, Any]:
    """Retrieve a workflow definition (type=1) including its xaml.

    Raises if the id points at a type=2 activation copy — callers want the
    definition the server compiles from.
    """
    if not workflow_id:
        raise D365Error("workflow_id is required.")
    result = as_dict(backend.get(
        f"workflows({workflow_id})", params={"$select": _WORKFLOW_SELECT}
    ))
    if result.get("type") == TYPE_ACTIVATION:
        raise D365Error(
            f"Workflow {workflow_id} is a type=2 activation copy; "
            "pass the type=1 definition id instead."
        )
    return result


# Categories clone supports fully via xaml-retarget alone.
_TIER1_CATEGORIES = {CATEGORY_WORKFLOW, CATEGORY_BUSINESS_RULE}
# Categories that need more than xaml (verified live in Task 7); refuse for now.
_NEEDS_MORE_CATEGORIES = {CATEGORY_ACTION, CATEGORY_BPF}
# Categories out of scope for clone entirely.
_UNSUPPORTED_CATEGORIES = {CATEGORY_DIALOG, CATEGORY_MODERN_FLOW}

_CLONE_COPY_FIELDS = (
    "category", "mode", "scope", "ondemand", "subprocess", "languagecode",
    "triggeroncreate", "triggerondelete", "triggeronupdateattributelist",
    "asyncautodelete", "runas", "syncworkflowlogonfailure", "istransacted",
)
COMPONENT_TYPE_WORKFLOW = 29


def clone_workflow_to_entity(
    backend: D365Backend,
    workflow_id: str,
    target_entity: str,
    *,
    name: str | None = None,
    activate: bool = True,
    solution: str | None = None,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """Clone a workflow definition onto another entity.

    Retargets the xaml entity references and the `XrmWorkflow<id>` class to a
    fresh id, creates the clone as a draft (explicit-GUID upsert), then
    optionally activates it (which compiles the xaml). Tier-1 categories
    (classic workflow, business rule) are fully supported; action/BPF fail
    loudly until Task 7 confirms their full create path.
    """
    if not target_entity:
        raise D365Error("target_entity is required.")
    src = get_workflow(backend, workflow_id)
    category = src.get("category")

    if category in _UNSUPPORTED_CATEGORIES:
        raise D365Error(
            f"Cloning category {category} (dialog/modern flow) is not supported."
        )
    if category in _NEEDS_MORE_CATEGORIES:
        raise D365Error(
            f"Cloning category {category} (action/BPF) is not yet supported: it "
            "needs more than an xaml retarget (sdkmessage / stage records). "
            "Use solution export/import for now."
        )
    if category not in _TIER1_CATEGORIES:
        raise D365Error(f"Unknown workflow category {category}; cannot clone.")

    new_id = str(uuid4())
    new_xaml = retarget_xaml(
        src.get("xaml", ""),
        src_entity=src["primaryentity"], dst_entity=target_entity,
        src_id=workflow_id, dst_id=new_id,
    )
    payload: dict[str, Any] = {k: src[k] for k in _CLONE_COPY_FIELDS if k in src}
    payload.update({
        "name": name or f"{src.get('name', 'Workflow')} (Clone)",
        "primaryentity": target_entity,
        "type": TYPE_DEFINITION,
        "xaml": new_xaml,
    })
    entity_ops.upsert(
        backend, "workflows", new_id, payload,
        caller_id=caller_id, caller_object_id=caller_object_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )

    activated = False
    if activate:
        set_workflow_state(
            backend, new_id, activate=True,
            caller_id=caller_id, caller_object_id=caller_object_id,
            suppress_duplicate_detection=suppress_duplicate_detection,
            bypass_custom_plugin_execution=bypass_custom_plugin_execution,
        )
        activated = True

    if solution:
        solution_ops.add_solution_component(
            backend, solution=solution,
            component_id=new_id, component_type=COMPONENT_TYPE_WORKFLOW,
        )

    return {
        "workflow_id": new_id,
        "source_id": workflow_id,
        "name": payload["name"],
        "primaryentity": target_entity,
        "category": category,
        "activated": activated,
        "solution": solution,
    }


_EXPORT_FIELDS = (
    "workflowid", "name", "category", "primaryentity", "type", "xaml",
    "mode", "scope", "ondemand", "subprocess", "languagecode",
    "triggeroncreate", "triggerondelete", "triggeronupdateattributelist",
    "asyncautodelete", "runas", "syncworkflowlogonfailure", "istransacted",
)


def export_workflow(
    backend: D365Backend, workflow_id: str, *, out_path: str | None = None
) -> dict[str, Any]:
    """Retrieve a workflow definition and (optionally) write it to a JSON file."""
    wf = get_workflow(backend, workflow_id)
    record = {k: wf[k] for k in _EXPORT_FIELDS if k in wf}
    if out_path:
        Path(out_path).write_text(_json.dumps(record, indent=2), encoding="utf-8")
    return {"workflow_id": workflow_id, "out_path": out_path, "record": record}


def import_workflow(
    backend: D365Backend,
    *,
    file_path: str,
    activate: bool = False,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """Upsert a workflow definition from a previously exported JSON file."""
    parsed: Any = _json.loads(Path(file_path).read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise D365Error(f"{file_path} must contain a JSON object, not {type(parsed).__name__}.")
    record = cast(dict[str, Any], parsed)
    wf_id = record.get("workflowid")
    if not wf_id:
        raise D365Error(f"{file_path} has no 'workflowid'.")
    payload = {k: v for k, v in record.items() if k != "workflowid"}
    payload["type"] = TYPE_DEFINITION
    entity_ops.upsert(
        backend, "workflows", wf_id, payload,
        caller_id=caller_id, caller_object_id=caller_object_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )
    activated = False
    if activate:
        set_workflow_state(
            backend, wf_id, activate=True,
            caller_id=caller_id, caller_object_id=caller_object_id,
            suppress_duplicate_detection=suppress_duplicate_detection,
            bypass_custom_plugin_execution=bypass_custom_plugin_execution,
        )
        activated = True
    return {"workflow_id": wf_id, "activated": activated}


def list_workflows(
    backend: D365Backend,
    *,
    category: int | None = None,
    primary_entity: str | None = None,
    activated_only: bool = False,
    on_demand_only: bool = False,
) -> list[dict[str, Any]]:
    """Return `workflow` rows filtered to definition records (type=1).

    Activation records (type=2) are internal copies the server creates when a
    workflow is activated; callers want the definition.
    """
    filters: list[str] = [f"type eq {TYPE_DEFINITION}"]
    if category is not None:
        filters.append(f"category eq {category}")
    if primary_entity:
        filters.append(f"primaryentity eq '{primary_entity}'")
    if activated_only:
        filters.append(f"statecode eq {STATE_ACTIVATED[0]}")
    if on_demand_only:
        filters.append("ondemand eq true")
    params: dict[str, str] = {
        "$select": "workflowid,name,category,primaryentity,statecode,statuscode,ondemand,type",
        "$filter": " and ".join(filters),
    }
    result = backend.get("workflows", params=params)
    return as_dict(result).get("value", [])


def assess_workflow_migration(row: dict[str, Any]) -> dict[str, Any]:
    """Per-workflow flow-migration readiness verdict for one category-0 row.

    Pure and deterministic given the row (which must include the heuristic
    inputs `mode`, `statecode`, and `xaml`). Best-effort: blockers are
    "needs redesign" signals from the MS capability table, not proofs of
    impossibility.
    """
    xaml = row.get("xaml") or ""
    blockers: list[str] = []
    if row.get("mode") == MODE_REALTIME:
        blockers.append(MIGRATION_BLOCKER_REAL_TIME)
    if _WAIT_ACTIVITY_RE.search(xaml):
        blockers.append(MIGRATION_BLOCKER_WAIT)
    if any(asm.strip() != _OOB_ACTIVITY_ASSEMBLY
           for asm in _ACTIVITY_ASSEMBLY_RE.findall(xaml)):
        blockers.append(MIGRATION_BLOCKER_CUSTOM_ACTIVITY)
    return {
        "id": row.get("workflowid"),
        "name": row.get("name"),
        "primaryentity": row.get("primaryentity"),
        "state": "activated" if row.get("statecode") == STATE_ACTIVATED[0] else "draft",
        "mode": "realtime" if row.get("mode") == MODE_REALTIME else "background",
        "verdict": "blocked" if blockers else "ready",
        "blockers": blockers,
    }


_MIGRATION_ASSESS_SELECT = "workflowid,name,primaryentity,mode,statecode,xaml"


def assess_workflow_migrations(
    backend: D365Backend,
    *,
    primary_entity: str | None = None,
    max_pages: int = 100,
) -> list[dict[str, Any]]:
    """Assess every category-0 workflow definition for flow-migration readiness.

    Selects the definition rows (type=1, category=0) with the heuristic inputs
    in one query and follows `@odata.nextLink` up to `max_pages` so large orgs
    are fully covered. Returns one `assess_workflow_migration` verdict per row.
    Read-only.
    """
    filters = [f"type eq {TYPE_DEFINITION}", f"category eq {CATEGORY_WORKFLOW}"]
    if primary_entity:
        escaped = primary_entity.replace("'", "''")
        filters.append(f"primaryentity eq '{escaped}'")
    params: dict[str, str] = {
        "$select": _MIGRATION_ASSESS_SELECT,
        "$filter": " and ".join(filters),
    }
    rows: list[dict[str, Any]] = []
    page = as_dict(backend.get("workflows", params=params))
    pages_consumed = 1
    while True:
        value = page.get("value", [])
        if isinstance(value, list):
            rows.extend(cast(list[dict[str, Any]], value))
        next_link = page.get("@odata.nextLink")
        if not isinstance(next_link, str) or not next_link or pages_consumed >= max_pages:
            break
        page = as_dict(backend.get(next_link))
        pages_consumed += 1
    return [assess_workflow_migration(r) for r in rows]


def set_workflow_state(
    backend: D365Backend,
    workflow_id: str,
    *,
    activate: bool,
    auto_resolve_parent: bool = True,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """Activate or deactivate a workflow via PATCH on statecode/statuscode.

    A type=2 activation-record id is rejected by the server with `0x80045003`;
    with `auto_resolve_parent` (default) the parent definition is resolved via
    `parentworkflowid` and the PATCH retried against it, recorded in the
    returned `resolved_from_activation_id` (None when no redirect happened).
    If resolution finds no parent, the original rejection is re-raised. The
    live path stays error-driven — no extra GET when the id is already a
    definition. Dry-run instead resolves proactively with a real GET (the
    short-circuited PATCH can never raise `0x80045003`), so the preview keys
    on the GUID the live run would PATCH.
    """
    if not workflow_id:
        raise D365Error("workflow_id is required.")
    state, status = STATE_ACTIVATED if activate else STATE_DRAFT
    body: dict[str, Any] = {"statecode": state, "statuscode": status}

    def _patch(target_id: str) -> None:
        backend.patch(
            f"workflows({target_id})",
            json_body=body,
            etag="*",
            caller_id=caller_id,
            caller_object_id=caller_object_id,
            suppress_duplicate_detection=suppress_duplicate_detection,
            bypass_custom_plugin_execution=bypass_custom_plugin_execution,
        )

    target_id = workflow_id
    resolved_from: str | None = None

    if auto_resolve_parent and backend.dry_run:
        parent = _resolve_parent_workflow_id(
            backend, workflow_id,
            caller_id=caller_id, caller_object_id=caller_object_id)
        if parent:
            target_id, resolved_from = parent, workflow_id

    try:
        _patch(target_id)
    except D365Error as exc:
        if not (auto_resolve_parent and exc.code == ACTIVATION_PATCH_ERROR_CODE):
            raise
        parent = _resolve_parent_workflow_id(
            backend, workflow_id,
            caller_id=caller_id, caller_object_id=caller_object_id)
        if not parent:
            raise
        _patch(parent)
        target_id, resolved_from = parent, workflow_id

    return {
        "workflow_id": target_id,
        "activated": activate,
        "statecode": state,
        "statuscode": status,
        "resolved_from_activation_id": resolved_from,
    }


# Server error code returned when a state PATCH targets a type=2 activation row.
ACTIVATION_PATCH_ERROR_CODE = "0x80045003"
# Server error code returned when a DELETE targets a type=2 activation row.
ACTIVATION_DELETE_ERROR_CODE = "0x80045004"


def _resolve_parent_workflow_id(
    backend: D365Backend,
    workflow_id: str,
    *,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
) -> str | None:
    """Best-effort single GET of the activation row's `parentworkflowid` lookup.

    Returns the parent definition GUID, or None if the GET fails or the row has
    no parent. Never raises — callers sit on an already-failed error path (or a
    dry-run preview), so it must not mask the original error. Impersonation
    args keep the lookup under the same identity as the caller's state change.
    """
    try:
        row = as_dict(backend.get(
            f"workflows({workflow_id})",
            params={"$select": "parentworkflowid"},
            caller_id=caller_id,
            caller_object_id=caller_object_id,
        ))
    except D365Error:
        return None
    parent = row.get("_parentworkflowid_value") or row.get("parentworkflowid")
    return parent or None


def activation_record_hint(
    backend: D365Backend, workflow_id: str, exc: D365Error
) -> str | None:
    """If `exc` is the 'cannot update a workflow activation' rejection, return a
    hint pointing at the parent draft GUID; else None.

    Only `0x80045003` (a state PATCH against a type=2 activation row) is handled.
    Resolves the activation row's `parentworkflowid` with a single GET; if that
    lookup fails or yields no parent, falls back to a generic hint rather than
    masking the original error or raising again.
    """
    if exc.code != ACTIVATION_PATCH_ERROR_CODE:
        return None
    generic = (
        f"{workflow_id} is a workflow activation record; "
        "pass the parent draft (type=1) GUID instead."
    )
    parent = _resolve_parent_workflow_id(backend, workflow_id)
    if not parent:
        return generic
    return (
        f"{workflow_id} is a workflow activation record. "
        f"Pass the parent draft GUID instead: {parent}"
    )


def activation_delete_hint(
    backend: D365Backend, workflow_id: str, exc: D365Error
) -> str | None:
    """If `exc` is the 'cannot delete a workflow activation' rejection, return a
    hint pointing at deactivating the parent definition; else None.

    Only `0x80045004` (a DELETE against a type=2 activation row) is handled.
    Resolves the activation row's `parentworkflowid` with a single GET; if that
    lookup fails or yields no parent, falls back to a generic hint referencing
    `parentworkflowid` by name rather than masking the original error or raising
    again. D365 blocks deleting activation rows directly — deactivating the
    parent definition removes the activation.
    """
    if exc.code != ACTIVATION_DELETE_ERROR_CODE:
        return None
    generic = (
        f"{workflow_id} is a workflow activation record (type=2) that cannot be "
        "deleted directly. Deactivate its parent definition instead (the parent "
        "GUID is on this row's parentworkflowid lookup): "
        "crm workflow deactivate <parent-guid>"
    )
    parent = _resolve_parent_workflow_id(backend, workflow_id)
    if not parent:
        return generic
    return (
        f"{workflow_id} is a workflow activation record (type=2) that cannot be "
        "deleted directly. Deactivate its parent definition instead, which "
        f"removes the activation: crm workflow deactivate {parent}"
    )


_DELETE_RESOLVE_SELECT = "name,type,statecode,parentworkflowid"


def resolve_delete_target(
    backend: D365Backend,
    workflow_id: str,
    *,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
) -> dict[str, Any]:
    """Resolve the definition a `workflow delete` operates on.

    GETs the row; a type=2 activation record is resolved via its
    `parentworkflowid` lookup to the live parent definition (deleting the
    definition removes the activation record server-side). Returns
    ``{workflow_id, name, statecode, resolved_from_activation_id}`` for the
    definition to delete. An activation record whose parent is null or
    dangling has no supported Web API delete path (ADR 0003) and raises a
    clean operational error before any mutation.

    The GETs run live even under dry-run (the short-circuit is toggled off
    around them) so the preview keys on the GUID a live run would delete.
    """
    if not workflow_id:
        raise D365Error("workflow_id is required.")
    return _resolve_delete_target_live(
        backend, workflow_id,
        caller_id=caller_id, caller_object_id=caller_object_id,
    )


def _resolve_delete_target_live(
    backend: D365Backend,
    workflow_id: str,
    *,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
) -> dict[str, Any]:
    row = as_dict(backend.get(
        f"workflows({workflow_id})",
        params={"$select": _DELETE_RESOLVE_SELECT},
        caller_id=caller_id,
        caller_object_id=caller_object_id,
    ))
    if row.get("type") != TYPE_ACTIVATION:
        return {
            "workflow_id": workflow_id,
            "name": row.get("name"),
            "statecode": row.get("statecode"),
            "resolved_from_activation_id": None,
        }
    no_parent = D365Error(
        f"{workflow_id} is an activation record with no live parent "
        "definition; there is no supported Web API path to delete it — "
        "use the D365 UI."
    )
    parent = row.get("_parentworkflowid_value") or row.get("parentworkflowid")
    if not parent:
        raise no_parent
    try:
        parent_row = as_dict(backend.get(
            f"workflows({parent})",
            params={"$select": "name,type,statecode"},
            caller_id=caller_id,
            caller_object_id=caller_object_id,
        ))
    except D365Error as exc:
        if exc.status == 404:
            raise no_parent from exc
        raise
    return {
        "workflow_id": parent,
        "name": parent_row.get("name"),
        "statecode": parent_row.get("statecode"),
        "resolved_from_activation_id": workflow_id,
    }


def delete_workflow(
    backend: D365Backend,
    workflow_id: str,
    *,
    resolved: dict[str, Any] | None = None,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """Delete a workflow definition, deactivating it first when active.

    An activation-record GUID resolves to its parent definition via
    `resolve_delete_target`; `resolved` lets the command layer pass the
    result it already fetched for the confirmation prompt, skipping a second
    lookup. Not atomic: when the deactivate lands but the delete fails there
    is no rollback — the raised error states the definition was deactivated
    and remains a draft. Dry-run resolves live and returns a preview
    (`{_dry_run, would_delete, would_deactivate, ...}`) without mutating.
    """
    target = resolved or resolve_delete_target(
        backend, workflow_id,
        caller_id=caller_id, caller_object_id=caller_object_id,
    )
    target_id = target["workflow_id"]
    needs_deactivate = target.get("statecode") == STATE_ACTIVATED[0]
    if backend.dry_run:
        return {
            "_dry_run": True,
            "would_delete": target_id,
            "would_deactivate": needs_deactivate,
            "workflow_id": target_id,
            "name": target.get("name"),
            "resolved_from_activation_id": target.get("resolved_from_activation_id"),
        }
    deactivated = False
    if needs_deactivate:
        set_workflow_state(
            backend, target_id, activate=False, auto_resolve_parent=False,
            caller_id=caller_id, caller_object_id=caller_object_id,
            suppress_duplicate_detection=suppress_duplicate_detection,
            bypass_custom_plugin_execution=bypass_custom_plugin_execution,
        )
        deactivated = True
    try:
        backend.delete(
            f"workflows({target_id})",
            caller_id=caller_id,
            caller_object_id=caller_object_id,
            suppress_duplicate_detection=suppress_duplicate_detection,
            bypass_custom_plugin_execution=bypass_custom_plugin_execution,
        )
    except D365Error as exc:
        if not deactivated:
            raise
        raise D365Error(
            f"Workflow definition {target_id} was deactivated but the delete "
            f"failed; it remains a draft (no rollback). {exc}",
            status=exc.status, code=exc.code, response_body=exc.response_body,
        ) from exc
    return {
        "deleted": True,
        "workflow_id": target_id,
        "name": target.get("name"),
        "deactivated": deactivated,
        "resolved_from_activation_id": target.get("resolved_from_activation_id"),
    }


def execute_workflow(
    backend: D365Backend,
    workflow_id: str,
    target_record_id: str,
    *,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """Trigger an on-demand workflow against a target record.

    `ExecuteWorkflow` is a bound action on the `workflow` entity set.
    """
    if not workflow_id or not target_record_id:
        raise D365Error("workflow_id and target_record_id are required.")
    path = (
        f"workflows({workflow_id})/Microsoft.Dynamics.CRM.ExecuteWorkflow"
    )
    body: dict[str, Any] = {"EntityId": target_record_id}
    result = as_dict(backend.post(
        path,
        json_body=body,
        caller_id=caller_id,
        caller_object_id=caller_object_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    ))
    return {
        "workflow_id": workflow_id,
        "target_id": target_record_id,
        "async_operation_id": result.get("Id"),
        "raw": result,
    }
