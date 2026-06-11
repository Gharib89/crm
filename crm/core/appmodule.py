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
from xml.sax.saxutils import quoteattr

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

    un_lit = unique_name.replace("'", "''")
    existing = as_dict(backend.get(
        "appmodules",
        params={"$filter": f"uniquename eq '{un_lit}'",
                "$select": "appmoduleid,uniquename"},
    )).get("value", [])
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
    else:
        out["app_lookup_error"] = (
            f"Could not parse appmoduleid from response: {entity_id_url!r}")
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
    out: dict[str, Any] = {"created": True, "sitemapid": smid,
                           "sitemapname": sitemap_name, "solution": solution}
    if not smid:
        out["sitemap_lookup_error"] = (
            f"Could not parse sitemapid from response: {entity_id_url!r}")
    return out


def build_sitemapxml(
    areas: list[tuple[str, str]],
    groups: list[tuple[str, str, str]],
    subareas: list[tuple[str, str, str, str | None]],
) -> str:
    """Build a compact SiteMapXml from structured Area/Group/SubArea inputs.

    `areas` = [(area_id, title)], `groups` = [(area_id, group_id, title)],
    `subareas` = [(area_id, group_id, entity, title_or_None)]. SubArea Ids are
    auto-allocated from the entity logical name (suffixed `_2`, `_3`, … to stay
    unique across the whole document); a SubArea Title is emitted only when a
    non-empty one is given, else the platform derives the label from the entity.
    All attribute values are quoteattr-escaped. Raises D365Error on empty input,
    duplicate Ids, or broken Area/Group references.
    """
    if not areas:
        raise D365Error("a sitemap needs at least one area.")

    # Strip identifier fields up front: whitespace-only Ids are then caught as
    # empty and emitted Ids stay clean for programmatic callers (the CLI already
    # strips; this mirrors set_sitemap's .strip() discipline). Titles keep their
    # own blank-handling below.
    areas = [(area_id.strip(), title) for area_id, title in areas]
    groups = [(a.strip(), g.strip(), t) for a, g, t in groups]
    subareas = [(a.strip(), g.strip(), e.strip(), t) for a, g, e, t in subareas]

    area_ids: set[str] = set()
    for area_id, _ in areas:
        if not area_id:
            raise D365Error("area_id must not be empty.")
        if area_id in area_ids:
            raise D365Error(f"duplicate area Id {area_id!r}.")
        area_ids.add(area_id)

    # group_id -> area_id, for both uniqueness and subarea referential checks.
    group_area: dict[str, str] = {}
    # area_id -> ordered list of (group_id, title)
    groups_by_area: dict[str, list[tuple[str, str]]] = {a: [] for a, _ in areas}
    for area_id, group_id, title in groups:
        if not group_id:
            raise D365Error("group_id must not be empty.")
        if group_id in group_area:
            raise D365Error(f"duplicate group Id {group_id!r}.")
        if area_id not in area_ids:
            raise D365Error(
                f"group {group_id!r} references unknown area {area_id!r}.")
        group_area[group_id] = area_id
        groups_by_area[area_id].append((group_id, title))

    # group_id -> ordered list of (sub_id, entity, title_or_None)
    subs_by_group: dict[str, list[tuple[str, str, str | None]]] = {
        g: [] for g in group_area
    }
    used_sub_ids: set[str] = set()
    for area_id, group_id, entity, title in subareas:
        if not entity:
            raise D365Error("subarea entity must not be empty.")
        if group_area.get(group_id) != area_id:
            raise D365Error(
                f"subarea on entity {entity!r} does not reference a defined "
                f"group {group_id!r} in area {area_id!r}.")
        sub_id = entity
        n = 1
        while sub_id in used_sub_ids:
            n += 1
            sub_id = f"{entity}_{n}"
        used_sub_ids.add(sub_id)
        subs_by_group[group_id].append((sub_id, entity, title))

    parts: list[str] = ["<SiteMap>"]
    for area_id, area_title in areas:
        a_title = area_title if area_title.strip() else area_id
        parts.append(f"<Area Id={quoteattr(area_id)} Title={quoteattr(a_title)}>")
        for group_id, group_title in groups_by_area[area_id]:
            g_title = group_title if group_title.strip() else group_id
            parts.append(
                f"<Group Id={quoteattr(group_id)} Title={quoteattr(g_title)}>")
            for sub_id, entity, title in subs_by_group[group_id]:
                attrs = f"Id={quoteattr(sub_id)} Entity={quoteattr(entity)}"
                if title and title.strip():
                    attrs += f" Title={quoteattr(title)}"
                parts.append(f"<SubArea {attrs} />")
            parts.append("</Group>")
        parts.append("</Area>")
    parts.append("</SiteMap>")
    return "".join(parts)


def build_sitemap(
    backend: D365Backend,
    *,
    sitemap_name: str,
    areas: list[tuple[str, str]],
    groups: list[tuple[str, str, str]],
    subareas: list[tuple[str, str, str, str | None]],
    unique_name: str | None = None,
    solution: str | None = None,
    publish: bool = False,
) -> dict[str, Any]:
    """Build a SiteMapXml from structured inputs and create the sitemaps row.

    Under dry-run, returns `{_dry_run, sitemapname, sitemapxml}` with the built
    XML and does NOT POST. Otherwise delegates to `set_sitemap` so the POST body
    is byte-identical to the existing set-sitemap path, then optionally publishes.
    """
    if not sitemap_name.strip():
        raise D365Error("sitemap_name must not be empty.")
    xml = build_sitemapxml(areas, groups, subareas)
    if backend.dry_run:
        return {"_dry_run": True, "sitemapname": sitemap_name, "sitemapxml": xml}
    out = set_sitemap(backend, sitemap_name=sitemap_name, sitemap_xml=xml,
                      unique_name=unique_name, solution=solution)
    maybe_publish(backend, out, publish)
    return out
