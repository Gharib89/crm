"""Plug-in assembly (pluginassemblies) registration.

register_assembly POSTs the `pluginassemblies` entity; the assembly file's
bytes are base64-encoded into the `content` column. update is a plain PATCH of
only the `content` field (no retrieve-merge-write, but it does carry
`MSCRM.SolutionUniqueName` when a solution is given), resolving the assembly id
by name and forcing a real read even under dry-run so a PATCH preview targets
the live id.

Identity (name/version/culture/publickeytoken) is derived in pure Python —
filename stem plus documented defaults, with per-call overrides — NOT by .NET
reflection on the assembly. The four columns are SystemRequired on the API, so
they are always included in the create body.

Plug-in **type** rows are NOT posted here: the platform auto-creates plugintype
rows via reflection on the uploaded Content. This module only uploads the
assembly.

Column values verified against MS Learn's pluginassembly entity reference
(learn.microsoft.com/power-apps/developer/data-platform/reference/entities/pluginassembly):
isolationmode 1=None / 2=Sandbox / 3=External; sourcetype 0=Database (default).
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict

# Isolation mode -> pluginassembly isolationmode option set (verified MS Learn).
_ISOLATION_MODE: dict[str, int] = {
    "none": 1,      # None
    "sandbox": 2,   # Sandbox
}

# Documented defaults for an unsigned, single-file assembly registered without
# .NET reflection. publickeytoken "null" is the standard unsigned placeholder.
_DEFAULT_VERSION = "1.0.0.0"
_DEFAULT_CULTURE = "neutral"
_DEFAULT_PUBLIC_KEY_TOKEN = "null"

# sourcetype 0 = Database (default); the only mode v1 supports.
_SOURCE_TYPE_DATABASE = 0

# Match a bare GUID (optionally brace/paren wrapped) so unregister-* can accept
# either a name or an id without an extra resolution GET when given an id.
_GUID_RE = re.compile(
    r"^[{(]?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}[)}]?$"
)


def _looks_like_guid(value: str) -> bool:
    """True if `value` is a bare GUID (so it can be used as an id directly)."""
    return bool(_GUID_RE.match(value.strip()))


# Step stage -> sdkmessageprocessingstep.stage option set (verified MS Learn):
# prevalidation=10, preoperation=20, postoperation=40.
_STAGE: dict[str, int] = {
    "prevalidation": 10,
    "preoperation": 20,
    "postoperation": 40,
}
# Step mode -> sdkmessageprocessingstep.mode option set (sync=0, async=1).
_MODE: dict[str, int] = {
    "sync": 0,
    "async": 1,
}


def register_assembly(
    backend: D365Backend,
    *,
    path: str,
    name: str | None = None,
    version: str | None = None,
    culture: str | None = None,
    public_key_token: str | None = None,
    isolation_mode: str = "sandbox",
    description: str | None = None,
    solution: str | None = None,
    update: bool = False,
) -> dict[str, Any]:
    """Register (or update) a plug-in assembly from a file on disk.

    Reads the bytes at `path` and base64-encodes them into the `content` column.
    Identity is derived from the filename stem and documented defaults, with
    per-call overrides (no .NET reflection).

    `update=True` PATCHes only the `content` of the existing assembly (resolved
    by `name`, defaulting to the filename stem); returns `{updated, ...}`.
    Otherwise POSTs a new pluginassemblies row; returns `{created, ...}`.

    Raises D365Error on a missing file or an unknown isolation_mode.
    """
    if isolation_mode not in _ISOLATION_MODE:
        raise D365Error(
            f"Unknown isolation mode {isolation_mode!r}; "
            f"choose from {sorted(_ISOLATION_MODE)}."
        )
    src = Path(path)
    if not src.is_file():
        raise D365Error(f"Assembly file not found: {path}")
    raw = src.read_bytes()
    content_b64 = base64.b64encode(raw).decode("ascii")

    resolved_name = name or src.stem

    if update:
        return _update_assembly_content(
            backend, name=resolved_name, content_b64=content_b64,
            solution=solution)

    iso_int = _ISOLATION_MODE[isolation_mode]
    body: dict[str, Any] = {
        "name": resolved_name,
        "version": version or _DEFAULT_VERSION,
        "culture": culture or _DEFAULT_CULTURE,
        "publickeytoken": public_key_token or _DEFAULT_PUBLIC_KEY_TOKEN,
        "isolationmode": iso_int,
        "sourcetype": _SOURCE_TYPE_DATABASE,
        "content": content_b64,
    }
    if description is not None:
        body["description"] = description

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(
        "pluginassemblies", json_body=body, extra_headers=headers))
    if result.get("_dry_run"):
        return result

    entity_id_url = result.get("_entity_id_url") or ""
    m = re.search(r"pluginassemblies\(([0-9a-fA-F-]{36})\)", entity_id_url)
    pid = m.group(1) if m else None
    out: dict[str, Any] = {
        "created": True,
        "name": resolved_name,
        "pluginassemblyid": pid,
        "isolationmode": iso_int,
        "version": body["version"],
        "solution": solution,
    }
    if not pid:
        out["pluginassembly_lookup_error"] = (
            f"Could not parse pluginassemblyid from response: {entity_id_url!r}"
        )
    return out


def list_types(
    backend: D365Backend, *, assembly: str | None = None,
) -> dict[str, Any]:
    """List platform-generated plug-in types (the `plugintypes` entity set).

    Plug-in **type** rows are auto-created by the platform via reflection on an
    uploaded assembly's Content (register_assembly never POSTs them); this is a
    plain read of those rows.

    When `assembly` (an assembly NAME) is given, it is resolved to a
    `pluginassemblyid` via `_resolve_id_by_name` (a not-found name raises
    D365Error) and the listing is filtered server-side on
    `_pluginassemblyid_value`.

    Column names verified against MS Learn's plugintype entity reference:
    `typename` (fully qualified class name), `friendlyname` (display name),
    `assemblyname` (owning assembly name).
    """
    params: dict[str, str] = {
        "$select": "plugintypeid,typename,friendlyname,assemblyname",
    }
    if assembly is not None:
        pid = _resolve_id_by_name(backend, assembly)
        params["$filter"] = f"_pluginassemblyid_value eq {pid}"
    rows: list[dict[str, Any]] = as_dict(backend.get(
        "plugintypes", params=params)).get("value", [])
    return {"value": rows}


def register_step(
    backend: D365Backend,
    *,
    message: str,
    plugin_type: str,
    entity: str | None = None,
    stage: str = "postoperation",
    mode: str = "sync",
    rank: int = 1,
    filtering_attributes: str | None = None,
    name: str | None = None,
    assembly: str | None = None,
) -> dict[str, Any]:
    """Register an `sdkmessageprocessingstep` (a plug-in step).

    Resolves the SDK message (by `message` name) and the plug-in type (by
    `plugin_type` typename, optionally scoped to `assembly` by name) to their
    ids, then POSTs a step bound to them. When `entity` is given, the message's
    `sdkmessagefilter` for that `primaryobjecttypecode` is resolved and bound so
    the step fires only for that entity; with no `entity` the step is
    message-level (fires for all entities). A missing message, plug-in type, or
    (entity-given) filter raises D365Error.

    Option-set values verified against MS Learn's sdkmessageprocessingstep
    entity reference: stage prevalidation=10 / preoperation=20 /
    postoperation=40; mode sync=0 / async=1. Raises D365Error on an unknown
    stage or mode.

    Async (`mode="async"`) requires the postoperation stage (enforced here):
    other stages raise D365Error. The derived default `name` plus a very long
    fully-qualified typename can exceed the platform's 256-char `name` limit —
    pass `name` explicitly if so (the server enforces the limit, not this code).

    Dry-run returns the backend preview as-is (no real POST).
    """
    if stage not in _STAGE:
        raise D365Error(
            f"Unknown stage {stage!r}; choose from {sorted(_STAGE)}.")
    if mode not in _MODE:
        raise D365Error(
            f"Unknown mode {mode!r}; choose from {sorted(_MODE)}.")
    stage_int = _STAGE[stage]
    mode_int = _MODE[mode]
    # Async plug-ins can only run in the postoperation stage (MS Learn:
    # register-plug-in#step-registration). Guard before any HTTP call.
    if mode_int == 1 and stage_int != 40:
        raise D365Error(
            f"Asynchronous mode requires the postoperation stage (got {stage!r}).",
            code="AsyncRequiresPostOperation")

    message_id = _resolve_sdkmessage_id(backend, message)
    plugintype_id = _resolve_plugintype_id(backend, plugin_type, assembly)

    resolved_name = name or (
        f"{plugin_type}: {message} of {entity or 'any entity'}")
    body: dict[str, Any] = {
        "name": resolved_name,
        "stage": stage_int,
        "mode": mode_int,
        "rank": rank,
        "SdkMessageId@odata.bind": f"/sdkmessages({message_id})",
        "PluginTypeId@odata.bind": f"/plugintypes({plugintype_id})",
    }
    if entity is not None:
        filter_id = _resolve_sdkmessagefilter_id(backend, entity, message_id)
        body["SdkMessageFilterId@odata.bind"] = (
            f"/sdkmessagefilters({filter_id})")
    if filtering_attributes is not None:
        body["filteringattributes"] = filtering_attributes

    result = as_dict(backend.post("sdkmessageprocessingsteps", json_body=body))
    if result.get("_dry_run"):
        return result

    entity_id_url = result.get("_entity_id_url") or ""
    m = re.search(
        r"sdkmessageprocessingsteps\(([0-9a-fA-F-]{36})\)", entity_id_url)
    sid = m.group(1) if m else None
    out: dict[str, Any] = {
        "created": True,
        "sdkmessageprocessingstepid": sid,
        "name": resolved_name,
        "stage": stage_int,
        "mode": mode_int,
        "message": message,
        "entity": entity,
        "plugintype": plugin_type,
    }
    if not sid:
        out["sdkmessageprocessingstep_lookup_error"] = (
            "Could not parse sdkmessageprocessingstepid from response: "
            f"{entity_id_url!r}"
        )
    return out


def unregister_step(backend: D365Backend, step: str) -> dict[str, Any]:
    """Unregister (delete) an `sdkmessageprocessingstep` by name or id.

    `step` is used directly when it looks like a GUID; otherwise it is resolved
    by exact name (the lookup force-reads even under dry-run so a dry-run delete
    still targets the live id). A name that matches no step raises D365Error.

    Deleting the step cascades its registered entity images (sdkmessageprocessingstepimage)
    automatically — no separate image delete is needed (MS Learn). Dry-run
    passes through the backend's delete preview (no real DELETE).
    """
    step_id = step.strip() if _looks_like_guid(step) else _resolve_step_id(
        backend, step)
    result = as_dict(backend.delete(f"sdkmessageprocessingsteps({step_id})"))
    if result.get("_dry_run"):
        return result
    return {"deleted": True, "sdkmessageprocessingstepid": step_id}


def unregister_assembly(
    backend: D365Backend, assembly: str,
) -> dict[str, Any]:
    """Unregister (delete) a plug-in assembly and its dependent steps.

    `assembly` is used directly when it looks like a GUID; otherwise it is
    resolved by exact name. Because the platform refuses to delete an assembly
    while any sdkmessageprocessingstep still depends on it, this first collects
    the dependent steps (assembly -> plugintypes -> steps) and DELETEs each one,
    THEN DELETEs the assembly. Deleting a step cascades its entity images, and
    deleting the assembly cascades its plugintypes — neither is deleted here
    explicitly (MS Learn).

    Under dry-run no real DELETE is issued: the resolution GETs still force-read
    (mirrors the rest of this module) and a `_dry_run` preview naming the
    assembly, the dependent step count, and the step ids is returned.

    Raises D365Error when an assembly name resolves to nothing.
    """
    aid = assembly.strip() if _looks_like_guid(assembly) else (
        _resolve_id_by_name(backend, assembly))
    step_ids = _dependent_step_ids(backend, aid)

    if backend.dry_run:
        return {
            "_dry_run": True,
            "deleted": True,
            "pluginassemblyid": aid,
            "steps_deleted": len(step_ids),
            "deleted_step_ids": step_ids,
        }

    # Dependent steps first (images cascade with each step), then the assembly
    # (plugintypes cascade with it).
    for sid in step_ids:
        backend.delete(f"sdkmessageprocessingsteps({sid})")
    backend.delete(f"pluginassemblies({aid})")
    return {
        "deleted": True,
        "pluginassemblyid": aid,
        "steps_deleted": len(step_ids),
        "deleted_step_ids": step_ids,
    }


def _resolve_step_id(backend: D365Backend, name: str) -> str:
    """Resolve an sdkmessageprocessingstep id by exact name (force-reads)."""
    esc = name.replace("'", "''")
    rows = _force_read_rows(
        backend, "sdkmessageprocessingsteps",
        {"$filter": f"name eq '{esc}'",
         "$select": "sdkmessageprocessingstepid"})
    if not rows or not rows[0].get("sdkmessageprocessingstepid"):
        raise D365Error(
            f"Plug-in step not found: {name}", code="SdkStepNotFound")
    return str(rows[0]["sdkmessageprocessingstepid"])


def _dependent_step_ids(backend: D365Backend, assembly_id: str) -> list[str]:
    """Collect ids of every step that depends on `assembly_id`.

    Walks the dependency chain assembly -> plugintypes -> steps. Both GETs
    force-read so the set is correct even under dry-run.
    """
    type_rows = _force_read_rows(
        backend, "plugintypes",
        {"$filter": f"_pluginassemblyid_value eq {assembly_id}",
         "$select": "plugintypeid"})
    step_ids: list[str] = []
    for tr in type_rows:
        ptid = tr.get("plugintypeid")
        if not ptid:
            continue
        step_rows = _force_read_rows(
            backend, "sdkmessageprocessingsteps",
            {"$filter": f"_plugintypeid_value eq {ptid}",
             "$select": "sdkmessageprocessingstepid"})
        step_ids.extend(
            str(sr["sdkmessageprocessingstepid"]) for sr in step_rows
            if sr.get("sdkmessageprocessingstepid"))
    return step_ids


def _force_read_rows(
    backend: D365Backend, entity_set: str, params: dict[str, str],
) -> list[dict[str, Any]]:
    """GET `entity_set` rows, forcing a real read even under dry-run.

    A step is POSTed only after its bound ids are resolved, so the resolution
    GETs must run for real even in dry-run (mirrors `_resolve_id_by_name`).
    """
    was_dry = backend.dry_run
    backend.dry_run = False
    try:
        return as_dict(backend.get(entity_set, params=params)).get("value", [])
    finally:
        backend.dry_run = was_dry


def _resolve_sdkmessage_id(backend: D365Backend, message: str) -> str:
    """Resolve an SDK message id by exact name (e.g. Create/Update/Delete)."""
    esc = message.replace("'", "''")
    rows = _force_read_rows(
        backend, "sdkmessages",
        {"$filter": f"name eq '{esc}'", "$select": "sdkmessageid,name"})
    if not rows or not rows[0].get("sdkmessageid"):
        raise D365Error(
            f"SDK message not found: {message}", code="SdkMessageNotFound")
    return str(rows[0]["sdkmessageid"])


def _resolve_plugintype_id(
    backend: D365Backend, typename: str, assembly: str | None,
) -> str:
    """Resolve a plug-in type id by typename, optionally scoped to an assembly.

    When `assembly` (an assembly NAME) is given it is resolved to a
    `pluginassemblyid` and ANDed into the filter to disambiguate. Without it the
    first matching row is used.
    """
    esc = typename.replace("'", "''")
    filt = f"typename eq '{esc}'"
    if assembly is not None:
        pid = _resolve_id_by_name(backend, assembly)
        filt += f" and _pluginassemblyid_value eq {pid}"
    rows = _force_read_rows(
        backend, "plugintypes",
        {"$filter": filt, "$select": "plugintypeid,typename"})
    if not rows or not rows[0].get("plugintypeid"):
        raise D365Error(
            f"Plug-in type not found: {typename}", code="PluginTypeNotFound")
    return str(rows[0]["plugintypeid"])


def _resolve_sdkmessagefilter_id(
    backend: D365Backend, entity: str, message_id: str,
) -> str:
    """Resolve the sdkmessagefilter id for an entity + message pair.

    Raises D365Error when the message does not support the entity (no filter
    row), since binding one is required for an entity-scoped step.
    """
    esc = entity.replace("'", "''")
    filt = (f"primaryobjecttypecode eq '{esc}' "
            f"and _sdkmessageid_value eq {message_id}")
    rows = _force_read_rows(
        backend, "sdkmessagefilters",
        {"$filter": filt, "$select": "sdkmessagefilterid"})
    if not rows or not rows[0].get("sdkmessagefilterid"):
        raise D365Error(
            f"No SDK message filter for entity {entity!r} on this message "
            "(that message does not support that entity).",
            code="SdkMessageFilterNotFound")
    return str(rows[0]["sdkmessagefilterid"])


def _update_assembly_content(
    backend: D365Backend, *, name: str, content_b64: str,
    solution: str | None = None,
) -> dict[str, Any]:
    """PATCH only the `content` of an existing assembly resolved by name.

    When `solution` is set, the PATCH carries `MSCRM.SolutionUniqueName` so the
    update lands in that solution (mirrors webresource.update_webresource).
    """
    pid = _resolve_id_by_name(backend, name)
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.patch(
        f"pluginassemblies({pid})", json_body={"content": content_b64},
        extra_headers=headers))
    if result.get("_dry_run"):
        return result
    return {
        "updated": True,
        "name": name,
        "pluginassemblyid": pid,
        "fields": ["content"],
        "solution": solution,
    }


def _resolve_id_by_name(backend: D365Backend, name: str) -> str:
    """Resolve a plug-in assembly's id by exact name.

    Forces a real read even under dry-run (a PATCH preview needs the real id;
    mirrors webresource._resolve_id_by_name).
    """
    esc = name.replace("'", "''")
    rows = _force_read_rows(
        backend, "pluginassemblies",
        {"$filter": f"name eq '{esc}'", "$select": "pluginassemblyid"})
    if not rows:
        raise D365Error(
            f"Plug-in assembly not found: {name}", code="PluginAssemblyNotFound")
    pid = rows[0].get("pluginassemblyid")
    if not pid:
        raise D365Error(
            f"Plug-in assembly not found: {name}", code="PluginAssemblyNotFound")
    return str(pid)
