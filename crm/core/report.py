"""Create, list, get, categorize, and delete custom reports headlessly.

A report is an ordinary entity (the ``reports`` set). The CLI registers two
kinds: a **Reporting Services report** whose RDL is uploaded into ``bodytext``
(``reporttypecode`` 1), or a **link report** pointing at an external URL via
``bodyurl`` (``reporttypecode`` 3). Reports are solution-aware, so writes honor
``--solution`` (``MSCRM.SolutionUniqueName``). The CLI uploads the RDL verbatim
— it does not author or validate it (Dataverse online, for instance, only
accepts an RDL whose data source uses the fetch data provider).

**Org visibility.** A report defaults to personal (``ispersonal = true``).
Making it available to the whole organization is done by setting
``ispersonal = false`` on the record — the documented Web API path. The
``MakeAvailableToOrganizationReport`` SDK message named by some docs is marked
*Deprecated — use Update* and has **no Web API binding**, so it is not callable
over the Web API this CLI speaks; the ``IsPersonal`` update is the official
equivalent on both on-prem v9.x and Dataverse online. ``create --org`` sets it
at create time.

**Categories.** ``set-category`` files a report under one of the four built-in
areas by creating a ``reportcategory`` record (``categorycode`` 1–4) bound to
the report through the live-verified ``reportid`` navigation property. A report
may carry more than one category — one ``reportcategory`` record per area.
"""

from __future__ import annotations

from typing import Any

from crm.utils.d365_backend import (
    D365Backend,
    D365Error,
    as_dict,
    normalize_guid,
)

_REPORT_SET = "reports"
_ID_FIELD = "reportid"
_CATEGORY_SET = "reportcategories"

# Single-valued navigation property on reportcategory pointing at report — the
# @odata.bind key for set-category. Live-verified on the test org
# (ManyToOne report_reportcategories → ReferencingEntityNavigationPropertyName);
# it is the lowercase logical name, not a PascalCase schema name.
_REPORT_NAV = "reportid"

# report.reporttypecode option values (see the report table reference): a
# Reporting Services report carries its RDL in bodytext; a Linked Report points
# at an external URL via bodyurl.
RDL_REPORT = 1
LINK_REPORT = 3

# reportcategory.categorycode options (Sales/Service/Marketing/Administrative).
CATEGORY_CODES: dict[str, int] = {
    "sales": 1,
    "service": 2,
    "marketing": 3,
    "administrative": 4,
}

# `get` returns the body (bodytext for an RDL, bodyurl for a link) so a report
# can be round-tripped; `list` projects to summary columns only (no large RDL).
_LIST_SELECT = "reportid,name,filename,reporttypecode,ispersonal,description"
_GET_SELECT = _LIST_SELECT + ",bodyurl,bodytext,languagecode"


def _normalize_report_id(report_id: str) -> str:
    """Strip braces and validate *report_id* as a GUID (raises on a bad id),
    matching the id discipline of the other by-id core verbs."""
    rid = normalize_guid(report_id)
    if rid is None:
        raise D365Error(f"Invalid report id (expected GUID): {report_id!r}")
    return rid


