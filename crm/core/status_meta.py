"""Status/state option metadata writes + custom state-model transitions.

The status/state analog of the global-option-set option CRUD in
``crm.core.optionsets``:

- ``add_status_value``  → ``InsertStatusValue`` (add a ``statuscode`` option tied
  to a ``statecode`` state).
- ``relabel_state_value`` → ``UpdateStateValue`` (relabel a ``statecode`` *state*
  option, e.g. rename Active/Inactive).
- ``set_status_transitions`` writes the ``StatusOptionMetadata.TransitionData``
  XML on the ``statuscode`` attribute so an allowed-transition state model can be
  defined headlessly. The companion ``EntityMetadata.EnforceStateTransitions``
  flag that *activates* enforcement is application-set / read-only over the Web
  API, so toggling enforcement is out of the CLI's reach (callers configure it in
  the app); this verb only writes the transition graph.

References:
  https://learn.microsoft.com/power-apps/developer/data-platform/webapi/reference/insertstatusvalue
  https://learn.microsoft.com/power-apps/developer/data-platform/webapi/reference/updatestatevalue
  https://learn.microsoft.com/power-apps/developer/data-platform/define-custom-state-model-transitions
"""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import quoteattr

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import label, maybe_publish
from crm.core import metadata_cache

# The allowed-transitions XML namespace Dataverse stamps on TransitionData.
_TRANSITION_NS = "https://schemas.microsoft.com/crm/2009/WebServices"

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

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post("InsertStatusValue", json_body=body, extra_headers=headers))
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

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post("UpdateStateValue", json_body=body, extra_headers=headers))
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


def _transition_xml(source: int, targets: list[int]) -> str:
    """Build the ``<allowedtransitions>`` XML document for one source status."""
    rows = "".join(
        f"<allowedtransition sourcestatusid={quoteattr(str(source))} "
        f"tostatusid={quoteattr(str(t))} />"
        for t in targets
    )
    return f'<allowedtransitions xmlns="{_TRANSITION_NS}">{rows}</allowedtransitions>'


def set_status_transitions(
    backend: D365Backend,
    entity: str,
    *,
    transitions: list[tuple[int, int]],
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Write allowed status-reason transitions onto the ``statuscode`` attribute.

    Each ``(source, target)`` pair declares that a record at ``statuscode``
    ``source`` may move to ``statuscode`` ``target``. Sources not mentioned keep
    their existing ``TransitionData`` untouched (retrieve-merge-write). Both
    values must be existing ``statuscode`` options.

    Note: this only writes the transition graph. ``EnforceStateTransitions`` —
    the entity flag that *activates* enforcement — is application-set and
    read-only over the Web API, so it stays out of the CLI's reach.
    """
    if not entity:
        raise D365Error("entity is required.")
    if not transitions:
        raise D365Error("at least one --transition is required.")

    cast = "Microsoft.Dynamics.CRM.StatusAttributeMetadata"
    path = (
        f"EntityDefinitions(LogicalName='{entity}')"
        f"/Attributes(LogicalName='{_STATUS_ATTR}')/{cast}"
    )
    # GET always runs (reads are side-effect-free, even under --dry-run) so the
    # full attribute body is available for the retrieve-merge-write PUT.
    current = as_dict(backend.get(path, params={"$expand": "OptionSet"}))
    optionset = as_dict(current.get("OptionSet"))
    options: list[dict[str, Any]] = list(optionset.get("Options") or [])
    valid = {opt.get("Value") for opt in options}

    by_source: dict[int, list[int]] = {}
    for source, target in transitions:
        if source not in valid:
            raise D365Error(
                f"statuscode value {source} is not an option on {entity!r}."
            )
        if target not in valid:
            raise D365Error(
                f"statuscode value {target} is not an option on {entity!r}."
            )
        by_source.setdefault(source, []).append(target)

    for opt in options:
        src = opt.get("Value")
        if src in by_source:
            opt["TransitionData"] = _transition_xml(src, by_source[src])

    set_sources = sorted(by_source)
    if backend.dry_run:
        return {
            "_dry_run": True,
            "would_set_transitions": True,
            "method": "PUT",
            "path": path,
            "entity": entity,
            "attribute": _STATUS_ATTR,
            "transitions_set": set_sources,
        }

    headers: dict[str, str] = {"MSCRM.MergeLabels": "true"}
    if solution:
        headers["MSCRM.SolutionUniqueName"] = solution
    backend.put(path, json_body=current, extra_headers=headers)

    out: dict[str, Any] = {
        "updated": True,
        "entity": entity,
        "attribute": _STATUS_ATTR,
        "transitions_set": set_sources,
        "solution": solution,
    }
    maybe_publish(backend, out, publish)
    if not backend.dry_run:
        metadata_cache.invalidate(backend.profile)
    return out
