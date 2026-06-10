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
import uuid
from typing import Any

from crm.core.workflow import STATE_ACTIVATED, set_workflow_state
from crm.utils.d365_backend import D365Backend, D365Error, as_dict


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


def validate_sla_id(sla_id: str) -> None:
    """Raise D365Error if sla_id is not a GUID. Client-side only — the command
    layer calls this before building a backend."""
    try:
        uuid.UUID(sla_id)
    except (ValueError, TypeError) as exc:
        raise D365Error(f"Invalid GUID for sla_id: {sla_id!r}") from exc


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
    items = as_dict(backend.get(
        "slaitems",
        params={
            "$select": "slaitemid,name,_workflowid_value",
            "$filter": f"_slaid_value eq {sla_id}",
        },
        caller_id=caller_id, caller_object_id=caller_object_id,
    )).get("value", [])
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
    validate_sla_id(sla_id)

    # Dry-run short-circuits ALL requests including GETs, so the plan is
    # always resolved live; the toggle is restored before any mutation.
    was_dry = backend.dry_run
    backend.dry_run = False
    try:
        sla, workflow_rows = _fetch_plan(
            backend, sla_id, caller_id=caller_id, caller_object_id=caller_object_id)
    finally:
        backend.dry_run = was_dry

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
