"""Read, clone, create, get, list, and delete system and user charts.

Mirrors forms.py (read_entity_forms / clone_form_to_entity). A chart is read
from the `savedqueryvisualizations` set, its stored XML entity-retargeted, and
recreated against a new `primaryentitytypecode`. Chart retarget logic is
isolated here so it is testable independently of the clone orchestrator. The
list/get/create/delete verbs the ``crm chart`` command wraps live beside the
clone logic and share its projection and write helpers.

Two entity sets back the verbs: ``savedqueryvisualizations`` for system
(org-wide) charts and ``userqueryvisualizations`` for user-owned charts. They
differ only in id column and that user charts carry no ``isdefault`` flag.

A chart binds to its host entity via ``primaryentitytypecode`` — the
logical-name STRING (not an ObjectTypeCode integer). It carries two XML columns:

- ``datadescription`` embeds an aggregate FetchXML
  (``<datadefinition><fetchcollection><fetch ...><entity name="<src_entity>">``)
  that references the source entity name and MUST be retargeted.
- ``presentationdescription`` is rendering XML (series/axes keyed by data
  *alias* such as ``aggregate_column``) and carries no entity reference. A
  defensive word-boundary swap is applied anyway; it is expected to be a no-op.
"""

from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from typing import Any, Callable

from crm.core.metadata import attribute_info, maybe_publish
from crm.core.xml_edit import commit_xml_patches, parse_xml, serialize_xml
from crm.utils.d365_backend import (
    D365Backend,
    D365Error,
    as_dict,
    normalize_guid,
    odata_literal,
)

_SYSTEM_CHART_SET = "savedqueryvisualizations"
_USER_CHART_SET = "userqueryvisualizations"

_CHART_SELECT = (
    "savedqueryvisualizationid,name,primaryentitytypecode,"
    "datadescription,presentationdescription,description,isdefault"
)
# User charts have no isdefault column.
_USER_CHART_SELECT = (
    "userqueryvisualizationid,name,primaryentitytypecode,"
    "datadescription,presentationdescription,description"
)
# Lighter selects for `list`, which projects to list columns only and never
# returns the (potentially large) datadescription/presentationdescription XML.
_CHART_LIST_SELECT = "savedqueryvisualizationid,name,primaryentitytypecode,isdefault"
_USER_CHART_LIST_SELECT = "userqueryvisualizationid,name,primaryentitytypecode"


def _chart_set(user: bool) -> str:
    return _USER_CHART_SET if user else _SYSTEM_CHART_SET


def _chart_id_field(user: bool) -> str:
    return "userqueryvisualizationid" if user else "savedqueryvisualizationid"


def _chart_select(user: bool) -> str:
    return _USER_CHART_SELECT if user else _CHART_SELECT


def _chart_list_select(user: bool) -> str:
    return _USER_CHART_LIST_SELECT if user else _CHART_LIST_SELECT


def _normalize_chart_id(chart_id: str) -> str:
    """Strip braces and validate *chart_id* as a GUID (raises on a bad id),
    matching the id discipline of the other by-id core verbs."""
    rid = normalize_guid(chart_id)
    if rid is None:
        raise D365Error(f"Invalid chart id (expected GUID): {chart_id!r}")
    return rid


def _project_chart(row: dict[str, Any], *, user: bool) -> dict[str, Any]:
    """Project a raw chart row into the CLI-owned dict shape (id field varies by
    target; ``isdefault`` is system-only)."""
    id_field = _chart_id_field(user)
    rec: dict[str, Any] = {
        id_field: row.get(id_field),
        "name": row.get("name", ""),
        "primaryentitytypecode": row.get("primaryentitytypecode"),
        "datadescription": row.get("datadescription") or "",
        "presentationdescription": row.get("presentationdescription") or "",
        "description": row.get("description"),
    }
    if not user:
        rec["isdefault"] = bool(row.get("isdefault", False))
    return rec


def _project_chart_summary(row: dict[str, Any], *, user: bool) -> dict[str, Any]:
    """Project a chart row into list columns only (no XML)."""
    id_field = _chart_id_field(user)
    rec: dict[str, Any] = {
        id_field: row.get(id_field),
        "name": row.get("name", ""),
        "primaryentitytypecode": row.get("primaryentitytypecode"),
    }
    if not user:
        rec["isdefault"] = bool(row.get("isdefault", False))
    return rec


