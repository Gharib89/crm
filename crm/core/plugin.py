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
    was_dry = backend.dry_run
    backend.dry_run = False
    try:
        rows: list[dict[str, Any]] = as_dict(backend.get(
            "pluginassemblies",
            params={"$filter": f"name eq '{esc}'", "$select": "pluginassemblyid"},
        )).get("value", [])
    finally:
        backend.dry_run = was_dry
    if not rows:
        raise D365Error(
            f"Plug-in assembly not found: {name}", code="PluginAssemblyNotFound")
    pid = rows[0].get("pluginassemblyid")
    if not pid:
        raise D365Error(
            f"Plug-in assembly not found: {name}", code="PluginAssemblyNotFound")
    return str(pid)
