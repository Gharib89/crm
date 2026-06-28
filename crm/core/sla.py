"""SLA operations.

SLAs (`sla` entity) are enforced by backing workflows — one per SLA item
(`slaitem.workflowid`). The SLA cannot be activated until every backing
workflow is active, and after a solution import those workflows may carry
compile errors (InvalidEntity / InvalidRelationship) the platform reports
only as a raw `ErrorMap Details: {...}` string.

Reference:
  https://learn.microsoft.com/dynamics365/customerengagement/on-premises/developer/entities/sla
"""

from __future__ import annotations

import re
from typing import Any, cast

from crm.core.workflow import STATE_ACTIVATED, set_workflow_state
from crm.utils.d365_backend import (
    D365Backend,
    D365Error,
    as_dict,
    normalize_guid,
)


# `ErrorMap Details: {Step1: ErrA, ErrB; Step2: ErrC}` — the platform's
# compile-error detail buried inside the activation failure message.
_ERROR_MAP_RE = re.compile(r"ErrorMap Details:\s*\{([^}]*)\}")


def parse_error_map(message: str) -> list[dict[str, Any]] | None:
    """Parse the platform's `ErrorMap Details: {...}` string into per-step
    entries `{"step": ..., "errors": [...]}`.

    Returns None when the message does not contain a parseable error map —
    callers fall back to the raw string so platform detail is never dropped.
    """
    match = _ERROR_MAP_RE.search(message)
    if not match:
        return None
    entries: list[dict[str, Any]] = []
    for chunk in match.group(1).split(";"):
        step, sep, errors = chunk.partition(":")
        if not sep or not step.strip():
            continue
        entries.append({
            "step": step.strip(),
            "errors": [e.strip() for e in errors.split(",") if e.strip()],
        })
    return entries or None


# SLA activation state pair (statecode, statuscode)
_SLA_ACTIVE = (1, 2)

SLAS_SET = "slas"
SLA_ITEMS_SET = "slaitems"
_SLA_ID = "slaid"
_SLA_ITEM_ID = "slaitemid"


def _sla_enabled_value(raw: Any) -> bool:
    """Read the effective IsSLAEnabled flag.

    EntityMetadata.IsSLAEnabled is a BooleanManagedProperty — returned as a
    ``{"Value": bool, ...}`` object — but tolerate a bare bool too."""
    if isinstance(raw, dict):
        return bool(cast("dict[str, Any]", raw).get("Value"))
    return bool(raw)


def _ensure_sla_enabled(
    backend: D365Backend, entity: str, *, solution: str | None = None,
) -> str:
    """Verify the target entity is SLA-enabled; enable + publish it if not.

    SLAs only apply to entities whose ``IsSLAEnabled`` metadata flag is set, so
    `sla create` guarantees it (issue #432). The flag is read live even under
    dry-run (reads-execute rule); the metadata write is suppressed in dry-run.

    Returns one of ``"already"`` (already enabled), ``"set"`` (flipped on now),
    or ``"would_set"`` (dry-run preview of the flip).
    """
    md = as_dict(backend.get(
        f"EntityDefinitions(LogicalName='{entity}')",
        params={"$select": "LogicalName,IsSLAEnabled"},
    ))
    if _sla_enabled_value(md.get("IsSLAEnabled")):
        return "already"
    if backend.dry_run:
        return "would_set"
    # Flip via the safe retrieve-merge-write PUT; a metadata change needs a
    # publish to take effect before the SLA can be applied.
    from crm.core import metadata_update
    metadata_update.update_entity(
        backend, entity, is_sla_enabled=True, solution=solution, publish=True,
    )
    return "set"


def _object_type_code(backend: D365Backend, entity: str) -> int:
    """Resolve an entity's integer ``ObjectTypeCode`` from its metadata.

    `sla.objecttypecode` is a Picklist (Edm.Int32), so the SLA's target entity
    is written there as its numeric ObjectTypeCode — the Web API rejects the
    bare logical name with 0x80048d19. Read separately from the IsSLAEnabled
    check because this value is needed *before* the POST (to build the body),
    while the enable-check runs *after* it (so a created SLA is recoverable if
    the metadata flip later fails)."""
    md = as_dict(backend.get(
        f"EntityDefinitions(LogicalName='{entity}')",
        params={"$select": "LogicalName,ObjectTypeCode"},
    ))
    otc = md.get("ObjectTypeCode")
    if not isinstance(otc, int):
        raise D365Error(
            f"Could not resolve ObjectTypeCode for entity {entity!r} "
            f"(got {otc!r}); cannot create an SLA for it."
        )
    return otc


