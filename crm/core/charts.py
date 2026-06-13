"""Read and clone savedqueryvisualization (system chart) records.

Mirrors forms.py (read_entity_forms / clone_form_to_entity). A chart is read
from the `savedqueryvisualizations` set, its stored XML entity-retargeted, and
recreated against a new `primaryentitytypecode`. Chart retarget logic is
isolated here so it is testable independently of the clone orchestrator, and so
a future `crm chart` command can wrap it the way `view` wraps `views.py`.

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

import re
from typing import Any

from crm.core.metadata import maybe_publish
from crm.utils.d365_backend import D365Backend, D365Error, as_dict, odata_literal

_CHART_SELECT = (
    "savedqueryvisualizationid,name,primaryentitytypecode,"
    "datadescription,presentationdescription,description,isdefault"
)


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
