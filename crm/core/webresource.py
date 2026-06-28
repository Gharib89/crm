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
from pathlib import Path
from typing import Any

from crm.utils.d365_backend import (
    D365Backend,
    D365Error,
    as_dict,
    normalize_guid,
    odata_literal,
)
from crm.core import dependencies as dep_mod
from crm.core.metadata import maybe_publish

# Dataverse solution-component type for a web resource (the system `componenttype`
# option set). Used to query RetrieveDependenciesForDelete before a delete.
_WEBRESOURCE_COMPONENT_TYPE = 61

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
    result = as_dict(backend.post("webresourceset", json_body=body, solution=solution))
    if result.get("_dry_run"):
        return result

    wid = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "name": name,
        "webresourceid": wid,
        "webresourcetype": webresourcetype,
        "solution": solution,
    }
    if not wid:
        entity_id_url = result.get("_entity_id_url") or ""
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

    result = as_dict(backend.patch(
        f"webresourceset({wid})", json_body=body, solution=solution))
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


def _walk_files(directory: str) -> list[tuple[str, Path]]:
    """Return `(posix-relpath, path)` for every file under `directory`, sorted.

    `path` is the file as walked (rooted under `directory`, so absolute only when
    `directory` is). Sorted for a deterministic walk order; the relative path uses
    `/` separators so the derived web resource name is stable across platforms.
    Raises D365Error if `directory` is missing or not a directory — without that
    guard a bad path would yield an empty walk and look like a successful no-op.
    """
    base = Path(directory)
    if not base.is_dir():
        raise D365Error(f"push directory not found or not a directory: {directory}")
    out = [
        (p.relative_to(base).as_posix(), p)
        for p in base.rglob("*")
        if p.is_file()
    ]
    out.sort()
    return out


def push_webresources(
    backend: D365Backend,
    directory: str,
    *,
    prefix: str,
    solution: str | None = None,
    publish: bool = True,
) -> dict[str, Any]:
    """Walk `directory` and upsert each file as a web resource, publishing once.

    Each file maps to web resource name ``<prefix>_<relpath>`` where ``relpath``
    is the file's path relative to ``directory`` with ``/`` separators; the type
    is inferred from the extension. The file's bytes are base64-compared against
    the live ``content``: a missing resource is created, a changed one updated, a
    byte-identical one skipped (no PATCH). A per-file error is collected and the
    walk continues — one bad file never aborts the run (unlike ``apply_spec``).
    Once the whole walk completes, publishes once via ``PublishAllXml`` when any
    content changed; the successful writes publish even if other files failed.

    Returns ``{pushed, updated, skipped, published, failed, files}`` on a real
    run — ``pushed``/``updated``/``skipped`` counts, ``published`` a bool,
    ``failed`` a list of ``{file, name, error}``, ``files`` the full per-file
    detail. Under dry-run the live GETs still run (reads-execute rule) but every
    write is short-circuited, returning ``{_dry_run, would_create, would_update,
    skipped, published, failed, files}`` previewing the create/update sets.
    """
    from crm.core.solution import publish_all, validate_customization_prefix

    validate_customization_prefix(prefix)

    files: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    changed = 0  # creates + content updates (drives the single end-of-run publish)

    for relpath, abspath in _walk_files(directory):
        name = f"{prefix}_{relpath}"
        entry: dict[str, Any] = {"file": relpath, "name": name}
        try:
            wtype = resolve_webresourcetype(relpath)
            content = abspath.read_bytes()
            live = find_webresource(backend, name)
            if live is not None and live.get("content") == base64.b64encode(
                    content).decode("ascii"):
                entry["action"] = "skipped"
            elif backend.dry_run:
                entry["action"] = "would_create" if live is None else "would_update"
            elif live is None:
                res = create_webresource(
                    backend, name=name, content=content, webresourcetype=wtype,
                    solution=solution, publish=False)
                entry["action"] = "created"
                entry["webresourceid"] = res.get("webresourceid")
                changed += 1
            else:
                update_webresource(
                    backend, name, content=content, solution=solution, publish=False)
                entry["action"] = "updated"
                changed += 1
        except (D365Error, OSError) as exc:
            # A per-file error (server fault, unknown extension, or an unreadable
            # file) is collected and the walk continues — never aborts the run.
            entry["action"] = "failed"
            entry["error"] = str(exc)
            failed.append({"file": relpath, "name": name, "error": str(exc)})
        files.append(entry)

    published = bool(changed) and publish and not backend.dry_run
    if published:
        publish_all(backend)

    def _count(action: str) -> int:
        return sum(1 for f in files if f["action"] == action)

    if backend.dry_run:
        return {
            "_dry_run": True,
            "would_create": [f["name"] for f in files if f["action"] == "would_create"],
            "would_update": [f["name"] for f in files if f["action"] == "would_update"],
            "skipped": _count("skipped"),
            "published": False,
            "failed": failed,
            "files": files,
        }
    return {
        "pushed": _count("created"),
        "updated": _count("updated"),
        "skipped": _count("skipped"),
        "published": published,
        "failed": failed,
        "files": files,
    }


