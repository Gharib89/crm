"""Read, clone, create, get, list, and delete system and user charts.

Mirrors forms.py (read_entity_forms / clone_form_to_entity). A chart is read
from the `savedqueryvisualizations` set, its stored XML entity-retargeted, and
recreated against a new `primaryentitytypecode`. Chart retarget logic is
isolated here so it is testable independently of the clone orchestrator.

A chart binds to its host entity via ``primaryentitytypecode`` — the
logical-name STRING (not an ObjectTypeCode integer). It carries two XML columns:

- ``datadescription`` embeds an aggregate FetchXML
  (``<datadefinition><fetchcollection><fetch ...><entity name="<src_entity>">``)
  that references the source entity name and MUST be retargeted.
- ``presentationdescription`` is rendering XML (series/axes keyed by data
  *alias* such as ``aggregate_column``) and carries no entity reference. A
  defensive word-boundary swap is applied anyway; it is expected to be a no-op.

Entity sets:
  - ``savedqueryvisualizations`` — system (org-wide) charts
  - ``userqueryvisualizations`` — user-owned charts
"""

from __future__ import annotations

import re
from typing import Any

from crm.core.metadata import maybe_publish
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
_USER_CHART_SELECT = (
    "userqueryvisualizationid,name,primaryentitytypecode,"
    "datadescription,presentationdescription,description"
)


def _chart_set(user: bool) -> str:
    return _USER_CHART_SET if user else _SYSTEM_CHART_SET


def _chart_id_field(user: bool) -> str:
    return "userqueryvisualizationid" if user else "savedqueryvisualizationid"


def _chart_select(user: bool) -> str:
    return _USER_CHART_SELECT if user else _CHART_SELECT


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
    """List an entity's charts.

    Returns system charts by default (``savedqueryvisualizations``); pass
    ``user=True`` for user charts (``userqueryvisualizations``).
    """
    entity_set = _chart_set(user)
    id_field = _chart_id_field(user)
    filt = f"primaryentitytypecode eq {odata_literal(entity_logical_name)}"
    rows = backend.get_collection(
        entity_set,
        params={"$select": _chart_select(user), "$filter": filt},
    )
    result: list[dict[str, Any]] = []
    for row in rows:
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
        result.append(rec)
    return result


def get_chart(
    backend: D365Backend,
    chart_id: str,
    *,
    user: bool = False,
) -> dict[str, Any]:
    """Fetch a single chart by ID.

    Reads from ``savedqueryvisualizations`` (system) or
    ``userqueryvisualizations`` (user) depending on ``user``.
    """
    entity_set = _chart_set(user)
    id_field = _chart_id_field(user)
    row = as_dict(
        backend.get(f"{entity_set}({chart_id})",
                    params={"$select": _chart_select(user)})
    )
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


def delete_chart(
    backend: D365Backend,
    chart_id: str,
    *,
    user: bool = False,
) -> dict[str, Any]:
    """Delete a chart by ID.

    Dry-run returns ``{_dry_run, would_delete, <id_field>}``;
    a real delete returns ``{deleted, <id_field>}``.
    """
    entity_set = _chart_set(user)
    id_field = _chart_id_field(user)
    result = backend.delete(f"{entity_set}({chart_id})")
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

    Pass either ``data_description`` + ``presentation_description`` (XML-based
    chart from source control) **or** ``web_resource`` (name or GUID of a web
    resource visualization) — the two paths are mutually exclusive.

    ``user=True`` creates a ``userqueryvisualization``; the default creates a
    ``savedqueryvisualization`` (org-wide system chart).
    ``publish=True`` runs ``PublishAllXml`` after creation.
    """
    entity_set = _chart_set(user)
    id_field = _chart_id_field(user)
    body: dict[str, Any] = {
        "name": name,
        "primaryentitytypecode": entity,
    }
    if description is not None:
        body["description"] = description

    if web_resource is not None:
        wrid = normalize_guid(web_resource)
        if wrid is None:
            wrid = backend.resolve_id_by_name(
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

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(entity_set, json_body=body, extra_headers=headers))
    if result.get("_dry_run"):
        return result

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
            f"Could not parse {id_field} from response: {entity_id_url!r}"
        )
    maybe_publish(backend, out, publish)
    return out
