"""Solution transfer pipeline: import / export + ImportJob result parsing.

The deep subsystem behind `import_solution()` / `export_solution()` /
`import_result()` — async actions with sync fallbacks, solution-zip sniffing, and
ImportJob `data` XML parsing. Every public name here is re-exported from
`crm.core.solution` for backward compatibility.
"""

from __future__ import annotations

import base64
import io
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, BinaryIO

from crm.utils.d365_backend import D365Backend, D365Error, as_dict


def _async_export_unavailable(exc: D365Error) -> bool:
    """True when the org lacks the ExportSolutionAsync action (older on-prem)."""
    msg = str(exc).lower()
    return "exportsolutionasync" in msg and (
        "not enabled" in msg
        or "not supported" in msg
        or "resource not found" in msg
    )


def _import_job_id_rejected(exc: D365Error) -> bool:  # pyright: ignore[reportUnusedFunction]
    """True when on-prem rejects ImportJobId as an invalid parameter."""
    msg = str(exc).lower()
    return "importjobid" in msg and (
        "not a valid parameter" in msg
        or "invalid parameter" in msg
    )


def _import_solution_sync(
    backend: D365Backend,
    body: dict[str, Any],
    import_job_id: str,
    *,
    managed: bool | None,
    started: float,
    timeout: int | None,
    formatted: bool,
) -> dict[str, Any]:
    """Synchronous fallback: ImportSolution runs the whole import in one request.

    On-prem v9.x rejects ImportJobId on ImportSolutionAsync and never creates an
    ImportJob row for the async action, leaving import-result unusable (#182).
    The sync ImportSolution action accepts the same client GUID, so the
    importjobs(<id>) read-back and per-component result parsing work unchanged,
    and dependency failures surface as a synchronous fault instead of a silent
    success. The whole import runs inside one HTTP request — the read timeout
    follows the command's --timeout (else profile.async_timeout), leaving the
    global request default untouched.
    """
    import time as _time
    read_timeout = timeout if timeout is not None else backend.profile.async_timeout
    try:
        backend.post("ImportSolution", json_body=body, timeout=read_timeout)
    except D365Error as exc:
        # Name the job id so `import-result <id>` can fetch any partial
        # per-component results the server recorded before the fault.
        raise D365Error(
            f"{exc} (import_job_id={import_job_id})",
            status=exc.status, code=exc.code, response_body=exc.response_body,
        ) from exc

    try:
        job_row = as_dict(backend.get(
            f"importjobs({import_job_id})",
            params={"$select": "progress,startedon,completedon,data"},
        ))
        detail = "the importjob data column was empty"
    except D365Error as exc:
        # The import itself already succeeded; a missing/unreadable importjob
        # row must not fail it after the fact.
        job_row = {}
        detail = f"the importjob row could not be read ({exc})"
    duration_ms = int((_time.monotonic() - started) * 1000)
    prog = job_row.get("progress")
    out: dict[str, Any] = {
        "import_job_id": import_job_id,
        "async_operation_id": None,
        "status": "succeeded",
        "progress": float(prog) if prog is not None else 100.0,
        "started_on": job_row.get("startedon"),
        "completed_on": job_row.get("completedon"),
        "duration_ms": duration_ms,
        "managed": managed,
        "action": "ImportSolution",
    }
    data_xml = job_row.get("data")
    if data_xml:
        _attach_import_results(out, data_xml)
    else:
        out["warnings"] = [
            "per-component import results are unavailable on this platform: "
            f"{detail}; the synchronous ImportSolution import itself succeeded."
        ]

    # Skip the formatted-report round-trip when the row was unreadable — it
    # would fail the same way and crash an import that already succeeded.
    if formatted and job_row:
        out["formatted_results"] = _formatted_import_results(backend, import_job_id)

    return out


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