def get_webresource(backend: D365Backend, name: str) -> dict[str, Any]:
    """Resolve a web resource by name and return its record."""
    rows = backend.get_collection(
        "webresourceset",
        params={
            "$filter": f"name eq {odata_literal(name)}",
            "$select": "webresourceid,name,displayname,webresourcetype,ismanaged",
        },
    )
    if not rows:
        raise D365Error(f"Web resource not found: {name}", code="WebResourceNotFound")
    return rows[0]


def find_webresource(backend: D365Backend, name: str) -> dict[str, Any] | None:
    """Resolve a web resource by name for apply's drift check, or None if absent.

    Unlike :func:`get_webresource` this returns ``None`` (not a raise) when the
    name is unknown and the ``$select`` carries the base64 ``content`` so apply can
    diff the live body against the spec's file. A forced-real read (``get_collection``
    runs even under dry-run), so a dry-run still reports create-vs-update correctly.
    """
    rows = backend.get_collection(
        "webresourceset",
        params={
            "$filter": f"name eq {odata_literal(name)}",
            "$select": "webresourceid,name,displayname,webresourcetype,content",
        },
    )
    return rows[0] if rows else None


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
    return backend.get_collection("webresourceset", params=params)


def delete_webresource(
    backend: D365Backend,
    name_or_id: str,
    *,
    check_dependencies: bool = False,
) -> dict[str, Any]:
    """Delete a web resource by unique name or id.

    Resolves `name_or_id` via `resolve_webresource_id` (a GUID passes through
    untouched; a name is resolved by a live read, which runs even under dry-run),
    then DELETEs `webresourceset(<id>)`.

    A web resource referenced by a ribbon button (or other component) cannot be
    deleted — the server returns 0x8004f01f and that fault surfaces unchanged.
    When `check_dependencies` is set, an up-front RetrieveDependenciesForDelete
    probe folds `can_delete` + `blockers` into the result; it is informational
    only and does NOT block the delete.

    Dry-run returns `{_dry_run, would_delete, name, webresourceid}`; a real
    delete returns `{deleted, name, webresourceid}`.
    """
    wid = resolve_webresource_id(backend, name_or_id)

    deps = None
    if check_dependencies:
        deps = dep_mod.dependencies_by_id(
            backend, wid, _WEBRESOURCE_COMPONENT_TYPE,
            for_="delete", kind="webresource",
        )

    preview = backend.delete(f"webresourceset({wid})")
    if isinstance(preview, dict) and preview.get("_dry_run"):
        result: dict[str, Any] = {
            "_dry_run": True,
            "would_delete": True,
            "name": name_or_id,
            "webresourceid": wid,
        }
    else:
        result = {
            "deleted": True,
            "name": name_or_id,
            "webresourceid": wid,
        }
    if deps is not None:
        result["can_delete"] = deps["can_delete"]
        result["blockers"] = deps["blockers"]
    return result


def _resolve_id_by_name(backend: D365Backend, name: str) -> str:
    """Resolve a web resource's id by exact name.

    A PATCH preview needs the real id, so resolve it via a live read.
    """
    wid = backend.resolve_id_by_name(
        "webresourceset", filter_field="name", id_field="webresourceid", value=name
    )
    if not wid:
        raise D365Error(f"Web resource not found: {name}", code="WebResourceNotFound")
    return wid


def resolve_webresource_id(backend: D365Backend, name_or_guid: str) -> str:
    """Resolve a web resource id from a GUID or a name.

    A GUID is returned unchanged (no HTTP); otherwise resolve by name. Used by
    `app create --icon-webresource`.
    """
    stripped = name_or_guid.strip()
    rid = normalize_guid(stripped)
    if rid is not None:
        return rid
    return _resolve_id_by_name(backend, stripped)
