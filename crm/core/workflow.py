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


def set_workflow_state(
    backend: D365Backend,
    workflow_id: str,
    *,
    activate: bool,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """Activate or deactivate a workflow via PATCH on statecode/statuscode."""
    if not workflow_id:
        raise D365Error("workflow_id is required.")
    state, status = STATE_ACTIVATED if activate else STATE_DRAFT
    body: dict[str, Any] = {"statecode": state, "statuscode": status}
    backend.patch(
        f"workflows({workflow_id})",
        json_body=body,
        etag="*",
        caller_id=caller_id,
        caller_object_id=caller_object_id,
        suppress_duplicate_detection=suppress_duplicate_detection,
        bypass_custom_plugin_execution=bypass_custom_plugin_execution,
    )
    return {
        "workflow_id": workflow_id,
        "activated": activate,
        "statecode": state,
        "statuscode": status,
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