# solution.xml is metadata — KB to a few MB even for large solutions (heavy
# assets live in separate zip members). Cap the advisory sniff's decompression
# so a zip-bomb solution.xml can't balloon memory / OOM-kill an import the
# server would otherwise accept from the small uploaded zip.
_MAX_SOLUTION_XML_BYTES = 64 * 1024 * 1024


def _sniff_solution_managed(zip_src: str | Path | BinaryIO) -> bool | None:
    """Best-effort read of solution.xml's <Managed> flag from a solution zip.

    Accepts a path or an already-open binary stream (so the caller can read the
    zip bytes once and sniff in-memory). Returns True (managed,
    <Managed>1</Managed>), False (unmanaged, 0), or None when the flag can't be
    determined — a bad/non-zip file, a zip with no solution.xml, a member too
    large to be a real manifest, an unparseable document, or an unexpected
    value. Never raises; the sniff is advisory metadata, not a gate on import.
    """
    try:
        with zipfile.ZipFile(zip_src) as zf:
            # file_size is the declared uncompressed size from the central
            # directory — read it without decompressing, and bail before read.
            if zf.getinfo("solution.xml").file_size > _MAX_SOLUTION_XML_BYTES:
                return None
            raw = zf.read("solution.xml")
        el = ET.fromstring(raw).find(".//Managed")
    except Exception:
        # Advisory sniff only — must never block an import the server would
        # accept. Beyond bad-zip/missing/parse, zf.read can raise
        # NotImplementedError (unsupported compression) or RuntimeError
        # (encrypted member); any failure degrades to "unknown" (None).
        return None
    if el is None or el.text is None:
        return None
    val = el.text.strip()
    if val == "1":
        return True
    if val == "0":
        return False
    return None


def import_solution(
    backend: D365Backend,
    zip_path: str | Path,
    *,
    publish_workflows: bool = True,
    overwrite_unmanaged_customizations: bool = True,
    holding_solution: bool = False,
    skip_dependency_check: bool = False,
    timeout: int | None = None,
    quiet: bool = False,
    formatted: bool = False,
) -> dict[str, Any]:
    """Call ImportSolutionAsync and block on the resulting ImportJob.

    `holding_solution=True` stages the import as a "holding" solution for a
    managed upgrade (ImportSolution `HoldingSolution`); apply it afterwards with
    `delete_and_promote`. This is what `crm solution stage-and-upgrade` uses.

    `skip_dependency_check=True` sets ImportSolution `SkipProductUpdateDependencies`
    so the import proceeds past a product-update dependency block (#376).

    When the org rejects ImportJobId on the async action (on-prem v9.x), falls
    back to the synchronous ImportSolution action carrying the same client GUID
    so the importjob row exists and `import-result` stays usable (#182).

    Returns a dict with import_job_id, async_operation_id, status='succeeded',
    progress percent, started_on / completed_on (from the importjobs row), and
    total wall-clock duration in ms. The final read also pulls the importjob
    `data` column and parses it into a per-component `result` + `components`
    envelope; any non-success component adds a `warnings` note so a partial
    failure under an overall-succeeded async op is no longer hidden (#70). With
    `formatted=True`, attaches the Excel-format report under `formatted_results`.

    Raises D365Error on file-not-found, async failure, or timeout.
    """
    import sys as _sys
    import time as _time

    p = Path(zip_path)
    if not p.is_file():
        raise D365Error(f"Solution file not found: {zip_path}")
    # Read the zip once: sniff the managed flag in-memory and reuse the same
    # bytes for the base64 upload (no second disk read).
    data = p.read_bytes()
    managed = _sniff_solution_managed(io.BytesIO(data))
    encoded = base64.b64encode(data).decode("ascii")
    import_job_id = _new_guid()
    body: dict[str, Any] = {
        "CustomizationFile": encoded,
        "PublishWorkflows": publish_workflows,
        "OverwriteUnmanagedCustomizations": overwrite_unmanaged_customizations,
        "HoldingSolution": holding_solution,
        "ImportJobId": import_job_id,
    }
    # Only emit SkipProductUpdateDependencies when opted in, so the default
    # import body stays byte-for-byte as it was (#376). Set on the shared body so
    # both the async path and the synchronous fallback inherit it.
    if skip_dependency_check:
        body["SkipProductUpdateDependencies"] = True

    started = _time.monotonic()
    try:
        resp = as_dict(backend.post("ImportSolutionAsync", json_body=body))
    except D365Error as exc:
        if not _import_job_id_rejected(exc):
            raise
        # On-prem D365 CE rejects ImportJobId on the async action and never
        # creates an ImportJob row for it — fall back to the synchronous
        # ImportSolution, which accepts the same client GUID (#182).
        return _import_solution_sync(
            backend, body, import_job_id,
            managed=managed, started=started,
            timeout=timeout, formatted=formatted,
        )

    if "_dry_run" in resp:
        return {**resp, "action": "ImportSolutionAsync", "import_job_id": import_job_id,
                "managed": managed}

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

    # Final importjobs read for the canonical progress + timestamps + the per-
    # component result envelope (data column).
    job_row = as_dict(backend.get(
        f"importjobs({import_job_id})",
        params={"$select": "progress,startedon,completedon,data"},
    ))
    duration_ms = int((_time.monotonic() - started) * 1000)
    prog = job_row.get("progress")
    out: dict[str, Any] = {
        "import_job_id": import_job_id,
        "async_operation_id": async_op_id,
        "status": "succeeded",
        "progress": float(prog) if prog is not None else 100.0,
        "started_on": job_row.get("startedon"),
        "completed_on": job_row.get("completedon"),
        "duration_ms": duration_ms,
        "managed": managed,
        "action": "ImportSolutionAsync",
    }

    # The import already succeeded (statuscode 30); parsing the post-hoc report
    # is best-effort (missing/unparseable data → warning, never an error).
    _attach_import_results(out, job_row.get("data"))

    if formatted:
        out["formatted_results"] = _formatted_import_results(backend, import_job_id)

    return out


