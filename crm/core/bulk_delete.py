"""Server-side BulkDelete: submit an async bulk-delete job from a FetchXML query.

The Web API ``BulkDelete`` action's ``QuerySet`` parameter accepts only
``QueryExpression`` — it does not take raw FetchXml or an OData ``$filter`` (and
there is no server-side OData→QueryExpression conversion). So the caller's
FetchXml is converted to a ``QueryExpression`` server-side via the
``FetchXmlToQueryExpression`` function before submission.

Reference: https://learn.microsoft.com/power-apps/developer/data-platform/webapi/reference/bulkdelete
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any, cast

from crm.utils import safe_xml
from crm.utils.d365_backend import D365Backend, D365Error, as_dict, odata_literal

QE_ODATA_TYPE = "Microsoft.Dynamics.CRM.QueryExpression"
"""The ``@odata.type`` a converted query carries.

``QuerySet`` is typed ``Collection(QueryExpression)``, and the server binds the
object to the right subtype only when it names its type.
"""


def _with_total_record_count(fetch_xml: str) -> str:
    """Return *fetch_xml* with ``returntotalrecordcount="true"`` on its ``<fetch>``.

    The matched-record total rides back on ``@odata.count`` only when the fetch
    asks for it; the user's query usually won't, so set it for the preview read.
    """
    try:
        root = safe_xml.fromstring(fetch_xml)
    except ET.ParseError as exc:
        raise D365Error(f"--fetchxml is not well-formed XML: {exc}") from exc
    if root.tag != "fetch":
        raise D365Error("FetchXML must have a <fetch> root element.")
    root.set("returntotalrecordcount", "true")
    return ET.tostring(root, encoding="unicode")


def fetchxml_to_query_expression(backend: D365Backend, fetch_xml: str) -> dict[str, Any]:
    """Convert a FetchXML document to a QueryExpression via the server function.

    Shared by every action whose ``Query``/``QuerySet`` parameter takes a
    ``QueryExpression`` rather than raw FetchXml (``BulkDelete``,
    ``BulkDetectDuplicates``); the returned object carries its ``@odata.type`` so
    the server binds it to the right subtype.

    Validates well-formedness locally first, so a typo'd fetch fails fast with a
    clear message instead of an opaque server round-trip.
    """
    try:
        root = safe_xml.fromstring(fetch_xml)
    except ET.ParseError as exc:
        raise D365Error(f"FetchXML is not well-formed XML: {exc}") from exc
    if root.tag != "fetch":
        raise D365Error("FetchXML must have a <fetch> root element.")
    resp = as_dict(
        backend.get(
            "FetchXmlToQueryExpression(FetchXml=@p1)",
            params={"@p1": odata_literal(fetch_xml)},
        )
    )
    raw_query = resp.get("Query")
    if not isinstance(raw_query, dict):
        raise D365Error("FetchXmlToQueryExpression returned no Query object.", response_body=resp)
    query: dict[str, Any] = dict(cast("dict[str, Any]", raw_query))
    query["@odata.type"] = QE_ODATA_TYPE
    return query


def _preview_count(backend: D365Backend, entity_set: str, counting_fetch: str) -> int:
    """Return how many records *counting_fetch* matches, without deleting anything.

    *counting_fetch* must already carry ``returntotalrecordcount`` (see
    :func:`_with_total_record_count`) so the server reports the total via
    ``@odata.count``. Runs as a read, which executes even under dry-run.
    """
    raw = as_dict(backend.get(entity_set, params={"fetchXml": counting_fetch}))
    count = raw.get("@odata.count")
    if isinstance(count, int):
        return count
    value = raw.get("value")
    return len(cast("list[Any]", value)) if isinstance(value, list) else 0


def _read_counts(backend: D365Backend, job_id: str) -> dict[str, Any]:
    """Read succeeded/failed counts from the bulkdeleteoperation linked to *job_id*."""
    raw = as_dict(
        backend.get(
            "bulkdeleteoperations",
            params={
                "$select": "successcount,failurecount",
                "$filter": f"_asyncoperationid_value eq {job_id}",
                "$top": "1",
            },
        )
    )
    rows = cast("list[dict[str, Any]]", raw.get("value") or [])
    row: dict[str, Any] = rows[0] if rows else {}
    return {"succeeded": row.get("successcount"), "failed": row.get("failurecount")}


def bulk_delete(
    backend: D365Backend,
    entity_set: str,
    fetch_xml: str,
    *,
    job_name: str | None = None,
    wait: bool = False,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Submit a BulkDelete async job for records matching *fetch_xml*.

    The server requires ``StartDateTime`` in the payload; "now" runs the job at
    once, and it still deletes only the rows that exist when it starts. Under
    ``--dry-run`` the POST is a no-op echo (the reads still run), so the result
    reports the would-be scope and matched count with no job reference.
    """
    counting_fetch = _with_total_record_count(fetch_xml)
    query = fetchxml_to_query_expression(backend, fetch_xml)
    match_count = _preview_count(backend, entity_set, counting_fetch)
    name = job_name or f"crm data delete {entity_set}"
    body: dict[str, Any] = {
        "QuerySet": [query],
        "JobName": name,
        "SendEmailNotification": False,
        "ToRecipients": [],
        "CCRecipients": [],
        "RecurrencePattern": "",
        "StartDateTime": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    resp = as_dict(backend.post("BulkDelete", json_body=body))
    if "_dry_run" in resp:
        return {
            "_dry_run": True,
            "would_submit": "BulkDelete",
            "entity_set": entity_set,
            "job_name": name,
            "match_count": match_count,
        }
    job_id = resp.get("JobId")
    if not job_id:
        raise D365Error("BulkDelete returned no JobId.", response_body=resp)
    result: dict[str, Any] = {"job_id": job_id, "job_name": name, "match_count": match_count}
    if not wait:
        result["status"] = "submitted"
        return result
    backend.poll_async_operation(job_id, timeout=timeout)
    result.update(status="completed", **_read_counts(backend, job_id))
    return result