def _project(row: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    """Project a raw report row into the CLI-owned report dict shape."""
    rec: dict[str, Any] = {
        _ID_FIELD: row.get(_ID_FIELD),
        "name": row.get("name", ""),
        "filename": row.get("filename"),
        "reporttypecode": row.get("reporttypecode"),
        "ispersonal": bool(row.get("ispersonal", True)),
        "description": row.get("description"),
    }
    if full:
        rec["bodyurl"] = row.get("bodyurl")
        rec["bodytext"] = row.get("bodytext")
        rec["languagecode"] = row.get("languagecode")
    return rec


def list_reports(backend: D365Backend) -> list[dict[str, Any]]:
    """List all reports as summary rows (no RDL body — use :func:`get_report`)."""
    rows = backend.get_collection(
        _REPORT_SET, params={"$select": _LIST_SELECT})
    return [_project(row) for row in rows]


def get_report(backend: D365Backend, report_id: str) -> dict[str, Any]:
    """Fetch a single report by id, including its body (RDL text or link URL)."""
    report_id = _normalize_report_id(report_id)
    row = as_dict(backend.get(
        f"{_REPORT_SET}({report_id})", params={"$select": _GET_SELECT}))
    return _project(row, full=True)


def create_report(
    backend: D365Backend,
    *,
    name: str,
    body: str | None = None,
    filename: str | None = None,
    url: str | None = None,
    description: str | None = None,
    org: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Create a report — either an RDL upload or a link report.

    Pass exactly one of *body* (the RDL text, → ``bodytext`` with
    ``reporttypecode`` 1) or *url* (an external link, → ``bodyurl`` with
    ``reporttypecode`` 3). *org* sets ``ispersonal = false`` so the report is
    available organization-wide rather than personal. Under dry-run, returns
    ``{_dry_run, would_create}`` with the resolved body.
    """
    if (body is None) == (url is None):
        raise D365Error(
            "create requires exactly one of an RDL body (--body-file) or a "
            "link (--url).")

    payload: dict[str, Any] = {"name": name}
    if body is not None:
        payload["bodytext"] = body
        payload["reporttypecode"] = RDL_REPORT
    else:
        payload["bodyurl"] = url
        payload["reporttypecode"] = LINK_REPORT
    if filename:
        payload["filename"] = filename
    if description is not None:
        payload["description"] = description
    if org:
        # ispersonal=false == "available to the organization" (the deprecated
        # MakeAvailableToOrganizationReport message has no Web API binding).
        payload["ispersonal"] = False

    if backend.dry_run:
        return {"_dry_run": True,
                "would_create": {"entity_set": _REPORT_SET, "body": payload}}

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(_REPORT_SET, json_body=payload, extra_headers=headers))
    report_id = result.get("_entity_id")
    out: dict[str, Any] = {"created": True, "name": name, _ID_FIELD: report_id}
    if report_id is None:
        out["report_lookup_error"] = (
            "Could not parse reportid from response: "
            f"{result.get('_entity_id_url')!r}")
    return out


def set_category(
    backend: D365Backend,
    report_id: str,
    *,
    category: str,
    solution: str | None = None,
) -> dict[str, Any]:
    """File *report_id* under *category* (sales/service/marketing/administrative).

    Creates a ``reportcategory`` record carrying the mapped ``categorycode``
    (1–4) bound to the report. Under dry-run, returns ``{_dry_run, would_create}``
    with the resolved body.
    """
    report_id = _normalize_report_id(report_id)
    code = CATEGORY_CODES.get(category)
    if code is None:
        raise D365Error(
            f"Unknown category {category!r}; choose from "
            f"{', '.join(CATEGORY_CODES)}.")

    payload: dict[str, Any] = {
        "categorycode": code,
        f"{_REPORT_NAV}@odata.bind": f"/{_REPORT_SET}({report_id})",
    }
    if backend.dry_run:
        return {"_dry_run": True,
                "would_create": {"entity_set": _CATEGORY_SET, "body": payload}}

    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post(
        _CATEGORY_SET, json_body=payload, extra_headers=headers))
    out: dict[str, Any] = {
        _ID_FIELD: report_id,
        "category": category,
        "categorycode": code,
        "reportcategoryid": result.get("_entity_id"),
    }
    return out


def delete_report(backend: D365Backend, report_id: str) -> dict[str, Any]:
    """Delete a report by id.

    Dry-run returns ``{_dry_run, would_delete, reportid}``; a real delete returns
    ``{deleted, reportid}``.
    """
    report_id = _normalize_report_id(report_id)
    result = backend.delete(f"{_REPORT_SET}({report_id})")
    if isinstance(result, dict) and result.get("_dry_run"):
        return {"_dry_run": True, "would_delete": True, _ID_FIELD: report_id}
    return {"deleted": True, _ID_FIELD: report_id}