# ── ImportJob result parsing (#70) ──────────────────────────────────────────
#
# The importjob `data` column holds an `<importexportxml>` document (structure
# verified against the op-9-1 "Work with solutions" sample). The solution-level
# outcome lives at //solutionManifest/result/@result; every imported component
# (optionSet / entity / webResource / rootComponent / dependency / …) carries
# its own <result> child with result/errorcode/errortext. We parse both so a
# partial failure under an overall-succeeded async op can no longer hide.


def _component_name(el: ET.Element) -> str:
    """Best human label for a component element: LocalizedName, name, id, or
    UniqueName — falling back to the element tag so the name is never null (some
    components, e.g. <rootComponent>/<dependency>, carry no label of their own)."""
    for attr in ("LocalizedName", "name", "id"):
        v = el.get(attr)
        if v:
            return v
    child = el.find("UniqueName")
    if child is not None and child.text:
        return child.text.strip()
    return el.tag


def parse_import_job_data(data_xml: str) -> dict[str, Any]:
    """Parse an ImportJob `data` XML document into a structured result envelope.

    Returns `{result, solution, components}` where `result` is the solution-level
    outcome (success|warning|failure) read from //solutionManifest/result/@result,
    `solution` the manifest UniqueName (or None), and `components` a list of
    `{name, type, result[, errorcode][, errortext]}` — one per component element
    that carries a `<result>` child. `errorcode`/`errortext` are included only
    when meaningful (non-zero / non-empty). Raises D365Error on unparseable XML.
    """
    if not data_xml or not data_xml.strip():
        raise D365Error("ImportJob data is empty; nothing to parse.")
    try:
        root = ET.fromstring(data_xml)
    except ET.ParseError as exc:
        raise D365Error(f"Could not parse ImportJob data XML: {exc}") from exc

    manifest = root.find(".//solutionManifest")
    overall = "unknown"
    solution_name: str | None = None
    if manifest is not None:
        res = manifest.find("result")
        if res is not None:
            overall = res.get("result", "unknown")
        uname = manifest.find("UniqueName")
        if uname is not None and uname.text:
            solution_name = uname.text.strip()

    components: list[dict[str, Any]] = []
    for el in root.iter():
        if el.tag == "solutionManifest":
            continue  # its <result> is the solution-level overall, not a component
        res = el.find("result")
        if res is None:
            continue
        comp: dict[str, Any] = {
            "name": _component_name(el),
            "type": el.tag,
            "result": res.get("result", "unknown"),
        }
        errorcode = res.get("errorcode")
        if errorcode and errorcode != "0":
            comp["errorcode"] = errorcode
        errortext = res.get("errortext")
        if errortext:
            comp["errortext"] = errortext
        components.append(comp)

    return {"result": overall, "solution": solution_name, "components": components}