def retarget_chartxml(xml: str, *, src_entity: str, dst_entity: str) -> str:
    """Rewrite a chart's stored XML to reference the clone entity.

    Swaps whole-token occurrences of ``src_entity`` for ``dst_entity``. Word
    boundaries protect attribute logical names that merely start with the entity
    name (e.g. ``new_projectid``, ``new_project_code`` are left intact) — the
    clone reuses those attribute names verbatim, so their bindings must not
    change. Only the entity name itself (the FetchXML ``<entity name>`` ref)
    moves.
    """
    if not xml:
        return xml
    return re.sub(rf"\b{re.escape(src_entity)}\b", dst_entity, xml)


def read_entity_charts(
    backend: D365Backend,
    entity_logical_name: str,
) -> list[dict[str, Any]]:
    """Read an entity's system charts as projection dicts.

    Returns dicts with keys ``savedqueryvisualizationid, name,
    primaryentitytypecode, datadescription, presentationdescription,
    description, isdefault``.
    """
    filt = f"primaryentitytypecode eq {odata_literal(entity_logical_name)}"
    rows = backend.get_collection(
        "savedqueryvisualizations",
        params={"$select": _CHART_SELECT, "$filter": filt},
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append({
            "savedqueryvisualizationid": row.get("savedqueryvisualizationid"),
            "name": row.get("name", ""),
            "primaryentitytypecode": row.get("primaryentitytypecode"),
            "datadescription": row.get("datadescription") or "",
            "presentationdescription": row.get("presentationdescription") or "",
            "description": row.get("description"),
            "isdefault": bool(row.get("isdefault", False)),
        })
    return result


def clone_chart_to_entity(
    backend: D365Backend,
    chart: dict[str, Any],
    new_entity: str,
    *,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Create a savedqueryvisualization on ``new_entity`` from a chart dict.

    Retargets ``datadescription`` and ``presentationdescription`` and sets
    ``primaryentitytypecode`` to the clone. The server assigns a fresh
    savedqueryvisualizationid. Read-back is via the OData-EntityId header,
    matching the form/metadata-write precedent.
    """
    src_entity = chart.get("primaryentitytypecode")
    if not src_entity:
        raise D365Error("chart is missing primaryentitytypecode; cannot retarget.")
    body: dict[str, Any] = {
        "name": chart.get("name"),
        "primaryentitytypecode": new_entity,
        "datadescription": retarget_chartxml(
            chart.get("datadescription", ""), src_entity=src_entity, dst_entity=new_entity),
        "presentationdescription": retarget_chartxml(
            chart.get("presentationdescription", ""), src_entity=src_entity, dst_entity=new_entity),
    }
    if chart.get("description") is not None:
        body["description"] = chart["description"]
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(
        "savedqueryvisualizations", json_body=body, extra_headers=headers))
    if result.get("_dry_run"):
        return result

    entity_id_url = result.get("_entity_id_url") or ""
    savedqueryvisualizationid = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "name": chart.get("name", ""),
        "savedqueryvisualizationid": savedqueryvisualizationid,
        "primaryentitytypecode": new_entity,
    }
    if savedqueryvisualizationid is None:
        out["chart_lookup_error"] = (
            f"Could not parse savedqueryvisualizationid from response: {entity_id_url!r}")
    maybe_publish(backend, out, publish)
    return out


def list_entity_charts(
    backend: D365Backend,
    entity_logical_name: str,
    *,
    user: bool = False,
) -> list[dict[str, Any]]:
    """List an entity's charts as list-column summaries (id, name,
    primaryentitytypecode, and isdefault for system charts) — the XML columns
    are not fetched; use :func:`get_chart` for a chart's XML.

    System charts (``savedqueryvisualizations``) by default; ``user=True`` lists
    user charts (``userqueryvisualizations``).
    """
    filt = f"primaryentitytypecode eq {odata_literal(entity_logical_name)}"
    rows = backend.get_collection(
        _chart_set(user),
        params={"$select": _chart_list_select(user), "$filter": filt},
    )
    return [_project_chart_summary(row, user=user) for row in rows]


def get_chart(
    backend: D365Backend,
    chart_id: str,
    *,
    user: bool = False,
) -> dict[str, Any]:
    """Fetch a single chart by id (system by default; ``user=True`` for user)."""
    chart_id = _normalize_chart_id(chart_id)
    row = as_dict(backend.get(
        f"{_chart_set(user)}({chart_id})",
        params={"$select": _chart_select(user)},
    ))
    return _project_chart(row, user=user)


def delete_chart(
    backend: D365Backend,
    chart_id: str,
    *,
    user: bool = False,
) -> dict[str, Any]:
    """Delete a chart by id.

    Dry-run returns ``{_dry_run, would_delete, <id_field>}``; a real delete
    returns ``{deleted, <id_field>}``.
    """
    id_field = _chart_id_field(user)
    chart_id = _normalize_chart_id(chart_id)
    result = backend.delete(f"{_chart_set(user)}({chart_id})")
    if isinstance(result, dict) and result.get("_dry_run"):
        return {"_dry_run": True, "would_delete": True, id_field: chart_id}
    return {"deleted": True, id_field: chart_id}


def create_chart(
    backend: D365Backend,
    *,
    entity: str,
    name: str,
    data_description: str | None = None,
    presentation_description: str | None = None,
    web_resource: str | None = None,
    user: bool = False,
    solution: str | None = None,
    publish: bool = False,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a system or user chart.

    Pass either ``data_description`` + ``presentation_description`` (chart XML
    from source control) **or** ``web_resource`` (the name or GUID of a web
    resource visualization) — the two paths are mutually exclusive (the CLI
    enforces this; the core trusts its caller). ``user=True`` creates a
    ``userqueryvisualization``, else a system ``savedqueryvisualization``.
    ``publish=True`` runs ``PublishAllXml`` after the write.
    """
    entity_set = _chart_set(user)
    id_field = _chart_id_field(user)
    body: dict[str, Any] = {"name": name, "primaryentitytypecode": entity}
    if description is not None:
        body["description"] = description

    if web_resource is not None:
        wrid = normalize_guid(web_resource) or backend.resolve_id_by_name(
            "webresourceset",
            filter_field="name",
            id_field="webresourceid",
            value=web_resource,
        )
        if not wrid:
            raise D365Error(
                f"Web resource not found: {web_resource!r}",
                code="WebResourceNotFound",
            )
        body["webresourceid@odata.bind"] = f"/webresourceset({wrid})"
    else:
        body["datadescription"] = data_description or ""
        body["presentationdescription"] = presentation_description or ""

    if backend.dry_run:
        # Any web-resource read above already ran live (reads-execute rule);
        # surface the fully resolved body rather than the backend's opaque echo
        # (mirrors entity.create_from_record).
        return {"_dry_run": True,
                "would_create": {"entity_set": entity_set, "body": body}}

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(entity_set, json_body=body, extra_headers=headers))
    entity_id_url = result.get("_entity_id_url") or ""
    chart_id = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "name": name,
        id_field: chart_id,
        "primaryentitytypecode": entity,
    }
    if chart_id is None:
        out["chart_lookup_error"] = (
            f"Could not parse {id_field} from response: {entity_id_url!r}")
    maybe_publish(backend, out, publish)
    return out


