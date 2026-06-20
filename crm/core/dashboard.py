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

from typing import Any

from crm.core.metadata import maybe_publish
from crm.utils.d365_backend import (
    D365Backend,
    D365Error,
    as_dict,
    normalize_guid,
)

_FORM_SET = "systemforms"
_ID_FIELD = "formid"

# systemform.type option values (see Microsoft Learn "systemform EntityType").
DASHBOARD_TYPE = 0       # standard system dashboard
INTERACTIVE_TYPE = 10    # interactive-experience dashboard — not API-creatable

# Dashboards are org-wide, not bound to one table — verified live on the test
# org: every type-0 systemform carries objecttypecode == "none".
_ORG_OBJECTTYPECODE = "none"

_SELECT = "formid,name,objecttypecode,description,isdefault,formxml"
# Lighter select for `list`, which omits the (large) formxml.
_LIST_SELECT = "formid,name,objecttypecode,description,isdefault"


def _normalize_dashboard_id(dashboard_id: str) -> str:
    """Strip braces and validate *dashboard_id* as a GUID (raises on a bad id),
    matching the id discipline of the other by-id core verbs."""
    rid = normalize_guid(dashboard_id)
    if rid is None:
        raise D365Error(f"Invalid dashboard id (expected GUID): {dashboard_id!r}")
    return rid


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
    """Fetch a single dashboard by id, including its ``formxml``."""
    dashboard_id = _normalize_dashboard_id(dashboard_id)
    row = as_dict(backend.get(
        f"{_FORM_SET}({dashboard_id})",
        params={"$select": _SELECT},
    ))
    return _project(row, with_xml=True)


def delete_dashboard(backend: D365Backend, dashboard_id: str) -> dict[str, Any]:
    """Delete a dashboard by id.

    Dry-run returns ``{_dry_run, would_delete, formid}``; a real delete returns
    ``{deleted, formid}``.
    """
    dashboard_id = _normalize_dashboard_id(dashboard_id)
    result = backend.delete(f"{_FORM_SET}({dashboard_id})")
    if isinstance(result, dict) and result.get("_dry_run"):
        return {"_dry_run": True, "would_delete": True, _ID_FIELD: dashboard_id}
    return {"deleted": True, _ID_FIELD: dashboard_id}


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
