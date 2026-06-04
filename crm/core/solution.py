"""Solution lifecycle: create-publisher / create / list / info / export / import."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from crm.core import entity
from crm.utils.d365_backend import D365Backend, D365Error, as_dict


# ── Create publisher / solution ─────────────────────────────────────────────
#
# Both mirror appmodule.create_app: a forced-real existence GET (accurate even
# under --dry-run), --if-exists error|skip semantics, then a 204-create via
# entity.create(return_record=False) whose OData-EntityId GUID is synthesised
# into the returned record. on-prem 9.1 publisher/solution contract is verified
# against the op-9-1 docs (customizationprefix 2-8 alnum not 'mscrm';
# customizationoptionvalueprefix 10000-99999; solution publisherid@odata.bind).


def _validate_customization_prefix(prefix: str) -> None:
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
    un_lit = unique_name.replace("'", "''")
    rows = as_dict(backend.get(
        "publishers",
        params={"$filter": f"uniquename eq '{un_lit}'", "$select": "publisherid"},
    )).get("value", [])
    if not rows:
        raise D365Error(f"Publisher not found: {unique_name}", code="PublisherNotFound")
    pub_id = rows[0].get("publisherid")
    if not isinstance(pub_id, str):
        raise D365Error(f"Publisher {unique_name!r} returned no publisherid.")
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
    _validate_customization_prefix(prefix)
    if not 10000 <= option_value_prefix <= 99999:
        raise D365Error(
            f"option_value_prefix must be in the range 10000-99999; got {option_value_prefix}."
        )
    if if_exists not in ("error", "skip"):
        raise D365Error("if_exists must be 'error' or 'skip'.")

    # Force a real read even under dry-run: idempotent, and an accurate preview
    # (_exists/would_skip) needs the live answer (cf. appmodule.create_app).
    un_lit = name.replace("'", "''")
    was_dry = backend.dry_run
    backend.dry_run = False
    try:
        existing = as_dict(backend.get(
            "publishers",
            params={"$filter": f"uniquename eq '{un_lit}'",
                    "$select": "publisherid,uniquename"},
        )).get("value", [])
    finally:
        backend.dry_run = was_dry
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
    pub_id = result.get("id")
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

    sol_lit = name.replace("'", "''")
    was_dry = backend.dry_run
    backend.dry_run = False
    try:
        existing = as_dict(backend.get(
            "solutions",
            params={"$filter": f"uniquename eq '{sol_lit}'",
                    "$select": "solutionid,uniquename"},
        )).get("value", [])
        # The skip/error short-circuit below only fires on a real (non-dry) run. Every
        # path that reaches the POST — including the dry-run preview — needs the
        # publisher id to build the bind, so resolve it now under the forced-real read
        # unless we already know we'll short-circuit.
        will_short_circuit = bool(existing) and not was_dry
        pub_id = publisher_id
        if not will_short_circuit and not pub_id:
            if not publisher_unique_name:
                raise D365Error(
                    "a publisher is required: pass publisher_unique_name or publisher_id.")
            pub_id = _resolve_publisher_id(backend, publisher_unique_name)
    finally:
        backend.dry_run = was_dry
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
    sol_id = result.get("id")
    out: dict[str, Any] = {
        "created": True, "uniquename": name, "friendlyname": friendly_name or name,
        "version": version, "publisherid": pub_id, "solutionid": sol_id,
    }
    if not sol_id:
        out["solution_lookup_error"] = (
            f"Could not parse solutionid from response: {result.get('entity_id_url')!r}")
    return out


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

    # Force a real read even under dry-run: idempotent, and resolving the id +
    # reading ismanaged/parentsolutionid must work in the preview too (cf. create_solution).
    was_dry = backend.dry_run
    backend.dry_run = False
    try:
        info = solution_info(backend, unique_name)
    finally:
        backend.dry_run = was_dry
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
    un_lit = unique_name.replace("'", "''")  # escape the OData string literal
    params = {"$filter": f"uniquename eq '{un_lit}'"}
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


def _async_export_unavailable(exc: D365Error) -> bool:
    """True when the org lacks the ExportSolutionAsync action (older on-prem)."""
    msg = str(exc).lower()
    return "exportsolutionasync" in msg and (
        "not enabled" in msg
        or "not supported" in msg
        or "resource not found" in msg
    )


def _write_export_file(output_path: str | Path, encoded: str) -> tuple[Path, int]:
    data = base64.b64decode(encoded)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out, len(data)


def _export_solution_sync(
    backend: D365Backend,
    body: dict[str, Any],
    unique_name: str,
    output_path: str | Path,
    *,
    managed: bool,
    started: float,
) -> dict[str, Any]:
    """Synchronous fallback: ExportSolution returns the zip bytes inline."""
    import time as _time
    resp = as_dict(backend.post("ExportSolution", json_body=body))
    encoded = resp.get("ExportSolutionFile")
    if not encoded:
        raise D365Error("ExportSolution returned no ExportSolutionFile payload.")
    out, n = _write_export_file(output_path, encoded)
    return {
        "output": str(out),
        "bytes": n,
        "managed": managed,
        "solution": unique_name,
        "action": "ExportSolution",
        "duration_ms": int((_time.monotonic() - started) * 1000),
    }


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
    try:
        resp = as_dict(backend.post("ExportSolutionAsync", json_body=body))
    except D365Error as exc:
        # Older on-prem orgs don't enable the async export action; fall back to the
        # synchronous ExportSolution, which returns the zip bytes inline.
        if _async_export_unavailable(exc):
            return _export_solution_sync(
                backend, body, unique_name, output_path,
                managed=managed, started=started,
            )
        raise
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
    out, _bytes = _write_export_file(output_path, encoded)

    duration_ms = int((_time.monotonic() - started) * 1000)
    return {
        "output": str(out),
        "bytes": _bytes,
        "managed": managed,
        "solution": unique_name,
        "async_operation_id": async_op_id,
        "export_job_id": export_job_id,
        "duration_ms": duration_ms,
        "action": "ExportSolutionAsync",
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
