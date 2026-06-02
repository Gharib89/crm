"""Model-driven app (appmodule) + component binding.

create_app POSTs an appmodules row (read-back non-fatal). add_app_components
wraps the unbound AddAppComponents action. set_sitemap creates a sitemaps row
from raw XML.

Shapes verified live against D365 CE on-prem 9.1 (walkthrough §11):
  * appmodules requires a non-null `webresourceid`; the platform ships a default
    icon (953b9fac-…) used here when no custom icon is given.
  * AddAppComponents takes **typed entity references** — each component is its
    primary-key field plus an `@odata.type`, not a `{Type, Id}` pair. Only
    record-backed components can be bound this way; tables reach the app through
    the sitemap's `Entity=` subareas.
"""

from __future__ import annotations

import re
from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import maybe_publish

# Default app-icon web resource the platform ships (MS Learn, op-9-1).
DEFAULT_APP_ICON = "953b9fac-1e5e-e611-80d6-00155ded156f"

# Friendly component kind -> (primary-key field, OData entity type) for
# AddAppComponents. Tables (entity metadata) are intentionally absent: they are
# not crmbaseentity records, so the action can't bind them — they surface via
# the sitemap instead.
_COMPONENT_REFS: dict[str, tuple[str, str]] = {
    "view": ("savedqueryid", "savedquery"),
    "chart": ("savedqueryvisualizationid", "savedqueryvisualization"),
    "form": ("formid", "systemform"),
    "dashboard": ("formid", "systemform"),
    "sitemap": ("sitemapid", "sitemap"),
    "bpf": ("workflowid", "workflow"),
}


def create_app(
    backend: D365Backend,
    *,
    name: str,
    unique_name: str,
    description: str | None = None,
    web_resource_id: str = DEFAULT_APP_ICON,
    client_type: int = 4,
    navigation_type: int = 0,
    publish: bool = False,
    solution: str | None = None,
    if_exists: str = "error",
) -> dict[str, Any]:
    """Create a model-driven app module. Returns `{created, appmoduleid, ...}`."""
    if not name:
        raise D365Error("name is required.")
    if not unique_name or "_" not in unique_name:
        raise D365Error("unique_name must include a publisher prefix, e.g. 'cwx_crmworx'.")
    if if_exists not in ("error", "skip"):
        raise D365Error("if_exists must be 'error' or 'skip'.")

    # Force a real read even in dry-run: idempotent, and an accurate preview
    # (_exists/would_skip) needs the live answer (cf. metadata.target_exists).
    un_lit = unique_name.replace("'", "''")
    was_dry = backend.dry_run
    backend.dry_run = False
    try:
        existing = as_dict(backend.get(
            "appmodules",
            params={"$filter": f"uniquename eq '{un_lit}'",
                    "$select": "appmoduleid,uniquename"},
        )).get("value", [])
    finally:
        backend.dry_run = was_dry
    if existing and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(f"App {unique_name!r} already exists.", code="AlreadyExists")
        return {"skipped": True, "exists": True, "uniquename": unique_name,
                "appmoduleid": existing[0].get("appmoduleid")}

    body: dict[str, Any] = {
        "name": name,
        "uniquename": unique_name,
        "clienttype": client_type,
        "navigationtype": navigation_type,
        "webresourceid": web_resource_id,
    }
    if description:
        body["description"] = description
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post("appmodules", json_body=body, extra_headers=headers))
    if result.get("_dry_run"):
        result["_exists"] = bool(existing)
        result["would_skip"] = bool(existing) and if_exists == "skip"
        return result

    entity_id_url = result.get("_entity_id_url") or ""
    m = re.search(r"appmodules\(([0-9a-fA-F-]{36})\)", entity_id_url)
    app_id = m.group(1) if m else None
    out: dict[str, Any] = {
        "created": True, "name": name, "uniquename": unique_name,
        "appmoduleid": app_id, "solution": solution,
    }
    # Publish BEFORE the read-back: on on-prem 9.1 a freshly created appmodule is
    # not retrievable until published, so reading first yields a spurious
    # app_lookup_error in the common create+publish flow (walkthrough §11).
    maybe_publish(backend, out, publish)
    if app_id:
        try:
            rb = as_dict(backend.get(f"appmodules({app_id})",
                                     params={"$select": "name,uniquename,appmoduleid"}))
            out["name"] = rb.get("name", name)
        except D365Error as exc:
            out["app_lookup_error"] = f"Read-back failed: {exc}"
    return out


def add_app_components(
    backend: D365Backend,
    *,
    app_id: str,
    components: list[tuple[str, str]],
) -> dict[str, Any]:
    """Bind components to an app via the AddAppComponents action.

    `components` is a list of `(kind, guid)` where kind is one of
    _COMPONENT_REFS. Each becomes a typed entity reference in the action body.
    Raises D365Error on an unknown kind before any HTTP call.
    """
    if not app_id:
        raise D365Error("app_id is required.")
    if not components:
        raise D365Error("at least one component is required.")
    refs: list[dict[str, Any]] = []
    for kind, guid in components:
        if kind not in _COMPONENT_REFS:
            raise D365Error(
                f"unknown component kind {kind!r}; "
                f"expected one of {sorted(_COMPONENT_REFS)}."
            )
        pk, otype = _COMPONENT_REFS[kind]
        refs.append({pk: guid, "@odata.type": f"Microsoft.Dynamics.CRM.{otype}"})
    result = as_dict(backend.post(
        "AddAppComponents", json_body={"AppId": app_id, "Components": refs}))
    if result.get("_dry_run"):
        result["app_id"] = app_id
        result["components"] = len(refs)
        return result
    return {"added": len(refs), "app_id": app_id}


def set_sitemap(
    backend: D365Backend,
    *,
    sitemap_name: str,
    sitemap_xml: str,
    unique_name: str | None = None,
    solution: str | None = None,
) -> dict[str, Any]:
    """Create a sitemaps row from raw SiteMapXml. Returns `{created, sitemapid}`.

    Pass `unique_name` equal to the app's uniquename to auto-associate the
    sitemap with that app (Dataverse links them by sitemapnameunique).
    """
    if not sitemap_name.strip():
        raise D365Error("sitemap_name must not be empty.")
    if not sitemap_xml.strip():
        raise D365Error("sitemap_xml must not be empty.")
    body: dict[str, Any] = {"sitemapname": sitemap_name, "sitemapxml": sitemap_xml}
    if unique_name:
        body["sitemapnameunique"] = unique_name
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post("sitemaps", json_body=body, extra_headers=headers))
    if result.get("_dry_run"):
        return result
    entity_id_url = result.get("_entity_id_url") or ""
    m = re.search(r"sitemaps\(([0-9a-fA-F-]{36})\)", entity_id_url)
    smid = m.group(1) if m else None
    return {"created": True, "sitemapid": smid, "sitemapname": sitemap_name,
            "solution": solution}
