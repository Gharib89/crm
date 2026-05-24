"""Solution lifecycle: list / info / export / import."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict


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
    params = {"$filter": f"uniquename eq '{unique_name}'"}
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


def export_solution(
    backend: D365Backend,
    unique_name: str,
    output_path: str | Path,
    *,
    managed: bool = False,
    export_autonumbering: bool = False,
    export_calendar: bool = False,
    export_customizations: bool = False,
    export_email_tracking: bool = False,
    export_general: bool = False,
    export_isv_config: bool = False,
    export_marketing: bool = False,
    export_outlook_sync: bool = False,
    export_relationship_roles: bool = False,
    export_sales: bool = False,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Call ExportSolutionAsync, poll to completion, then DownloadSolutionExportData.

    Blocks until the async operation finishes (or timeout). Returns a dict with
    the on-disk path, byte count, async operation id, export job id, and total
    duration in ms.
    """
    import time as _time
    body: dict[str, Any] = {
        "SolutionName": unique_name,
        "Managed": managed,
        "ExportAutoNumberingSettings": export_autonumbering,
        "ExportCalendarSettings": export_calendar,
        "ExportCustomizationSettings": export_customizations,
        "ExportEmailTrackingSettings": export_email_tracking,
        "ExportGeneralSettings": export_general,
        "ExportIsvConfig": export_isv_config,
        "ExportMarketingSettings": export_marketing,
        "ExportOutlookSynchronizationSettings": export_outlook_sync,
        "ExportRelationshipRoles": export_relationship_roles,
        "ExportSales": export_sales,
    }

    started = _time.monotonic()
    resp = as_dict(backend.post("ExportSolutionAsync", json_body=body))
    if "_dry_run" in resp:
        return {**resp, "action": "ExportSolutionAsync"}

    async_op_id = resp.get("AsyncOperationId")
    export_job_id = resp.get("ExportJobId")
    if not async_op_id or not export_job_id:
        raise D365Error(
            "ExportSolutionAsync returned no AsyncOperationId / ExportJobId."
        )

    backend.poll_async_operation(async_op_id, timeout=timeout)

    dl = as_dict(backend.post(
        "DownloadSolutionExportData",
        json_body={"ExportJobId": export_job_id},
    ))
    encoded = dl.get("ExportSolutionFile")
    if not encoded:
        raise D365Error(
            "DownloadSolutionExportData returned no ExportSolutionFile payload."
        )
    data = base64.b64decode(encoded)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)

    duration_ms = int((_time.monotonic() - started) * 1000)
    return {
        "output": str(out),
        "bytes": len(data),
        "managed": managed,
        "solution": unique_name,
        "async_operation_id": async_op_id,
        "export_job_id": export_job_id,
        "duration_ms": duration_ms,
    }


def import_solution(
    backend: D365Backend,
    zip_path: str | Path,
    *,
    publish_workflows: bool = True,
    overwrite_unmanaged_customizations: bool = True,
    timeout: int | None = None,
    quiet: bool = False,
) -> dict[str, Any]:
    """Call ImportSolutionAsync and block on the resulting ImportJob.

    Returns a dict with import_job_id, async_operation_id, status='succeeded',
    progress percent, started_on / completed_on (from the importjobs row), and
    total wall-clock duration in ms.

    Raises D365Error on file-not-found, async failure, or timeout.
    """
    import sys as _sys
    import time as _time

    p = Path(zip_path)
    if not p.is_file():
        raise D365Error(f"Solution file not found: {zip_path}")
    encoded = base64.b64encode(p.read_bytes()).decode("ascii")
    import_job_id = _new_guid()
    body: dict[str, Any] = {
        "CustomizationFile": encoded,
        "PublishWorkflows": publish_workflows,
        "OverwriteUnmanagedCustomizations": overwrite_unmanaged_customizations,
        "ImportJobId": import_job_id,
    }

    started = _time.monotonic()
    resp = as_dict(backend.post("ImportSolutionAsync", json_body=body))
    if "_dry_run" in resp:
        return {**resp, "action": "ImportSolutionAsync", "import_job_id": import_job_id}

    async_op_id = resp.get("AsyncOperationId")
    if not async_op_id:
        raise D365Error("ImportSolutionAsync returned no AsyncOperationId.")

    last_progress: dict[str, float] = {"pct": -1.0}
    last_emit: dict[str, float] = {"t": 0.0}

    def _on_progress(pct: float, msg: str) -> None:
        if quiet:
            return
        now = _time.monotonic()
        # Always emit terminal (100%). Otherwise: rate-limit to once per second
        # AND drop duplicate-percent ticks within that window.
        if pct < 100.0:
            if (now - last_emit["t"]) < 1.0:
                return
            if pct == last_progress["pct"]:
                return
        last_progress["pct"] = pct
        last_emit["t"] = now
        _sys.stderr.write(f"[crm] import progress={pct:.1f}% status={msg}\n")

    try:
        backend.poll_async_operation(
            async_op_id,
            timeout=timeout,
            import_job_id=None if quiet else import_job_id,
            on_progress=None if quiet else _on_progress,
        )
    except D365Error as exc:
        raise D365Error(
            f"{exc} (import_job_id={import_job_id})",
            status=exc.status, code=exc.code, response_body=exc.response_body,
        ) from exc

    # Final importjobs read for the canonical progress + timestamps.
    job_row = as_dict(backend.get(
        f"importjobs({import_job_id})",
        params={"$select": "progress,startedon,completedon"},
    ))
    duration_ms = int((_time.monotonic() - started) * 1000)
    prog = job_row.get("progress")
    return {
        "import_job_id": import_job_id,
        "async_operation_id": async_op_id,
        "status": "succeeded",
        "progress": float(prog) if prog is not None else 100.0,
        "started_on": job_row.get("startedon"),
        "completed_on": job_row.get("completedon"),
        "duration_ms": duration_ms,
    }


def publish_all(backend: D365Backend) -> dict[str, Any]:
    """Call PublishAllXml — publishes all unpublished customizations.

    Action returns 204 No Content on success, so we synthesize a confirmation dict.
    """
    result = as_dict(backend.post("PublishAllXml"))
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
    if result:
        return result
    return {"published": True, "action": "PublishXml"}


def service_document(backend: D365Backend) -> dict[str, Any]:
    """GET the root service document — lists all entity sets exposed by the server."""
    return as_dict(backend.get(""))


def _new_guid() -> str:
    import uuid
    return str(uuid.uuid4())