# --- Chart XML editors ----------------------------------------------------------
#
# The chart XML has two coupled containers that the editors keep consistent:
#
#   datadescription: <datadefinition><fetchcollection><fetch><entity name=...>
#     <attribute groupby="true" alias="groupby_column"/>      (the category)
#     <attribute aggregate="count" alias="aggregate_column"/> (a series)
#     ...</entity></fetch></fetchcollection>
#     <categorycollection><category alias="groupby_column">
#       <measurecollection><measure alias="aggregate_column"/></measurecollection>
#     </category></categorycollection></datadefinition>
#
#   presentationdescription: <Chart><Series><Series ChartType=.../>...</Series>...
#     — the inner <Series> elements carry no alias; the i-th measure couples to
#     the i-th inner <Series> positionally.
#
# The alias-coupling invariant (validated by _validate_alias_coupling): every
# <category alias> references a groupby fetch attribute alias, every <measure
# alias> references an aggregate fetch attribute alias, and the measure count
# equals the inner-series count.

# Dynamics caps a non-comparison chart at five series; a comparison chart
# (two categories) is a single series across two groupings.
MAX_SERIES = 5


def _require(el: ET.Element | None, what: str) -> ET.Element:
    if el is None:
        raise D365Error(f"chart XML has no {what}.")
    return el


