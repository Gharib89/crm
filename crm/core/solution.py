"""Solution lifecycle: create-publisher / create / list / info / export / import."""

from __future__ import annotations

import base64
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, BinaryIO

from crm.core import entity
from crm.core import metadata_cache
from crm.utils.d365_backend import D365Backend, D365Error, as_dict


# ── Create publisher / solution ─────────────────────────────────────────────
#
# Both mirror appmodule.create_app: a forced-real existence GET (accurate even
# under --dry-run), --if-exists error|skip semantics, then a 204-create via
# entity.create(return_record=False) whose OData-EntityId GUID is synthesised
# into the returned record. on-prem 9.1 publisher/solution contract is verified
# against the op-9-1 docs (customizationprefix 2-8 alnum not 'mscrm';
# customizationoptionvalueprefix 10000-99999; solution publisherid@odata.bind).


def validate_customization_prefix(prefix: str) -> None:
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
    validate_customization_prefix(prefix)
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


# ── Solution components (#71) ────────────────────────────────────────────────
#
# Flat friendly-name → integer map for the `componenttype` global optionset
# (values verified against the Dataverse SolutionComponent reference). Keys are
# canonical lower-case, separator-free; `resolve_component_type` normalises input
# so 'WebResource' / 'web resource' / 'web-resource' all map to 61. Note the
# canonical split: 'relationship' is 3 (base relationship), 'entityrelationship'
# is 10 — not interchangeable. Pass a raw int for any type not listed here.

SOLUTION_COMPONENT_TYPES: dict[str, int] = {
    "entity": 1,
    "attribute": 2,
    "relationship": 3,
    "optionset": 9,
    "entityrelationship": 10,
    "entitykey": 14,
    "role": 20,
    "form": 24,
    "savedquery": 26,
    "workflow": 29,
    "emailtemplate": 36,
    "duplicaterule": 44,
    "savedqueryvisualization": 59,
    "systemform": 60,
    "webresource": 61,
    "sitemap": 62,
    "connectionrole": 63,
    "fieldsecurityprofile": 70,
    "plugintype": 90,
    "pluginassembly": 91,
    "sdkmessageprocessingstep": 92,
    "serviceendpoint": 95,
}


def resolve_component_type(value: str | int) -> int:
    """Resolve a component-type `value` (int, numeric string, or friendly name)
    to its `componenttype` integer. Names are matched case- and separator-
    insensitively against SOLUTION_COMPONENT_TYPES. Raises D365Error on an
    unknown name."""
    if isinstance(value, int):
        return value
    text = value.strip()
    if text.lstrip("-").isdigit():
        return int(text)
    key = re.sub(r"[\s_-]+", "", text).lower()
    try:
        return SOLUTION_COMPONENT_TYPES[key]
    except KeyError:
        known = ", ".join(sorted(SOLUTION_COMPONENT_TYPES))
        raise D365Error(
            f"unknown component type {value!r}; pass an integer or one of: {known}."
        ) from None


def _require_unmanaged_solution(
    backend: D365Backend, solution: str, *, verb: str
) -> None:
    """Forced-real solution_info pre-flight (works under dry-run too); raise if the
    target is managed. `verb` is the action phrase, e.g. 'added to'."""
    was_dry = backend.dry_run
    backend.dry_run = False
    try:
        info = solution_info(backend, solution)
    finally:
        backend.dry_run = was_dry
    if info.get("ismanaged"):
        raise D365Error(
            f"Solution {solution!r} is managed; components can only be {verb} an "
            "unmanaged solution.",
            code="CannotModifyManagedSolution",
        )


def add_solution_component(
    backend: D365Backend,
    *,
    solution: str,
    component_id: str,
    component_type: int,
    add_required_components: bool = True,
    do_not_include_subcomponents: bool = False,
) -> dict[str, Any]:
    """Add an existing component to an unmanaged solution via AddSolutionComponent.

    Pre-flights solution_info (forced-real even under dry-run) and refuses a
    managed target — AddSolutionComponent is unmanaged-only. Returns
    `{added, solution, component_id, component_type}` on a real run.
    """
    _require_unmanaged_solution(backend, solution, verb="added to")

    body: dict[str, Any] = {
        "ComponentId": component_id,
        "ComponentType": component_type,
        "SolutionUniqueName": solution,
        "AddRequiredComponents": add_required_components,
        "DoNotIncludeSubcomponents": do_not_include_subcomponents,
    }
    result = as_dict(backend.post("AddSolutionComponent", json_body=body))
    if result.get("_dry_run"):
        result["solution"] = solution
        result["component_id"] = component_id
        result["component_type"] = component_type
        return result
    return {"added": True, "solution": solution, "component_id": component_id,
            "component_type": component_type}


