"""Status/state option metadata writes.

The status/state analog of the global-option-set option CRUD in
``crm.core.optionsets``:

- ``add_status_value``  → ``InsertStatusValue`` (add a ``statuscode`` option tied
  to a ``statecode`` state).
- ``relabel_state_value`` → ``UpdateStateValue`` (relabel a ``statecode`` *state*
  option, e.g. rename Active/Inactive).

Custom state-model *transitions* (``StatusOptionMetadata.TransitionData``) are
deliberately absent: a live probe showed they cannot be written over the
Dataverse Web API. Option-level data is not applied by an attribute-definition
PUT (the server returns 204 but silently drops it), no Web API action accepts a
``TransitionData`` parameter, and the ``EntityMetadata.EnforceStateTransitions``
flag that activates a custom state model is application-set / read-only over the
Web API. So there is no headless Web API path; transitions stay app-authored.

References:
  https://learn.microsoft.com/power-apps/developer/data-platform/webapi/reference/insertstatusvalue
  https://learn.microsoft.com/power-apps/developer/data-platform/webapi/reference/updatestatevalue
"""

from __future__ import annotations

from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import label, maybe_publish
from crm.core import metadata_cache

# Status attributes are conventionally named on every entity; callers never vary
# them, so they are not exposed as parameters.
_STATUS_ATTR = "statuscode"
_STATE_ATTR = "statecode"


def add_status_value(
    backend: D365Backend,
    entity: str,
    *,
    state_code: int,
    label_text: str,
    value: int | None = None,
    description: str | None = None,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Add a ``statuscode`` option tied to a state via ``InsertStatusValue``.

    ``state_code`` is the ``statecode`` value the new status belongs to (e.g. 0
    for Active). ``value`` is optional — omit it to let the server assign the
    next value with the publisher prefix.
    """
    if not entity:
        raise D365Error("entity is required.")
    if not label_text:
        raise D365Error("label is required.")
    body: dict[str, Any] = {
        "EntityLogicalName": entity,
        "AttributeLogicalName": _STATUS_ATTR,
        "Label": label(label_text),
        "StateCode": state_code,
    }
    if value is not None:
        body["Value"] = value
    if description:
        body["Description"] = label(description)

    result = as_dict(backend.post("InsertStatusValue", json_body=body, solution=solution))
    if result.get("_dry_run"):
        result["would_add_status"] = True
        result["entity"] = entity
        result["state_code"] = state_code
        return result

    out: dict[str, Any] = {
        "added": True,
        "entity": entity,
        "attribute": _STATUS_ATTR,
        "state_code": state_code,
        "value": result.get("NewOptionValue", value),
        "solution": solution,
    }
    maybe_publish(backend, out, publish)
    if not backend.dry_run:
        metadata_cache.invalidate(backend.profile)
    return out


def relabel_state_value(
    backend: D365Backend,
    entity: str,
    *,
    value: int,
    label_text: str,
    description: str | None = None,
    merge_labels: bool = False,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Relabel a ``statecode`` state option via ``UpdateStateValue``.

    ``value`` is the ``statecode`` value to relabel (e.g. 1 for Inactive).
    """
    if not entity:
        raise D365Error("entity is required.")
    if not label_text:
        raise D365Error("label is required.")
    body: dict[str, Any] = {
        "EntityLogicalName": entity,
        "AttributeLogicalName": _STATE_ATTR,
        "Value": value,
        "Label": label(label_text),
        "MergeLabels": merge_labels,
    }
    if description:
        body["Description"] = label(description)

    result = as_dict(backend.post("UpdateStateValue", json_body=body, solution=solution))
    if result.get("_dry_run"):
        result["would_relabel_state"] = True
        result["entity"] = entity
        result["value"] = value
        return result

    out: dict[str, Any] = {
        "updated": True,
        "entity": entity,
        "attribute": _STATE_ATTR,
        "value": value,
        "solution": solution,
    }
    maybe_publish(backend, out, publish)
    if not backend.dry_run:
        metadata_cache.invalidate(backend.profile)
    return out
