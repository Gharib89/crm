"""Workflow / process operations.

D365 workflows live in the `workflow` entity. This module wraps the common
read/state/trigger flows.

Reference:
  https://learn.microsoft.com/power-apps/developer/data-platform/webapi/use-web-api-actions
  https://learn.microsoft.com/dynamics365/customerengagement/on-premises/developer/entities/workflow
"""

from __future__ import annotations

import re
from typing import Any

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
    src_class = "XrmWorkflow" + src_id.replace("-", "")
    dst_class = "XrmWorkflow" + dst_id.replace("-", "")
    out = xaml.replace(src_class, dst_class)
    out = re.sub(rf"\b{re.escape(src_entity)}\b", dst_entity, out)
    return out


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