def remove_solution_component(
    backend: D365Backend,
    *,
    solution: str,
    component_id: str,
    component_type: int,
) -> dict[str, Any]:
    """Remove a component from an unmanaged solution via RemoveSolutionComponent.

    Pre-flights solution_info (forced-real even under dry-run) and refuses a
    managed target — a managed solution cannot be edited. Returns
    `{removed, solution, component_id, component_type}` on a real run.
    """
    _require_unmanaged_solution(backend, solution, verb="removed from")

    # Unlike AddSolutionComponent, the RemoveSolutionComponent Web API action
    # has no ComponentId parameter — it takes a SolutionComponent entity
    # reference whose solutioncomponentid carries the component objectid
    # (live-verified contract, #181).
    body: dict[str, Any] = {
        "SolutionComponent": {
            "solutioncomponentid": component_id,
            "@odata.type": "Microsoft.Dynamics.CRM.solutioncomponent",
        },
        "ComponentType": component_type,
        "SolutionUniqueName": solution,
    }
    result = as_dict(backend.post("RemoveSolutionComponent", json_body=body))
    if result.get("_dry_run"):
        result["solution"] = solution
        result["component_id"] = component_id
        result["component_type"] = component_type
        return result
    return {"removed": True, "solution": solution, "component_id": component_id,
            "component_type": component_type}


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


# ── Component normalisation / diff ──────────────────────────────────────────


def normalize_components(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a new, sorted list with exactly the three canonical keys.

    - ``componenttype``        → coerced to ``int``
    - ``objectid``             → lowercased ``str`` (stable GUID matching);
      a non-string ``objectid`` raises ``ValueError`` rather than being coerced,
      so a malformed snapshot (e.g. ``{"objectid": null}``) fails fast instead
      of silently becoming the literal string ``"none"``
    - ``rootcomponentbehavior`` → ``int`` or ``None`` (missing/None preserved)

    Input rows are not mutated.  The sort key is
    ``(componenttype, objectid, rootcomponentbehavior_or_minus1)``
    where ``None`` maps to ``-1`` for ordering only — the stored value stays
    ``None``.
    """
    out: list[dict[str, Any]] = []
    for row in items:
        objectid = row["objectid"]
        if not isinstance(objectid, str):
            raise ValueError(
                f"objectid must be a string, got {type(objectid).__name__}"
            )
        rcb_raw = row.get("rootcomponentbehavior")
        rcb: int | None = None if rcb_raw is None else int(rcb_raw)
        out.append({
            "componenttype": int(row["componenttype"]),
            "objectid": objectid.lower(),
            "rootcomponentbehavior": rcb,
        })
    out.sort(key=lambda c: (
        c["componenttype"],
        c["objectid"],
        c["rootcomponentbehavior"] if c["rootcomponentbehavior"] is not None else -1,
    ))
    return out


def diff_components(
    live: list[dict[str, Any]],
    expected: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare two component lists and return a diff summary.

    Each component is keyed on ``(componenttype, objectid, rootcomponentbehavior)``
    after normalisation, so a same-ID component with a different
    ``rootcomponentbehavior`` value counts as **both** missing and unexpected.

    Returns::

        {
            "matches": bool,
            "missing":    [...],   # in expected, not in live
            "unexpected": [...],   # in live, not in expected
        }
    """
    norm_live = normalize_components(live)
    norm_expected = normalize_components(expected)

    def _key(c: dict[str, Any]) -> tuple[int, str, int | None]:
        return (c["componenttype"], c["objectid"], c["rootcomponentbehavior"])

    live_keys = {_key(c): c for c in norm_live}
    expected_keys = {_key(c): c for c in norm_expected}

    missing    = [c for c in norm_expected if _key(c) not in live_keys]
    unexpected = [c for c in norm_live    if _key(c) not in expected_keys]
    return {
        "matches": len(missing) == 0 and len(unexpected) == 0,
        "missing": missing,
        "unexpected": unexpected,
    }


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
    timeout: int | None = None,
    quiet: bool = False,
    formatted: bool = False,
) -> dict[str, Any]:
    """Call ImportSolutionAsync and block on the resulting ImportJob.

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
        "ImportJobId": import_job_id,
    }

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


def publish_all(backend: D365Backend) -> dict[str, Any]:
    """Call PublishAllXml — publishes all unpublished customizations.

    Action returns 204 No Content on success, so we synthesize a confirmation dict.
    """
    result = as_dict(backend.post("PublishAllXml"))
    # Bust the cache on any successful non-dry-run publish, regardless of whether
    # the action returned a body (dry-run yields a truthy preview dict — its body
    # must NOT trigger invalidation, hence the guard before the early return).
    if not backend.dry_run:
        metadata_cache.invalidate(backend.profile)
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
    # Bust the cache on any successful non-dry-run publish, regardless of body
    # (see publish_all — the dry-run preview is truthy and must not invalidate).
    if not backend.dry_run:
        metadata_cache.invalidate(backend.profile)
    if result:
        return result
    return {"published": True, "action": "PublishXml"}


def service_document(backend: D365Backend) -> dict[str, Any]:
    """GET the root service document — lists all entity sets exposed by the server."""
    return as_dict(backend.get(""))


def _new_guid() -> str:
    import uuid
    return str(uuid.uuid4())
