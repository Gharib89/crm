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
from datetime import datetime, timezone
from typing import Any, cast

from crm.utils.d365_backend import D365Backend, D365Error, as_dict, odata_literal

# The QuerySet element is typed Collection(QueryExpression); the converted query
# object must carry its OData type so the server binds it to the right subtype.
_QE_ODATA_TYPE = "Microsoft.Dynamics.CRM.QueryExpression"


def _with_total_record_count(fetch_xml: str) -> str:
    """Return *fetch_xml* with ``returntotalrecordcount="true"`` on its ``<fetch>``.

    The matched-record total rides back on ``@odata.count`` only when the fetch
    asks for it; the user's query usually won't, so set it for the preview read.
    """
    try:
        root = ET.fromstring(fetch_xml)
    except ET.ParseError as exc:
        raise D365Error(f"--fetchxml is not well-formed XML: {exc}") from exc
    if root.tag != "fetch":
        raise D365Error('FetchXML must have a <fetch> root element.')
    root.set("returntotalrecordcount", "true")
    return ET.tostring(root, encoding="unicode")


def _to_query_expression(backend: D365Backend, fetch_xml: str) -> dict[str, Any]:
    """Convert a FetchXML document to a QueryExpression via the server function."""
    resp = as_dict(backend.get(
        "FetchXmlToQueryExpression(FetchXml=@p1)",
        params={"@p1": odata_literal(fetch_xml)},
    ))
    raw_query = resp.get("Query")
    if not isinstance(raw_query, dict):
        raise D365Error(
            "FetchXmlToQueryExpression returned no Query object.", response_body=resp
        )
    query: dict[str, Any] = dict(cast("dict[str, Any]", raw_query))
    query["@odata.type"] = _QE_ODATA_TYPE
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
    raw = as_dict(backend.get("bulkdeleteoperations", params={
        "$select": "successcount,failurecount",
        "$filter": f"_asyncoperationid_value eq {job_id}",
        "$top": "1",
    }))
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
    """Submit a BulkDelete async job for records matching *fetch_xml*."""
    # Validate well-formedness locally first, so a typo'd fetch fails fast with a
    # clear message instead of a server round-trip.
    counting_fetch = _with_total_record_count(fetch_xml)
    query = _to_query_expression(backend, fetch_xml)
    match_count = _preview_count(backend, entity_set, counting_fetch)
    name = job_name or f"crm data delete {entity_set}"
    body: dict[str, Any] = {
        "QuerySet": [query],
        "JobName": name,
        "SendEmailNotification": False,
        "ToRecipients": [],
        "CCRecipients": [],
        "RecurrencePattern": "",
        # The server requires StartDateTime in the payload; "now" runs the job
        # immediately (it still only deletes rows that existed when it starts).
        "StartDateTime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    resp = as_dict(backend.post("BulkDelete", json_body=body))
    if "_dry_run" in resp:
        # --dry-run: the POST was a no-op echo (reads still ran), so report the
        # would-be scope and matched count without a job reference.
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