def _fetch_el(data_root: ET.Element) -> ET.Element:
    return _require(data_root.find(".//fetch"), "<fetch> element in datadescription")


def _entity_el(fetch: ET.Element) -> ET.Element:
    return _require(fetch.find("entity"), "<entity> element in the <fetch>")


def _fetch_attrs(data_root: ET.Element) -> list[ET.Element]:
    return list(_entity_el(_fetch_el(data_root)).findall("attribute"))


def _categories(data_root: ET.Element) -> list[ET.Element]:
    cc = data_root.find("categorycollection")
    return list(cc.findall("category")) if cc is not None else []


def _measure_collections(category: ET.Element) -> list[ET.Element]:
    """A category's ``<measurecollection>`` elements (one per series)."""
    return list(category.findall("measurecollection"))


def _measures(data_root: ET.Element) -> list[ET.Element]:
    out: list[ET.Element] = []
    for cat in _categories(data_root):
        for mc in _measure_collections(cat):
            out.extend(mc.findall("measure"))
    return out


def _inner_series(pres_root: ET.Element) -> list[ET.Element]:
    """The inner ``<Series>`` elements (children of the top ``<Series>`` wrapper)."""
    wrapper = pres_root.find("Series")
    return list(wrapper.findall("Series")) if wrapper is not None else []


def _pres_root_of(chart: dict[str, Any]) -> ET.Element | None:
    """Parse a chart dict's presentationdescription, or None when it is empty."""
    pres = chart.get("presentationdescription") or ""
    return parse_xml(pres, label="chart presentationdescription") if pres else None


def _reject_comparison(chart: dict[str, Any], verb: str) -> None:
    """Refuse a per-series edit on a comparison chart (two categories).

    A comparison chart pairs two categories against a single series, so adding
    or removing a series can't keep both categories balanced — surface that
    directly instead of letting the generic alias-coupling check fail later."""
    data_root = parse_xml(chart.get("datadescription") or "", label="chart datadescription")
    if len(_categories(data_root)) >= 2:
        raise D365Error(
            f"{verb} is not supported on a comparison chart (two categories); "
            "use 'chart update' to replace the chart XML.")


def _validate_fetch_metadata(
    backend: D365Backend, *, expected_entity: str, data_root: ET.Element
) -> None:
    """Refuse a fetch that re-homes the chart or names attributes that do not exist.

    ``primaryentitytypecode`` is protected: a fetch ``<entity name>`` other than
    the chart's host entity would silently re-home the chart, so it is rejected.
    Every ``<attribute name>`` is then resolved against live metadata (a 404
    surfaces as a typed error) so a typo cannot land a chart that renders empty.
    """
    entity = _entity_el(_fetch_el(data_root))
    name = entity.get("name")
    if name != expected_entity:
        raise D365Error(
            f"fetch <entity name={name!r}> does not match the chart's "
            f"primaryentitytypecode {expected_entity!r}; re-homing a chart to "
            "another entity is not supported.")
    # Validate the primary entity's attributes and every <link-entity>'s
    # attributes against their own target entity — a typo'd column anywhere in
    # the fetch otherwise lands a chart that renders empty.
    _validate_entity_attributes(backend, entity, expected_entity)


def _validate_entity_attributes(
    backend: D365Backend, element: ET.Element, entity_name: str
) -> None:
    """Resolve each direct ``<attribute name>`` of ``element`` against
    ``entity_name``'s metadata, then recurse into its ``<link-entity>`` children
    (whose attributes belong to the link's target entity)."""
    for attr in element.findall("attribute"):
        col = attr.get("name")
        if not col:
            continue
        try:
            attribute_info(backend, entity_name, col)
        except D365Error as exc:
            raise D365Error(
                f"fetch attribute {col!r} does not exist on {entity_name!r}."
            ) from exc
    for link in element.findall("link-entity"):
        target = link.get("name")
        if target:
            _validate_entity_attributes(backend, link, target)


