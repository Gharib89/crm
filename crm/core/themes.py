"""Read, create, update, and publish application themes.

A theme is an ordinary entity (the ``themes`` set) carrying product-branding
columns — the navigation-bar / header / link colors (each a ``#rrggbb`` string)
plus a ``logoid`` lookup to a web resource — and one bound action,
``PublishTheme``, that promotes a theme to the org's active theme.

**Themes are not solution-aware.** They are not solution components, so they do
not travel with a solution export and there is no ``--solution`` plumbing here;
the ``crm theme`` help and the how-to state this so callers don't expect a theme
to move across orgs with a packaged solution.

The ``logoid`` lookup binds through the ``logoimage`` navigation property
(``logoimage@odata.bind``), verified against the live ``theme`` metadata — the
bind key is the navigation property name, which differs from the attribute
logical name.
"""

from __future__ import annotations

from typing import Any

from crm.utils.d365_backend import (
    D365Backend,
    D365Error,
    as_dict,
    normalize_guid,
)

_THEME_SET = "themes"

# `get` returns the full branding record; `list` projects to summary columns
# only (no per-color noise), mirroring the chart list/get split.
_THEME_SELECT = (
    "themeid,name,type,isdefaulttheme,logotooltip,"
    "maincolor,navbarbackgroundcolor,navbarshelfcolor,headercolor,"
    "globallinkcolor,selectedlinkeffect,hoverlinkeffect,processcontrolcolor,"
    "defaultentitycolor,defaultcustomentitycolor,controlshade,"
    "pageheaderbackgroundcolor,panelheaderbackgroundcolor"
)
_THEME_LIST_SELECT = "themeid,name,type,isdefaulttheme"

# The logoid lookup's single-valued navigation property (NOT the attribute
# logical name) — the @odata.bind key. Live-verified on the theme metadata
# (relationship lk_theme_logoid → ReferencingEntityNavigationPropertyName).
_LOGO_NAV = "logoimage"


def _normalize_theme_id(theme_id: str) -> str:
    """Strip braces and validate *theme_id* as a GUID (raises on a bad id),
    matching the id discipline of the other by-id core verbs."""
    rid = normalize_guid(theme_id)
    if rid is None:
        raise D365Error(f"Invalid theme id (expected GUID): {theme_id!r}")
    return rid


def _resolve_logo_bind(backend: D365Backend, logo: str) -> dict[str, str]:
    """Resolve a web-resource name or GUID to a ``logoimage@odata.bind`` entry.

    A GUID binds directly; a name is resolved to its id with a live read (which
    runs even under dry-run, per the reads-execute rule). Raises when the named
    web resource does not exist."""
    wrid = normalize_guid(logo) or backend.resolve_id_by_name(
        "webresourceset",
        filter_field="name",
        id_field="webresourceid",
        value=logo,
    )
    if not wrid:
        raise D365Error(
            f"Web resource not found: {logo!r}", code="WebResourceNotFound")
    return {f"{_LOGO_NAV}@odata.bind": f"/webresourceset({wrid})"}


def _build_body(
    backend: D365Backend,
    *,
    name: str | None,
    attributes: dict[str, Any] | None,
    logo: str | None,
) -> dict[str, Any]:
    """Assemble a theme write body from name + raw attributes + an optional logo
    bind. ``attributes`` keys are used verbatim (so the caller may pass any
    writable theme column); the logo bind, when given, is added last."""
    body: dict[str, Any] = dict(attributes or {})
    if name is not None:
        body["name"] = name
    if logo is not None:
        body.update(_resolve_logo_bind(backend, logo))
    return body


def list_themes(backend: D365Backend) -> list[dict[str, Any]]:
    """List all themes as list-column summaries (id, name, type, default flag).

    The color columns are not fetched — use :func:`get_theme` for a theme's full
    branding. Themes are org-wide, so there is no per-entity scoping."""
    rows = backend.get_collection(
        _THEME_SET, params={"$select": _THEME_LIST_SELECT})
    return [
        {
            "themeid": row.get("themeid"),
            "name": row.get("name", ""),
            "type": row.get("type"),
            "isdefaulttheme": bool(row.get("isdefaulttheme", False)),
        }
        for row in rows
    ]


def get_theme(backend: D365Backend, theme_id: str) -> dict[str, Any]:
    """Fetch a single theme by id, including its branding columns."""
    theme_id = _normalize_theme_id(theme_id)
    return as_dict(backend.get(
        f"{_THEME_SET}({theme_id})", params={"$select": _THEME_SELECT}))


def create_theme(
    backend: D365Backend,
    *,
    name: str,
    attributes: dict[str, Any] | None = None,
    logo: str | None = None,
) -> dict[str, Any]:
    """Create a theme.

    ``attributes`` carries branding columns (colors, ``logotooltip``, ``type``,
    …) by their logical names; ``logo`` is a web-resource name or GUID bound to
    the theme's logo. Under dry-run, returns ``{_dry_run, would_create}`` with
    the fully resolved body (the logo lookup having run live)."""
    body = _build_body(backend, name=name, attributes=attributes, logo=logo)

    if backend.dry_run:
        # The logo name->id read already ran live (reads-execute); surface the
        # resolved body rather than the backend's opaque echo (mirrors
        # charts.create_chart).
        return {"_dry_run": True,
                "would_create": {"entity_set": _THEME_SET, "body": body}}

    result = as_dict(backend.post(_THEME_SET, json_body=body))
    theme_id = result.get("_entity_id")
    out: dict[str, Any] = {"created": True, "name": name, "themeid": theme_id}
    if theme_id is None:
        out["theme_lookup_error"] = (
            "Could not parse themeid from response: "
            f"{result.get('_entity_id_url')!r}")
    return out


def update_theme(
    backend: D365Backend,
    theme_id: str,
    *,
    name: str | None = None,
    attributes: dict[str, Any] | None = None,
    logo: str | None = None,
) -> dict[str, Any]:
    """Update a theme's name, branding columns, and/or logo.

    At least one of ``name`` / ``attributes`` / ``logo`` must be given. Under
    dry-run, returns ``{_dry_run, would_update}`` with the resolved PATCH body."""
    theme_id = _normalize_theme_id(theme_id)
    body = _build_body(backend, name=name, attributes=attributes, logo=logo)
    if not body:
        raise D365Error(
            "Nothing to update: pass --name, --set, and/or --logo.")

    if backend.dry_run:
        return {"_dry_run": True,
                "would_update": {"entity_set": _THEME_SET,
                                 "themeid": theme_id, "body": body}}

    backend.patch(f"{_THEME_SET}({theme_id})", json_body=body, etag="*")
    return {"updated": True, "themeid": theme_id}


def publish_theme(backend: D365Backend, theme_id: str) -> dict[str, Any]:
    """Publish a theme via the ``PublishTheme`` bound action, making it the
    active org theme.

    Under dry-run, returns ``{_dry_run, would_publish}`` (the action is a write,
    so it is short-circuited)."""
    theme_id = _normalize_theme_id(theme_id)
    if backend.dry_run:
        return {"_dry_run": True, "would_publish": {"themeid": theme_id}}

    result = as_dict(backend.post(
        f"{_THEME_SET}({theme_id})/Microsoft.Dynamics.CRM.PublishTheme"))
    if result:
        return result
    return {"published": True, "themeid": theme_id}