def create_sla(
    backend: D365Backend,
    *,
    name: str,
    entity: str,
    applicable_from: str | None = None,
    business_hours_id: str | None = None,
    description: str | None = None,
    solution: str | None = None,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """Create an SLA (`sla` record) for a target entity and ensure that entity
    is SLA-enabled.

    `entity` is the target entity's logical name; its numeric ``ObjectTypeCode``
    is written to the SLA's ``objecttypecode`` (a Picklist attribute — the Web
    API rejects the bare logical-name string with 0x80048d19) and it is also the
    entity whose ``IsSLAEnabled`` flag is verified/set. `applicable_from` is
    the SLA's date-anchor *field* (e.g. ``createdon``); per-KPI FetchXML
    conditions belong on SLA items (`add_kpi`), not on the SLA record — the
    `sla` entity has no condition attribute.

    Dry-run returns ``{_dry_run, would_create, entity, sla_enabled}`` without
    POSTing; the ``IsSLAEnabled`` read still runs live so the preview is honest.
    """
    if not name:
        raise D365Error("name is required.")
    if not entity:
        raise D365Error("entity is required.")
    body: dict[str, Any] = {
        "name": name,
        "objecttypecode": _object_type_code(backend, entity),
    }
    if applicable_from:
        body["applicablefrom"] = applicable_from
    if description is not None:
        body["description"] = description
    if business_hours_id:
        bid = normalize_guid(business_hours_id)
        if bid is None:
            raise D365Error(
                f"Invalid GUID for business_hours_id: {business_hours_id!r}")
        body["businesshoursid@odata.bind"] = f"/calendars({bid})"

    admin = {
        "caller_id": caller_id,
        "caller_object_id": caller_object_id,
        "suppress_duplicate_detection": suppress_duplicate_detection,
        "bypass_custom_plugin_execution": bypass_custom_plugin_execution,
    }
    result = as_dict(backend.post(
        SLAS_SET, json_body=body,
        solution=solution, **admin,
    ))
    if result.get("_dry_run"):
        return {
            "_dry_run": True,
            "would_create": {"entity_set": SLAS_SET, "body": body},
            "entity": entity,
            "sla_enabled": _ensure_sla_enabled(backend, entity, solution=solution),
        }
    sla_id = result.get("_entity_id")
    try:
        sla_enabled = _ensure_sla_enabled(backend, entity, solution=solution)
    except D365Error as exc:
        # The SLA row already landed server-side; record it on the partial-failure
        # context (the optionsets multi-stage convention) so a failure to flip
        # IsSLAEnabled — e.g. lacking metadata-write privilege — leaves a
        # discoverable/recoverable SLA rather than a lost orphan.
        exc.completed_steps = [f"created sla {sla_id}"] if sla_id else []
        exc.stage = "enable_sla"
        raise
    out: dict[str, Any] = {
        "created": True,
        "name": name,
        "entity": entity,
        _SLA_ID: sla_id,
        "sla_enabled": sla_enabled,
        "solution": solution,
    }
    if not sla_id:
        out["sla_lookup_error"] = (
            "Could not parse slaid from response: "
            f"{result.get('_entity_id_url')!r}"
        )
    return out


def add_kpi(
    backend: D365Backend,
    *,
    sla_id: str,
    kpi: str,
    applicable_when: str,
    success_criteria: str,
    name: str | None = None,
    solution: str | None = None,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """Attach a KPI / SLA-item (`slaitem` record) to an existing SLA.

    `kpi` is the related KPI field the item tracks (written to ``relatedfield``)
    and doubles as the item ``name`` when `name` is omitted. `applicable_when`
    and `success_criteria` are FetchXML/condition strings written to the
    ApplicationRequired ``applicablewhenxml`` / ``successconditionsxml`` columns.

    Dry-run returns ``{_dry_run, would_create, sla_id}`` without POSTing.
    """
    sla_id = validate_sla_id(sla_id)
    if not kpi:
        raise D365Error("kpi is required.")
    if not applicable_when:
        raise D365Error("applicable_when is required.")
    if not success_criteria:
        raise D365Error("success_criteria is required.")
    item_name = name or kpi
    body: dict[str, Any] = {
        "name": item_name,
        f"{_SLA_ID}@odata.bind": f"/{SLAS_SET}({sla_id})",
        "relatedfield": kpi,
        "applicablewhenxml": applicable_when,
        "successconditionsxml": success_criteria,
    }
    admin = {
        "caller_id": caller_id,
        "caller_object_id": caller_object_id,
        "suppress_duplicate_detection": suppress_duplicate_detection,
        "bypass_custom_plugin_execution": bypass_custom_plugin_execution,
    }
    result = as_dict(backend.post(
        SLA_ITEMS_SET, json_body=body,
        solution=solution, **admin,
    ))
    if result.get("_dry_run"):
        return {
            "_dry_run": True,
            "would_create": {"entity_set": SLA_ITEMS_SET, "body": body},
            "sla_id": sla_id,
        }
    item_id = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        _SLA_ITEM_ID: item_id,
        "sla_id": sla_id,
        "name": item_name,
        "solution": solution,
    }
    if not item_id:
        out["slaitem_lookup_error"] = (
            "Could not parse slaitemid from response: "
            f"{result.get('_entity_id_url')!r}"
        )
    return out


def validate_sla_id(sla_id: str) -> str:
    """Return the canonical (lowercase, brace-free) form of sla_id, raising
    D365Error if it is not a GUID. Client-side only — the command layer calls
    this before building a backend; the canonical form keeps OData paths and
    $filter expressions stable."""
    rid = normalize_guid(sla_id)
    if rid is None:
        raise D365Error(f"Invalid GUID for sla_id: {sla_id!r}")
    return rid


def _fetch_plan(
    backend: D365Backend,
    sla_id: str,
    *,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch the SLA row and the distinct backing-workflow rows its items
    reference (order preserved)."""
    sla = as_dict(backend.get(
        f"slas({sla_id})", params={"$select": "slaid,name,statecode"},
        caller_id=caller_id, caller_object_id=caller_object_id,
    ))
    items = backend.get_collection(
        "slaitems",
        params={
            "$select": "slaitemid,name,_workflowid_value",
            "$filter": f"_slaid_value eq {sla_id}",
        },
        caller_id=caller_id, caller_object_id=caller_object_id,
    )
    workflow_ids: list[str] = []
    for item in items:
        wid = item.get("_workflowid_value")
        if wid and wid not in workflow_ids:
            workflow_ids.append(wid)
    workflows = [
        as_dict(backend.get(
            f"workflows({wid})",
            params={"$select": "workflowid,name,statecode,type"},
            caller_id=caller_id, caller_object_id=caller_object_id,
        ))
        for wid in workflow_ids
    ]
    return sla, workflows


def activate_sla(
    backend: D365Backend,
    sla_id: str,
    *,
    caller_id: str | None = None,
    caller_object_id: str | None = None,
    suppress_duplicate_detection: bool | None = None,
    bypass_custom_plugin_execution: bool | None = None,
) -> dict[str, Any]:
    """Activate an SLA's backing workflows, then the SLA itself.

    Each SLA item references a backing workflow; the SLA cannot activate
    until all of them are active. Already-active workflows are skipped
    (re-running is safe). On any workflow activation failure the remaining
    workflows are still attempted, the SLA is NOT touched, and the result
    reports per-workflow status with structured compile errors where the
    platform message parses (`parse_error_map`), raw otherwise. Dry-run
    resolves the plan with live GETs and returns a
    `{_dry_run, would_activate, ...}` preview without mutating.
    """
    sla_id = validate_sla_id(sla_id)

    sla, workflow_rows = _fetch_plan(
        backend, sla_id, caller_id=caller_id, caller_object_id=caller_object_id)

    if backend.dry_run:
        def _brief(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [{"workflow_id": r["workflowid"], "name": r.get("name")}
                    for r in rows]
        active = [r for r in workflow_rows
                  if r.get("statecode") == STATE_ACTIVATED[0]]
        draft = [r for r in workflow_rows
                 if r.get("statecode") != STATE_ACTIVATED[0]]
        return {
            "_dry_run": True,
            "sla_id": sla_id,
            "name": sla.get("name"),
            "would_activate": _brief(draft),
            "already_active": _brief(active),
            "would_activate_sla": sla.get("statecode") != _SLA_ACTIVE[0],
        }

    report: list[dict[str, Any]] = []
    any_failed = False
    for row in workflow_rows:
        wid = row["workflowid"]
        if row.get("statecode") == STATE_ACTIVATED[0]:
            report.append({"workflow_id": wid, "name": row.get("name"),
                           "status": "already_active"})
            continue
        try:
            set_workflow_state(
                backend, wid, activate=True,
                caller_id=caller_id, caller_object_id=caller_object_id,
                suppress_duplicate_detection=suppress_duplicate_detection,
                bypass_custom_plugin_execution=bypass_custom_plugin_execution,
            )
        except D365Error as exc:
            # Remaining workflows are still attempted so the report covers
            # every backing workflow; already-activated ones stay active
            # (matches platform/UI behavior — no rollback).
            any_failed = True
            entry: dict[str, Any] = {"workflow_id": wid, "name": row.get("name"),
                                     "status": "failed", "error": str(exc)}
            parsed = parse_error_map(str(exc))
            if parsed:
                entry["errors"] = parsed
            report.append(entry)
            continue
        report.append({"workflow_id": wid, "name": row.get("name"),
                       "status": "activated"})

    if any_failed:
        return {
            "sla_id": sla_id,
            "name": sla.get("name"),
            "sla_activated": False,
            "ui_activation_required": True,
            "workflows": report,
        }

    sla_already_active = sla.get("statecode") == _SLA_ACTIVE[0]
    if not sla_already_active:
        backend.patch(
            f"slas({sla_id})",
            json_body={"statecode": _SLA_ACTIVE[0], "statuscode": _SLA_ACTIVE[1]},
            etag="*",
            caller_id=caller_id, caller_object_id=caller_object_id,
            suppress_duplicate_detection=suppress_duplicate_detection,
            bypass_custom_plugin_execution=bypass_custom_plugin_execution,
        )
    return {
        "sla_id": sla_id,
        "name": sla.get("name"),
        "sla_activated": True,
        "sla_already_active": sla_already_active,
        "workflows": report,
    }