def _validate_alias_coupling(
    data_root: ET.Element, pres_root: ET.Element | None = None
) -> None:
    """Enforce the cross-container alias-coupling invariant and the series caps.

    Each series is one ``<measurecollection>`` within a category. The server
    requires the inner ``<Series>`` count to equal each category's
    measure-collection count, so this validates: every ``<category alias>``
    references a groupby fetch attribute alias; every ``<measure alias>`` an
    aggregate one; every category carries the same number of measure collections;
    and (when ``pres_root`` is given) the inner-series count equals it. A chart
    carries one category (1…``MAX_SERIES`` series) or two (a comparison chart,
    exactly one series).
    """
    attrs = _fetch_attrs(data_root)
    groupby_aliases = {
        a.get("alias") for a in attrs if a.get("groupby") == "true" and a.get("alias")
    }
    agg_aliases = {a.get("alias") for a in attrs if a.get("aggregate") and a.get("alias")}

    cats = _categories(data_root)
    if not cats:
        raise D365Error(
            "datadescription has no <category>; a chart needs a grouping.")
    for cat in cats:
        alias = cat.get("alias")
        if alias not in groupby_aliases:
            raise D365Error(
                f"category alias {alias!r} does not match any groupby fetch "
                "attribute alias (alias-coupling violated).")

    measures = _measures(data_root)
    if not measures:
        raise D365Error(
            "datadescription has no <measure>; a chart needs a series.")
    for measure in measures:
        alias = measure.get("alias")
        if alias not in agg_aliases:
            raise D365Error(
                f"measure alias {alias!r} does not match any aggregate fetch "
                "attribute alias (alias-coupling violated).")

    # Each category must carry the same number of measure collections — that
    # shared count is the chart's series count.
    mc_counts = {len(_measure_collections(cat)) for cat in cats}
    if len(mc_counts) != 1:
        raise D365Error(
            "every category must have the same number of measure collections "
            "(alias-coupling violated).")
    n_series = mc_counts.pop()
    if n_series == 0:
        raise D365Error("datadescription has no <measurecollection>; a chart needs a series.")

    n_cat = len(cats)
    if n_cat == 2:
        if n_series != 1:
            raise D365Error(
                "a comparison chart (two categories) must have exactly one series.")
    elif n_cat == 1:
        if not 1 <= n_series <= MAX_SERIES:
            raise D365Error(
                f"a chart must have 1 to {MAX_SERIES} series; found {n_series}.")
    else:
        raise D365Error(f"a chart must have one or two categories; found {n_cat}.")

    if pres_root is not None:
        n_pres = len(_inner_series(pres_root))
        if n_pres != n_series:
            raise D365Error(
                f"presentationdescription has {n_pres} series but each category "
                f"has {n_series} measure collection(s); they must match "
                "(alias-coupling violated).")


def _commit_chart_change(
    backend: D365Backend,
    chart_id: str,
    *,
    user: bool,
    columns: dict[str, str],
    action: str,
    publish: bool,
    solution: str | None,
    read_back: "Callable[[dict[str, str]], None] | None" = None,
) -> dict[str, Any]:
    """PATCH a chart's XML column(s) (or preview), then maybe publish + read-back.

    Publish is forced off for user charts: a ``userqueryvisualization`` is a
    personal view that is never part of the published customization layer, so
    ``PublishAllXml`` does not apply. System-chart edits publish by default and
    pass ``read_back`` through for the T3 verification of the published state.
    """
    id_field = _chart_id_field(user)
    result: dict[str, Any] = {id_field: chart_id, "action": action, "user": user}
    effective_publish = publish and not user
    return commit_xml_patches(
        backend, entity_set=_chart_set(user), record_id=chart_id,
        columns=columns, result=result, dry_run_flag="would_update",
        publish=effective_publish, solution=solution,
        read_back=read_back if effective_publish else None)


