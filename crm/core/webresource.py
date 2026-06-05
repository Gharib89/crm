"""Web resource (webresourceset) CRUD.

create/update POST/PATCH the `webresourceset` entity; file bytes are
base64-encoded into the `content` column. update is a plain PATCH of only the
fields passed (no retrieve-merge-write). Resolve-by-name helpers force a real
read even under dry-run so a PATCH preview can target the live id.

The webresourcetype map is the D365 `webresource_webresourcetype` global option
set, verified against MS Learn's webresource entity reference
(learn.microsoft.com/power-apps/developer/data-platform/reference/entities/webresource):
2 = Style Sheet (CSS), 8 = Silverlight (XAP) — NOT the other way around.
"""

from __future__ import annotations

import base64
import os
import re
from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import maybe_publish

# Extension -> D365 webresourcetype (webresource_webresourcetype option set).
_EXT_TO_TYPE: dict[str, int] = {
    ".htm": 1, ".html": 1,   # Webpage (HTML)
    ".css": 2,               # Style Sheet (CSS)
    ".js": 3,                # Script (JScript)
    ".xml": 4,               # Data (XML)
    ".png": 5,               # PNG
    ".jpg": 6, ".jpeg": 6,   # JPG
    ".gif": 7,               # GIF
    ".xap": 8,               # Silverlight (XAP)
    ".xsl": 9, ".xslt": 9,   # Style Sheet (XSL)
    ".ico": 10,              # ICO
    ".svg": 11,              # Vector format (SVG)
    ".resx": 12,             # String (RESX)
}

_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def resolve_webresourcetype(file_name: str, override: int | None = None) -> int:
    """Resolve the D365 webresourcetype.

    An explicit override wins; otherwise map by the file extension
    (case-insensitive). Raise D365Error on an unknown extension when no
    override is given.
    """
    if override is not None:
        return override
    ext = os.path.splitext(file_name)[1].lower()
    if ext not in _EXT_TO_TYPE:
        raise D365Error(
            f"Cannot infer web resource type from extension {ext!r}; "
            f"pass an explicit type. Known: {sorted(set(_EXT_TO_TYPE))}"
        )
    return _EXT_TO_TYPE[ext]


def create_webresource(
    backend: D365Backend,
    *,
    name: str,
    content: bytes,
    webresourcetype: int,
    display_name: str | None = None,
    solution: str | None = None,
    publish: bool = False,
) -> dict[str, Any]:
    """Create a web resource (POST webresourceset).

    `content` is the raw file bytes, base64-encoded into the `content` column.
    Returns `{created, webresourceid, ...}`.
    """
    if not name:
        raise D365Error("name is required.")
    content_b64 = base64.b64encode(content).decode("ascii")
    body: dict[str, Any] = {
        "name": name,
        "displayname": display_name or name,
        "webresourcetype": webresourcetype,
        "content": content_b64,
    }
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post("webresourceset", json_body=body, extra_headers=headers))
    if result.get("_dry_run"):
        return result

    entity_id_url = result.get("_entity_id_url") or ""
    m = re.search(r"webresourceset\(([0-9a-fA-F-]{36})\)", entity_id_url)
    wid = m.group(1) if m else None
    out: dict[str, Any] = {
        "created": True,
        "name": name,
        "webresourceid": wid,
        "webresourcetype": webresourcetype,
        "solution": solution,
    }
    if not wid:
        out["webresource_lookup_error"] = (
            f"Could not parse webresourceid from response: {entity_id_url!r}"
        )
    maybe_publish(backend, out, publish)
    return out


def update_webresource(
    backend: D365Backend,
    name: str,
    *,
    content: bytes | None = None,
    display_name: str | None = None,
    solution: str | None = None,
    publish: bool = False,
) -> dict[str, Any]:
    """Update a web resource by name with a plain PATCH of only sent fields.

    Requires at least one of `content` / `display_name`. Resolves the id by
    name (force-reads even under dry-run), then PATCHes only the provided
    fields — not retrieve-merge-write.
    """
    if content is None and display_name is None:
        raise D365Error("nothing to update: pass new content and/or a display name.")

    wid = _resolve_id_by_name(backend, name)

    body: dict[str, Any] = {}
    if content is not None:
        body["content"] = base64.b64encode(content).decode("ascii")
    if display_name is not None:
        body["displayname"] = display_name

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.patch(
        f"webresourceset({wid})", json_body=body, extra_headers=headers))
    if result.get("_dry_run"):
        return result

    out: dict[str, Any] = {
        "updated": True,
        "name": name,
        "webresourceid": wid,
        "fields": sorted(body.keys()),
        "solution": solution,
    }
    maybe_publish(backend, out, publish)
    return out


def get_webresource(backend: D365Backend, name: str) -> dict[str, Any]:
    """Resolve a web resource by name and return its record."""
    esc = name.replace("'", "''")
    rows: list[dict[str, Any]] = as_dict(backend.get(
        "webresourceset",
        params={
            "$filter": f"name eq '{esc}'",
            "$select": "webresourceid,name,displayname,webresourcetype,ismanaged",
        },
    )).get("value", [])
    if not rows:
        raise D365Error(f"Web resource not found: {name}", code="WebResourceNotFound")
    return rows[0]


def list_webresources(
    backend: D365Backend,
    *,
    custom_only: bool = False,
    top: int | None = None,
) -> list[dict[str, Any]]:
    """List web resources, filtering server-side via $filter / $top.

    `custom_only` becomes a `$filter=ismanaged eq false`; `top` becomes a
    server-side `$top`. webresourceset is a normal entity collection (unlike
    the GlobalOptionSetDefinitions metadata endpoint), so both push to D365.
    """
    if top is not None and top < 1:
        raise D365Error("--top must be >= 1")
    params: dict[str, str] = {
        "$select": "name,displayname,webresourcetype,ismanaged",
        "$orderby": "name",
    }
    if custom_only:
        params["$filter"] = "ismanaged eq false"
    if top is not None:
        params["$top"] = str(top)
    items: list[dict[str, Any]] = as_dict(backend.get(
        "webresourceset", params=params)).get("value", [])
    return items


def _resolve_id_by_name(backend: D365Backend, name: str) -> str:
    """Resolve a web resource's id by exact name.

    Forces a real read even under dry-run (a PATCH preview needs the real id;
    mirrors appmodule.create_app).
    """
    esc = name.replace("'", "''")
    was_dry = backend.dry_run
    backend.dry_run = False
    try:
        rows: list[dict[str, Any]] = as_dict(backend.get(
            "webresourceset",
            params={"$filter": f"name eq '{esc}'", "$select": "webresourceid"},
        )).get("value", [])
    finally:
        backend.dry_run = was_dry
    if not rows:
        raise D365Error(f"Web resource not found: {name}", code="WebResourceNotFound")
    wid = rows[0].get("webresourceid")
    if not wid:
        raise D365Error(f"Web resource not found: {name}", code="WebResourceNotFound")
    return str(wid)


def resolve_webresource_id(backend: D365Backend, name_or_guid: str) -> str:
    """Resolve a web resource id from a GUID or a name.

    A GUID is returned unchanged (no HTTP); otherwise resolve by name. Used by
    `app create --icon-webresource`.
    """
    stripped = name_or_guid.strip()
    if _GUID_RE.match(stripped):
        return stripped
    return _resolve_id_by_name(backend, name_or_guid)