def _result_warnings(result: str, components: list[dict[str, Any]]) -> list[str]:
    """Advisory notes for a parsed import result: the solution-level outcome if it
    is not `success`, plus one note per non-success component. Empty when clean —
    so a real partial failure surfaces even when the async op reported success."""
    warnings: list[str] = []
    if result != "success":
        warnings.append(f"solution-level import result is {result!r}.")
    for c in components:
        r = c.get("result")
        if not r or r == "success":
            continue
        label = c.get("name") or c.get("type") or "component"
        msg = f"{c.get('type', 'component')} {label!r} import result is {r!r}"
        detail = c.get("errortext")
        if detail:
            msg += f": {detail}"
        warnings.append(msg + ".")
    return warnings


def _attach_import_results(out: dict[str, Any], data_xml: str | None) -> None:
    """Parse the import `data` column into `out['result']`/`['components']`, adding
    a `warnings` note for any non-success component. Best-effort: a missing or
    unparseable data column degrades to a `warnings` note, never an error — so the
    post-hoc report can't fail an import the server already completed, and an
    absent result/components is never read as "checked and clean"."""
    if not data_xml:
        out["warnings"] = [
            "import job data column was empty; per-component results not verified."
        ]
        return
    try:
        env = parse_import_job_data(data_xml)
    except D365Error as exc:
        out["warnings"] = [f"could not parse import result data: {exc}"]
        return
    out["result"] = env["result"]
    out["components"] = env["components"]
    warnings = _result_warnings(env["result"], env["components"])
    if warnings:
        out["warnings"] = warnings


def _formatted_import_results(backend: D365Backend, import_job_id: str) -> str | None:
    """Fetch the Excel-format RetrieveFormattedImportJobResults report (verbatim)."""
    fr = as_dict(backend.get(
        f"RetrieveFormattedImportJobResults(ImportJobId={import_job_id})"
    ))
    return fr.get("FormattedResults")


def import_result(
    backend: D365Backend,
    import_job_id: str,
    *,
    formatted: bool = False,
) -> dict[str, Any]:
    """Re-fetch an ImportJob's `data` and parse it into the per-component envelope.

    Returns `{import_job_id, solution, progress, started_on, completed_on}` plus
    `result`/`components` when the data column could be parsed; otherwise a
    `warnings` note explains why. Parsing is best-effort — a missing or unparseable
    data column degrades to a warning, never an error. With `formatted=True`, also
    attaches the Excel-format report verbatim under `formatted_results`.
    """
    job = as_dict(backend.get(
        f"importjobs({import_job_id})",
        params={"$select": "data,solutionname,progress,startedon,completedon"},
    ))
    out: dict[str, Any] = {
        "import_job_id": import_job_id,
        "solution": job.get("solutionname"),
        "progress": job.get("progress"),
        "started_on": job.get("startedon"),
        "completed_on": job.get("completedon"),
    }
    _attach_import_results(out, job.get("data"))
    if formatted:
        out["formatted_results"] = _formatted_import_results(backend, import_job_id)
    return out


def _new_guid() -> str:
    import uuid
    return str(uuid.uuid4())