def update_chart(
    backend: D365Backend,
    chart_id: str,
    *,
    data_description: str | None = None,
    presentation_description: str | None = None,
    name: str | None = None,
    description: str | None = None,
    chart_type: str | None = None,
    user: bool = False,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Update a chart's XML, name/description, and/or series chart type.

    The host of the shared cross-container validation: on a partial XML update
    (only one of ``data_description`` / ``presentation_description`` given) the
    other column is read live so the alias-coupling pair is validated together.
    ``primaryentitytypecode`` is never written (no re-homing).
    """
    chart_id = _normalize_chart_id(chart_id)
    if not any(v is not None for v in (
            data_description, presentation_description, name, description, chart_type)):
        raise D365Error(
            "nothing to update: pass at least one of --data-description, "
            "--presentation-description, --name, --description, or --type.")
    current = get_chart(backend, chart_id, user=user)

    new_data = data_description if data_description is not None else current["datadescription"]
    new_pres = presentation_description if presentation_description is not None \
        else current["presentationdescription"]
    if chart_type is not None:
        new_pres = _set_chart_type(new_pres, chart_type)

    columns: dict[str, str] = {}
    if name is not None:
        columns["name"] = name
    if description is not None:
        columns["description"] = description

    # Validate the XML pair whenever either XML column changed (or --type rewrote
    # the presentation), reading the unchanged side from the live chart.
    if data_description is not None or presentation_description is not None or chart_type is not None:
        data_root = parse_xml(new_data, label="chart datadescription")
        pres_root = parse_xml(new_pres, label="chart presentationdescription") if new_pres else None
        _validate_fetch_metadata(
            backend, expected_entity=current["primaryentitytypecode"], data_root=data_root)
        _validate_alias_coupling(data_root, pres_root)
        if data_description is not None:
            columns["datadescription"] = new_data
        if presentation_description is not None or chart_type is not None:
            columns["presentationdescription"] = new_pres

    def _verify(cols: dict[str, str]) -> None:
        if "datadescription" in cols:
            parse_xml(cols["datadescription"], label="chart datadescription")
        if "presentationdescription" in cols:
            parse_xml(cols["presentationdescription"], label="chart presentationdescription")

    return _commit_chart_change(
        backend, chart_id, user=user, columns=columns, action="update",
        publish=publish, solution=solution, read_back=_verify)


def set_chart_fetch(
    backend: D365Backend,
    chart_id: str,
    *,
    fetch: str,
    user: bool = False,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Replace the inner ``<fetch>`` of a chart's datadescription, keeping its
    categorycollection. Validates the fetch entity (no re-homing), attribute
    existence, and that the surviving aliases still couple."""
    chart_id = _normalize_chart_id(chart_id)
    current = get_chart(backend, chart_id, user=user)
    new_data = _replace_fetch(current["datadescription"], fetch)
    data_root = parse_xml(new_data, label="chart datadescription")
    _validate_fetch_metadata(
        backend, expected_entity=current["primaryentitytypecode"], data_root=data_root)
    # The presentationdescription is unchanged but the new fetch's aliases must
    # still couple to its series, so validate the full three-layer pair.
    _validate_alias_coupling(data_root, _pres_root_of(current))

    def _verify(cols: dict[str, str]) -> None:
        parse_xml(cols["datadescription"], label="chart datadescription")

    return _commit_chart_change(
        backend, chart_id, user=user, columns={"datadescription": new_data},
        action="set-fetch", publish=publish, solution=solution, read_back=_verify)


def add_chart_series(
    backend: D365Backend,
    chart_id: str,
    *,
    column: str,
    aggregate: str,
    alias: str,
    user: bool = False,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Add an aggregate series (fetch attribute + measure + presentation series).

    Validates the aggregated column exists, the alias is new, and the resulting
    series count stays within the caps. Touches both XML columns in one PATCH."""
    chart_id = _normalize_chart_id(chart_id)
    current = get_chart(backend, chart_id, user=user)
    _reject_comparison(current, "add-series")
    attribute_info_or_raise(backend, current["primaryentitytypecode"], column)
    new_data, new_pres = _append_series(
        current["datadescription"], current["presentationdescription"],
        column=column, aggregate=aggregate, alias=alias)
    data_root = parse_xml(new_data, label="chart datadescription")
    pres_root = parse_xml(new_pres, label="chart presentationdescription")
    _validate_alias_coupling(data_root, pres_root)

    def _verify(cols: dict[str, str]) -> None:
        root = parse_xml(cols["datadescription"], label="chart datadescription")
        if alias not in {a.get("alias") for a in _fetch_attrs(root)}:
            raise D365Error(f"read-back: series alias {alias!r} not present after add.")

    return _commit_chart_change(
        backend, chart_id, user=user,
        columns={"datadescription": new_data, "presentationdescription": new_pres},
        action="add-series", publish=publish, solution=solution, read_back=_verify)


def remove_chart_series(
    backend: D365Backend,
    chart_id: str,
    *,
    alias: str,
    user: bool = False,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Remove an aggregate series by its alias (fetch attribute + measure +
    the positionally-coupled presentation series). Refuses the last series."""
    chart_id = _normalize_chart_id(chart_id)
    current = get_chart(backend, chart_id, user=user)
    _reject_comparison(current, "remove-series")
    new_data, new_pres = _drop_series(
        current["datadescription"], current["presentationdescription"], alias=alias)
    data_root = parse_xml(new_data, label="chart datadescription")
    pres_root = parse_xml(new_pres, label="chart presentationdescription")
    _validate_alias_coupling(data_root, pres_root)

    def _verify(cols: dict[str, str]) -> None:
        root = parse_xml(cols["datadescription"], label="chart datadescription")
        if alias in {a.get("alias") for a in _fetch_attrs(root)}:
            raise D365Error(f"read-back: series alias {alias!r} still present after remove.")

    return _commit_chart_change(
        backend, chart_id, user=user,
        columns={"datadescription": new_data, "presentationdescription": new_pres},
        action="remove-series", publish=publish, solution=solution, read_back=_verify)


def set_chart_groupby(
    backend: D365Backend,
    chart_id: str,
    *,
    column: str,
    dategrouping: str | None = None,
    user: bool = False,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Set the chart's grouping (category) column, optionally with a date grouping.

    Validates the new column exists; the category alias is preserved so the
    measure coupling is unaffected. Touches datadescription only."""
    chart_id = _normalize_chart_id(chart_id)
    current = get_chart(backend, chart_id, user=user)
    info = attribute_info_or_raise(backend, current["primaryentitytypecode"], column)
    if dategrouping is not None and (info.get("AttributeType") or "") != "DateTime":
        raise D365Error(
            f"--dategrouping is only valid for a date column; {column!r} is "
            f"{info.get('AttributeType') or 'an unknown type'}.")
    new_data = _set_groupby(current["datadescription"], column=column, dategrouping=dategrouping)
    data_root = parse_xml(new_data, label="chart datadescription")
    _validate_alias_coupling(data_root, _pres_root_of(current))

    def _verify(cols: dict[str, str]) -> None:
        root = parse_xml(cols["datadescription"], label="chart datadescription")
        gb = [a for a in _fetch_attrs(root) if a.get("groupby") == "true"]
        if not gb or gb[0].get("name") != column:
            raise D365Error(f"read-back: groupby column {column!r} did not land.")

    return _commit_chart_change(
        backend, chart_id, user=user, columns={"datadescription": new_data},
        action="set-groupby", publish=publish, solution=solution, read_back=_verify)


def attribute_info_or_raise(
    backend: D365Backend, entity: str, column: str
) -> dict[str, Any]:
    """Confirm ``column`` exists on ``entity`` and return its metadata, with a
    clean error if it does not."""
    try:
        return attribute_info(backend, entity, column)
    except D365Error as exc:
        raise D365Error(f"attribute {column!r} does not exist on {entity!r}.") from exc


# --- Pure XML mutation helpers (no backend; tested independently) ---------------


def _set_chart_type(pres_xml: str, chart_type: str) -> str:
    """Set ``ChartType`` on every inner ``<Series>`` of a presentationdescription."""
    root = parse_xml(pres_xml, label="chart presentationdescription")
    series = _inner_series(root)
    if not series:
        raise D365Error("presentationdescription has no series to set a type on.")
    for el in series:
        el.set("ChartType", chart_type)
    return serialize_xml(root)


def _replace_fetch(data_xml: str, fetch_xml: str) -> str:
    """Replace the ``<fetch>`` inside a datadescription's ``<fetchcollection>``."""
    root = parse_xml(data_xml, label="chart datadescription")
    fc = _require(root.find("fetchcollection"), "<fetchcollection>")
    new_fetch = parse_xml(fetch_xml, label="fetch")
    if new_fetch.tag != "fetch":
        raise D365Error("the --fetch file must have a <fetch> root element.")
    old = fc.find("fetch")
    if old is not None:
        fc.remove(old)
    fc.insert(0, new_fetch)
    return serialize_xml(root)


def _append_series(
    data_xml: str, pres_xml: str, *, column: str, aggregate: str, alias: str
) -> tuple[str, str]:
    """Append an aggregate attribute + measure + cloned presentation series."""
    droot = parse_xml(data_xml, label="chart datadescription")
    if alias in {a.get("alias") for a in _fetch_attrs(droot)}:
        raise D365Error(f"alias {alias!r} already exists on this chart.")
    entity = _entity_el(_fetch_el(droot))
    attr = ET.SubElement(entity, "attribute")
    attr.set("name", column)
    attr.set("aggregate", aggregate)
    attr.set("alias", alias)
    cats = _categories(droot)
    if not cats:
        raise D365Error("datadescription has no <category>; cannot add a series.")
    # One measurecollection per series — the server couples the inner <Series>
    # count to the category's measurecollection count, not its <measure> count.
    mc = ET.SubElement(cats[0], "measurecollection")
    ET.SubElement(mc, "measure").set("alias", alias)

    proot = parse_xml(pres_xml, label="chart presentationdescription")
    wrapper = _require(proot.find("Series"), "<Series> container in presentationdescription")
    existing = wrapper.findall("Series")
    if existing:
        # Clone the first series so the new one inherits the chart's styling/type.
        clone = copy.deepcopy(existing[0])
    else:
        clone = ET.Element("Series")
        clone.set("ChartType", "Column")
    wrapper.append(clone)
    return serialize_xml(droot), serialize_xml(proot)


def _drop_series(data_xml: str, pres_xml: str, *, alias: str) -> tuple[str, str]:
    """Remove the attribute + measure for ``alias`` and its positional series."""
    droot = parse_xml(data_xml, label="chart datadescription")
    # Series are positional: the i-th measurecollection (across categories, in
    # document order) couples to the i-th inner <Series>.
    collections = [(cat, mc) for cat in _categories(droot)
                   for mc in _measure_collections(cat)]
    aliases: list[str | None] = []
    for _cat, mc in collections:
        measure = mc.find("measure")
        aliases.append(measure.get("alias") if measure is not None else None)
    if alias not in aliases:
        raise D365Error(f"no series with alias {alias!r} on this chart.")
    if len(collections) <= 1:
        raise D365Error("cannot remove the last series; a chart needs at least one.")
    index = aliases.index(alias)

    cat, target_mc = collections[index]
    cat.remove(target_mc)
    entity = _entity_el(_fetch_el(droot))
    for attr in list(entity.findall("attribute")):
        if attr.get("alias") == alias:
            entity.remove(attr)

    proot = parse_xml(pres_xml, label="chart presentationdescription")
    wrapper = proot.find("Series")
    if wrapper is not None:
        series = wrapper.findall("Series")
        if index < len(series):
            wrapper.remove(series[index])
    return serialize_xml(droot), serialize_xml(proot)


def _set_groupby(data_xml: str, *, column: str, dategrouping: str | None) -> str:
    """Set the groupby attribute's column (and optional ``dategrouping``)."""
    root = parse_xml(data_xml, label="chart datadescription")
    groupby = [a for a in _fetch_attrs(root) if a.get("groupby") == "true"]
    if not groupby:
        raise D365Error("datadescription has no groupby attribute to set.")
    target = groupby[0]
    target.set("name", column)
    if dategrouping is not None:
        target.set("dategrouping", dategrouping)
    return serialize_xml(root)
