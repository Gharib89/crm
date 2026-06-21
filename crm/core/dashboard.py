"""Create, list, get, and delete organization-owned system dashboards.

A dashboard is a ``systemform`` record with ``type = 0`` (Dashboard) and an
org-wide ``objecttypecode`` of ``"none"`` (it is not bound to a single table).
Its layout lives in the ``formxml`` column, authored from source control and
posted verbatim. The ``crm dashboard`` command group wraps these verbs so a
dashboard can be created and managed headlessly, without the dashboard designer.

``systemforms`` also backs every other form type (main, quick-create, card, …);
the verbs here scope every read to ``type eq 0`` so the group only ever sees
dashboards. Interactive-experience dashboards (``type = 10``) are **not**
programmatically creatable over the Web API — the CLI rejects that path with a
clear error rather than silently creating a different kind of record.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from typing import Any

from crm.core import xml_edit
from crm.core.metadata import maybe_publish
from crm.utils.d365_backend import (
    D365Backend,
    D365Error,
    as_dict,
    normalize_guid,
)

_FORM_SET = "systemforms"
_ID_FIELD = "formid"

# The ChartGrid control's classid — an MS-documented, live-verified platform
# constant (a chart/grid tile on a dashboard). It is a *protected* external
# reference: emitted verbatim, never regenerated (see add_chartgrid_to_formxml).
CHARTGRID_CLASSID = "{E7A81278-8635-4D9E-8D4D-59480B391C5B}"

# The base-language label code used for an inserted tile label (matches the
# dashboards/forms the project targets; multi-language authoring is out of scope).
_LABEL_LANGUAGECODE = "1033"

# systemform.type option values (see Microsoft Learn "systemform EntityType").
DASHBOARD_TYPE = 0       # standard system dashboard
INTERACTIVE_TYPE = 10    # interactive-experience dashboard — not API-creatable

# Dashboards are org-wide, not bound to one table — verified live on the test
# org: every type-0 systemform carries objecttypecode == "none".
_ORG_OBJECTTYPECODE = "none"

# `type` is fetched so the by-id verbs can confirm the target is a dashboard
# (see _require_dashboard_type); it is not projected into the output shape.
_SELECT = "formid,name,objecttypecode,description,isdefault,type,formxml"
# Lighter select for `list`, which omits the (large) formxml.
_LIST_SELECT = "formid,name,objecttypecode,description,isdefault"


def _normalize_dashboard_id(dashboard_id: str) -> str:
    """Strip braces and validate *dashboard_id* as a GUID (raises on a bad id),
    matching the id discipline of the other by-id core verbs."""
    rid = normalize_guid(dashboard_id)
    if rid is None:
        raise D365Error(f"Invalid dashboard id (expected GUID): {dashboard_id!r}")
    return rid


def _require_dashboard_type(dashboard_id: str, form_type: Any) -> None:
    """Refuse to operate on a non-dashboard ``systemform``.

    ``systemforms`` is a shared set (main / quick-create / card / … forms all
    live there), so a by-id ``get`` could project an unrelated form as a
    dashboard and — worse — a by-id ``delete`` could destroy one. Both verbs
    confirm ``type == 0`` first so the group stays scoped to dashboards.
    """
    if form_type != DASHBOARD_TYPE:
        raise D365Error(
            f"systemform {dashboard_id} is not a dashboard (type={form_type}); "
            f"the dashboard verbs only operate on system dashboards (type {DASHBOARD_TYPE}).")


def _project(row: dict[str, Any], *, with_xml: bool) -> dict[str, Any]:
    """Project a raw systemform row into the CLI-owned dashboard dict shape."""
    rec: dict[str, Any] = {
        _ID_FIELD: row.get(_ID_FIELD),
        "name": row.get("name", ""),
        "objecttypecode": row.get("objecttypecode"),
        "description": row.get("description"),
        "isdefault": bool(row.get("isdefault", False)),
    }
    if with_xml:
        rec["formxml"] = row.get("formxml") or ""
    return rec


def list_dashboards(backend: D365Backend) -> list[dict[str, Any]]:
    """List organization-owned dashboards as list-column summaries (no formxml).

    Scoped to ``type eq 0`` so other ``systemform`` types (main/quick-create/…)
    never appear; use :func:`get_dashboard` for a dashboard's ``formxml``.
    """
    rows = backend.get_collection(
        _FORM_SET,
        params={"$select": _LIST_SELECT, "$filter": f"type eq {DASHBOARD_TYPE}"},
    )
    return [_project(row, with_xml=False) for row in rows]


def get_dashboard(backend: D365Backend, dashboard_id: str) -> dict[str, Any]:
    """Fetch a single dashboard by id, including its ``formxml``.

    Raises if the id resolves to a non-dashboard ``systemform`` (see
    :func:`_require_dashboard_type`).
    """
    dashboard_id = _normalize_dashboard_id(dashboard_id)
    row = as_dict(backend.get(
        f"{_FORM_SET}({dashboard_id})",
        params={"$select": _SELECT},
    ))
    _require_dashboard_type(dashboard_id, row.get("type"))
    return _project(row, with_xml=True)


def delete_dashboard(backend: D365Backend, dashboard_id: str) -> dict[str, Any]:
    """Delete a dashboard by id.

    Pre-flight GETs the form's ``type`` (a read, so it runs even under dry-run)
    and refuses to delete a non-dashboard ``systemform`` — a mistyped id must
    not destroy a main/quick-create form on the shared set. Dry-run returns
    ``{_dry_run, would_delete, formid}``; a real delete returns
    ``{deleted, formid}``.
    """
    dashboard_id = _normalize_dashboard_id(dashboard_id)
    row = as_dict(backend.get(
        f"{_FORM_SET}({dashboard_id})", params={"$select": "formid,type"}))
    _require_dashboard_type(dashboard_id, row.get("type"))
    result = backend.delete(f"{_FORM_SET}({dashboard_id})")
    if isinstance(result, dict) and result.get("_dry_run"):
        return {"_dry_run": True, "would_delete": True, _ID_FIELD: dashboard_id}
    return {"deleted": True, _ID_FIELD: dashboard_id}


def _id_matches(value: str | None, given: str) -> bool:
    """Whether a FormXml ``id`` attribute matches a user-supplied id, tolerating
    braces and case (FormXml ids are brace-wrapped, case-insensitive GUIDs)."""
    if not value:
        return False
    return value.strip("{}").lower() == given.strip("{}").lower()


def _resolve_target_tab(root: "ET.Element", tab: str | None) -> "ET.Element":
    """Pick the ``<tab>`` to operate on (default: the first), matched by name or
    id. Raises ``D365Error`` naming the available tabs when one is absent."""
    tabs = root.findall("./tabs/tab")
    if not tabs:
        raise D365Error("Dashboard has no <tab> layout.")
    if tab is None:
        return tabs[0]
    target = next(
        (t for t in tabs
         if t.get("name") == tab or _id_matches(t.get("id"), tab)), None)
    if target is None:
        names = ", ".join(t.get("name") or "?" for t in tabs)
        raise D365Error(f"No tab {tab!r} on the dashboard. Tabs: {names}.")
    return target


def _resolve_named_section(target_tab: "ET.Element", section: str) -> "ET.Element":
    """Find an existing ``<section>`` in ``target_tab`` by name or id, or raise
    ``D365Error`` naming the available sections."""
    sections = target_tab.findall("./columns/column/sections/section")
    target = next(
        (s for s in sections
         if s.get("name") == section or _id_matches(s.get("id"), section)), None)
    if target is None:
        names = ", ".join(s.get("name") or "?" for s in sections) or "(none)"
        raise D365Error(
            f"No section {section!r} in tab {target_tab.get('name')!r}. "
            f"Sections: {names}.")
    return target


def _new_tile_section(target_tab: "ET.Element", *, colspan: int) -> "ET.Element":
    """Append a fresh single-component ``<section>`` to ``target_tab``'s first
    column and return it.

    Each tile gets its own section so the ``rowspan == count(<row>)`` invariant
    holds per component (it cannot hold for two cells sharing one section). The
    section's ``columns`` grid is sized to the tile's ``colspan``. Raises
    ``D365Error`` if the tab has no ``<columns>/<column>`` to host sections —
    the documented "tab must have at least one section" prerequisite, applied to
    the structural scaffold an add needs.
    """
    sections_el = target_tab.find("./columns/column/sections")
    if sections_el is None:
        raise D365Error(
            f"Tab {target_tab.get('name')!r} has no <columns>/<column>/<sections> "
            f"scaffold to add a section to.")
    section = ET.Element("section")
    sid = xml_edit.fresh_guid()
    section.set("id", sid)
    section.set("name", sid)
    section.set("showlabel", "false")
    section.set("showbar", "false")
    section.set("columns", "1" * max(1, colspan))
    labels = ET.SubElement(section, "labels")
    label = ET.SubElement(labels, "label")
    label.set("description", "")
    label.set("languagecode", _LABEL_LANGUAGECODE)
    ET.SubElement(section, "rows")
    sections_el.append(section)
    return section


def _append_tile(section: "ET.Element", cell: "ET.Element", *, rowspan: int) -> None:
    """Append ``cell`` as a new ``<row>`` and reconcile the section's ``<row>``
    count to the cell's ``rowspan`` (the ``rowspan == count(<row>)`` invariant).

    Existing rows are never dropped: the count is grown to
    ``max(rowspan, current)`` with empty ``<row/>`` padding and the cell's
    ``rowspan`` is set to that final count, so the invariant holds without
    corrupting any content already in the section.
    """
    rows = section.find("rows")
    if rows is None:
        rows = ET.SubElement(section, "rows")
    row = ET.SubElement(rows, "row")
    row.append(cell)
    need = max(rowspan, len(rows.findall("row")))
    while len(rows.findall("row")) < need:
        ET.SubElement(rows, "row")
    cell.set("rowspan", str(need))


def _build_chartgrid_cell(
    *, control_id: str, params: dict[str, str], label: str, colspan: int,
) -> "ET.Element":
    """A fresh ChartGrid ``<cell>`` (fresh cell id, label, control, params).

    The control carries the protected :data:`CHARTGRID_CLASSID` verbatim and a
    ``<parameters>`` bag in the order ``params`` is given.
    """
    cell = ET.Element("cell")
    cell.set("id", xml_edit.fresh_guid())
    # rowspan is finalized by _append_tile (row-count reconciliation); colspan is
    # set here as it is not touched by the layout invariant.
    cell.set("colspan", str(colspan))
    cell.set("showlabel", "true")
    labels = ET.SubElement(cell, "labels")
    lab = ET.SubElement(labels, "label")
    lab.set("description", label)
    lab.set("languagecode", _LABEL_LANGUAGECODE)
    control = ET.SubElement(cell, "control")
    control.set("id", control_id)
    control.set("classid", CHARTGRID_CLASSID)
    parameters = ET.SubElement(control, "parameters")
    for key, value in params.items():
        ET.SubElement(parameters, key).text = value
    return cell


# A dashboard holds up to six components by default. This is a *soft* cap (an
# on-prem org can raise it via PowerShell), so --force overrides it rather than
# the CLI hard-blocking. See the MS "dashboard components" guidance.
_DEFAULT_COMPONENT_CAP = 6


def _count_components(root: "ET.Element") -> int:
    """Count existing dashboard components — ``<cell>``s that host a control."""
    return sum(1 for cell in root.iter("cell") if cell.find("control") is not None)


def _guid_counter(xml: str) -> "Counter[str]":
    """Multiset of every (lowercased) GUID in ``xml`` — for the pure-append guard."""
    return Counter(g.lower() for g in xml_edit.ANY_GUID_RE.findall(xml))


def _section_has_component(section: "ET.Element") -> bool:
    """Whether ``section`` already hosts a component (a ``<cell>`` with a control)."""
    return any(c.find("control") is not None for c in section.iter("cell"))


def _unique_control_id(root: "ET.Element", base: str = "ChartGrid") -> str:
    """A control ``id`` not already used by any control on the dashboard.

    Control ids must be unique within a dashboard's FormXml — a duplicate is
    accepted by the PATCH but rejected at publish ("Duplicate id found for
    control element"). Returns ``base`` if free, else ``base_2``, ``base_3``, …
    """
    used = {c.get("id") for c in root.iter("control")}
    if base not in used:
        return base
    n = 2
    while f"{base}_{n}" in used:
        n += 1
    return f"{base}_{n}"


def add_chartgrid_to_formxml(
    formxml: str, *, params: dict[str, str], label: str,
    tab: str | None = None, section: str | None = None,
    rowspan: int = 1, colspan: int = 1,
    force: bool = False,
    control_id: str = "ChartGrid",
) -> str:
    """Return ``formxml`` with a new ChartGrid ``<cell>`` spliced into a section.

    By default the tile lands in a fresh ``<section>`` of the target tab (the
    first tab unless ``tab`` selects another) — one component per section, so
    the documented ``rowspan == count(<row>)`` grammar invariant holds for each
    tile (it cannot hold for two cells sharing a section). Pass ``section`` to
    co-locate the tile in an existing section instead. The section's ``<row>``
    count is reconciled to the cell's ``rowspan`` to satisfy the invariant.

    Refuses to exceed the default six-component cap unless ``force`` is set. A
    guard re-reads every GUID the splice did not introduce and refuses to return
    FormXml whose pre-existing ids/classids/external references changed.
    """
    root = xml_edit.parse_xml(formxml, label="dashboard's FormXml")
    if not force and _count_components(root) >= _DEFAULT_COMPONENT_CAP:
        raise D365Error(
            f"Dashboard already has {_DEFAULT_COMPONENT_CAP} components (the "
            f"default cap). Pass --force to add more.")
    target_tab = _resolve_target_tab(root, tab)
    is_new_section = section is None
    if is_new_section:
        target = _new_tile_section(target_tab, colspan=colspan)
    else:
        target = _resolve_named_section(target_tab, section)
        # A section can hold only one component while keeping the
        # rowspan == count(<row>) invariant (adding a second cell would
        # invalidate the first's rowspan and risk a publish-time rejection), so
        # refuse to co-locate into an already-occupied section.
        if _section_has_component(target):
            raise D365Error(
                f"Section {section!r} already has a component; a dashboard "
                f"component needs its own section. Omit --section to add a new "
                f"one, or target an empty section.")
    cell = _build_chartgrid_cell(
        control_id=_unique_control_id(root, control_id),
        params=params, label=label, colspan=colspan)
    _append_tile(target, cell, rowspan=rowspan)
    new_xml = xml_edit.serialize_xml(root)
    # The splice must be a pure append: the new XML's GUIDs equal the old ones
    # plus exactly the added subtree's (the new section, or just the new cell
    # when co-locating). Asserting that *multiset* equality catches any stray
    # rewrite or drop of a pre-existing id, classid or external reference. A
    # plain set-exclusion guard would have a blind spot here precisely because
    # the ChartGrid classid (and a shared view ref) is duplicated across tiles —
    # excusing it globally would stop policing its other occurrences — so the
    # count-exact check is used instead.
    added_subtree = target if is_new_section else cell
    if (_guid_counter(new_xml)
            != _guid_counter(formxml) + _guid_counter(
                xml_edit.serialize_xml(added_subtree))):
        raise D365Error(
            "dashboard tile add altered a pre-existing id/classid/external "
            "reference; refusing to write a possibly corrupt dashboard.")
    return new_xml


def _braced(guid: str) -> str:
    """A normalized GUID in the brace-wrapped form FormXml uses."""
    return "{" + guid + "}"


def _resolve_view(backend: D365Backend, view: str) -> tuple[str, str]:
    """Validate ``view`` is an existing savedquery and return its
    ``(savedqueryid, returnedtypecode)`` — the latter is the tile's
    ``TargetEntityType``. Refuses a non-GUID (savedqueries have no alternate
    key, so a name cannot be resolved without an entity context)."""
    vid = normalize_guid(view)
    if vid is None:
        raise D365Error(
            f"--view must be a savedquery id (GUID): {view!r}")
    row = as_dict(backend.get(
        f"savedqueries({vid})",
        params={"$select": "savedqueryid,returnedtypecode,name"}))
    entity = row.get("returnedtypecode")
    if not entity:
        raise D365Error(f"savedquery {vid} has no returnedtypecode (entity).")
    return vid, str(entity)


def _resolve_visualization(
    backend: D365Backend, chart: str, *, entity: str
) -> str:
    """Validate ``chart`` is an existing org-owned savedqueryvisualization whose
    primary entity matches the grid's ``entity``; return its id. A mismatch is
    refused — a chart bound to a different table renders broken on the grid."""
    cid = normalize_guid(chart)
    if cid is None:
        raise D365Error(
            f"--chart must be a savedqueryvisualization id (GUID): {chart!r}")
    row = as_dict(backend.get(
        f"savedqueryvisualizations({cid})",
        params={"$select": "savedqueryvisualizationid,primaryentitytypecode,name"}))
    primary = row.get("primaryentitytypecode")
    if primary != entity:
        raise D365Error(
            f"visualization {cid} is bound to entity {primary!r}, but the view "
            f"targets {entity!r}; a chart's primary entity must match its grid.")
    return cid


# The fixed grid behaviour flags every ChartGrid tile carries (live-verified on
# the stock dashboards). They are presentation toggles, not references, so they
# are constants rather than flags — keeping the tile's surface to what the issue
# asked for (the view, the chart, the layout).
_GRID_TOGGLES = {
    "EnableQuickFind": "true",
    "EnableViewPicker": "true",
    "EnableJumpBar": "true",
    "EnableChartPicker": "true",
}


def _commit_tile(
    backend: D365Backend, dashboard_id: str, dashboard: dict[str, Any],
    new_xml: str, *, action: str, publish: bool, solution: str | None,
    extra: dict[str, Any],
) -> dict[str, Any]:
    """Build the result dict and PATCH the dashboard's ``formxml`` (or preview
    under dry-run) via the shared direct-PATCH commit.

    Like the forms family, this does not opt into ``commit_xml_patch``'s
    read-back T3 (``read_back=None``): a Web API GET returns the *published*
    layer, so an in-process read-back only verifies on a ``--publish`` write and
    would silently skip T3 on the (recommended) ``--no-publish`` batching path.
    The structural T3 — classid intact, refs landed verbatim, rowspan invariant
    — is asserted by the live e2e (``test_dashboard_add_chart_and_view``) on the
    published layer instead.
    """
    out: dict[str, Any] = {
        _ID_FIELD: dashboard_id, "name": dashboard.get("name"), "action": action}
    out.update({k: v for k, v in extra.items() if v is not None})
    return xml_edit.commit_xml_patch(
        backend, entity_set=_FORM_SET, record_id=dashboard_id, column="formxml",
        new_xml=new_xml, result=out, dry_run_flag="would_add",
        publish=publish, solution=solution)


def add_chart_to_dashboard(
    backend: D365Backend, dashboard_id: str, *,
    view: str, chart: str,
    tab: str | None = None, section: str | None = None,
    rowspan: int = 1, colspan: int = 1, force: bool = False,
    records_per_page: int = 10,
    publish: bool = False, solution: str | None = None,
) -> dict[str, Any]:
    """Add a chart (ChartGrid, ``ChartGridMode=Chart``) tile to a dashboard.

    Validates that ``view`` is a savedquery (its ``returnedtypecode`` becomes the
    tile's ``TargetEntityType``) and ``chart`` is an org-owned visualization on
    that same entity, then splices a protected-classid ChartGrid cell into the
    target section and PATCHes the dashboard's ``formxml``.
    """
    info = get_dashboard(backend, dashboard_id)
    view_id, entity = _resolve_view(backend, view)
    vis_id = _resolve_visualization(backend, chart, entity=entity)
    params = {
        "TargetEntityType": entity,
        "ChartGridMode": "Chart",
        **_GRID_TOGGLES,
        "RecordsPerPage": str(records_per_page),
        "ViewId": _braced(view_id),
        "IsUserView": "false",
        "ViewIds": "",
        "AutoExpand": "Fixed",
        "VisualizationId": _braced(vis_id),
        "IsUserChart": "false",
    }
    new_xml = add_chartgrid_to_formxml(
        info["formxml"], params=params, label=entity,
        tab=tab, section=section, rowspan=rowspan, colspan=colspan, force=force)
    return _commit_tile(
        backend, info[_ID_FIELD], info, new_xml, action="add-chart",
        publish=publish, solution=solution,
        extra={"view": view_id, "chart": vis_id, "entity": entity,
               "tab": tab, "section": section})


# add-view ChartGridMode values, keyed by the friendly --mode token.
_VIEW_MODES = {"list": "List", "all": "All"}


def add_view_to_dashboard(
    backend: D365Backend, dashboard_id: str, *,
    view: str, mode: str = "list", records_per_page: int = 10,
    tab: str | None = None, section: str | None = None,
    rowspan: int = 1, colspan: int = 1, force: bool = False,
    publish: bool = False, solution: str | None = None,
) -> dict[str, Any]:
    """Add a view-only grid (ChartGrid, no visualization) tile to a dashboard.

    ``mode`` selects ``ChartGridMode``: ``list`` (grid only) or ``all`` (grid
    with the chart toggle). Validates that ``view`` is a savedquery and derives
    the tile's ``TargetEntityType`` from its entity.
    """
    grid_mode = _VIEW_MODES.get(mode)
    if grid_mode is None:
        raise D365Error(
            f"--mode must be one of {', '.join(_VIEW_MODES)}; got {mode!r}.")
    info = get_dashboard(backend, dashboard_id)
    view_id, entity = _resolve_view(backend, view)
    params = {
        "TargetEntityType": entity,
        "ChartGridMode": grid_mode,
        **_GRID_TOGGLES,
        "RecordsPerPage": str(records_per_page),
        "ViewId": _braced(view_id),
        "IsUserView": "false",
        "ViewIds": "",
        "AutoExpand": "Fixed",
    }
    new_xml = add_chartgrid_to_formxml(
        info["formxml"], params=params, label=entity,
        tab=tab, section=section, rowspan=rowspan, colspan=colspan, force=force)
    return _commit_tile(
        backend, info[_ID_FIELD], info, new_xml, action="add-view",
        publish=publish, solution=solution,
        extra={"view": view_id, "entity": entity, "mode": mode,
               "tab": tab, "section": section})


def create_dashboard(
    backend: D365Backend,
    *,
    name: str,
    formxml: str,
    description: str | None = None,
    solution: str | None = None,
    publish: bool = False,
) -> dict[str, Any]:
    """Create an organization-owned system dashboard (``systemform`` type 0).

    *formxml* is the dashboard layout XML, posted verbatim (authored in the
    designer or held in source control — the CLI does not generate it).
    ``publish=True`` runs ``PublishAllXml`` after the write so the dashboard
    appears without a manual publish step.

    Interactive-experience (type-10) dashboards are not creatable over the Web
    API; that path is rejected at the command layer before reaching here.
    """
    body: dict[str, Any] = {
        "type": DASHBOARD_TYPE,
        "name": name,
        "formxml": formxml,
        "objecttypecode": _ORG_OBJECTTYPECODE,
    }
    if description is not None:
        body["description"] = description

    if backend.dry_run:
        return {"_dry_run": True,
                "would_create": {"entity_set": _FORM_SET, "body": body}}

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(_FORM_SET, json_body=body, extra_headers=headers))
    entity_id_url = result.get("_entity_id_url") or ""
    dashboard_id = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "name": name,
        _ID_FIELD: dashboard_id,
    }
    if dashboard_id is None:
        out["dashboard_lookup_error"] = (
            f"Could not parse {_ID_FIELD} from response: {entity_id_url!r}")
    maybe_publish(backend, out, publish)
    return out
