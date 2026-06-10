"""Query helpers: OData v4 ($filter/$select/etc.) and FetchXML."""

from __future__ import annotations

import re
from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict

# Bare OData 4.01 `in` operator (e.g. `workflowid in ('a','b')`). The Dataverse
# Web API is OData 4.0 and rejects it with a generic 500 — detect it client-side.
# Quoted string literals are stripped first (they may contain ` in (`). Lowercase-only
# and no IGNORECASE so the native `Microsoft.Dynamics.CRM.In(` function is NOT matched.
_QUOTED_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")
_BARE_IN_OPERATOR_RE = re.compile(r"\bin\s*[(\[]")


# ── OData query ─────────────────────────────────────────────────────────


def odata_query(
    backend: D365Backend,
    entity_set: str,
    *,
    select: list[str] | None = None,
    filter_: str | None = None,
    top: int | None = None,
    orderby: str | None = None,
    expand: list[str] | None = None,
    count: bool = False,
    include_annotations: bool = False,
    page_size: int | None = None,
) -> dict[str, Any]:
    """Execute a GET against an entity set with OData query options.

    Returns the raw response dict (with `value` array + optional `@odata.nextLink`).
    """
    # The entity-set arg carries the URL path only — OData options go through the
    # query-option kwargs below. A `?` or `$` means the caller baked params into
    # the path (e.g. `solutions?$select=uniquename`), which the server bounces as
    # a bare HTTP 400 with no recovery signal — reject it here. Set names and
    # bound-function paths (`RetrieveAppComponents(...)`) carry neither, so pass.
    if "?" in entity_set or "$" in entity_set:
        raise D365Error(
            "entity-set arg must be a bare set name (e.g. `solutions`); "
            "use --select/--filter for OData parameters",
            code="InvalidEntitySet",
        )

    params: dict[str, Any] = {}
    if select:
        params["$select"] = ",".join(select)
    if filter_:
        if _BARE_IN_OPERATOR_RE.search(_QUOTED_LITERAL_RE.sub("", filter_)):
            raise D365Error(
                "OData 'in' operator is not supported by the Dataverse Web API (OData 4.0).\n"
                "Use the In query function:\n"
                "  --filter \"Microsoft.Dynamics.CRM.In(PropertyName='<column>',PropertyValues=['<val1>','<val2>'])\"\n"
                "or run the equivalent FetchXML via `crm query fetchxml`."
            )
        params["$filter"] = filter_
    if top is not None:
        if top < 1:
            raise D365Error("--top must be >= 1")
        params["$top"] = str(top)
    if orderby:
        params["$orderby"] = orderby
    if expand:
        params["$expand"] = ",".join(expand)
    if count:
        params["$count"] = "true"

    headers: dict[str, str] = {}
    if include_annotations:
        headers["Prefer"] = 'odata.include-annotations="*"'
    if page_size is not None:
        if page_size < 1:
            raise D365Error("--page-size must be >= 1")
        existing = headers.get("Prefer")
        page_pref = f"odata.maxpagesize={page_size}"
        headers["Prefer"] = f"{existing},{page_pref}" if existing else page_pref

    return as_dict(backend.get(entity_set, params=params or None, extra_headers=headers or None))


# ── FetchXML query ──────────────────────────────────────────────────────


def fetchxml_query(
    backend: D365Backend,
    entity_set: str,
    fetch_xml: str,
    *,
    include_annotations: bool = False,
) -> dict[str, Any]:
    """Execute a FetchXML query against the given entity set.

    fetch_xml must be a complete `<fetch>...</fetch>` document. It's passed as the
    `fetchXml` query parameter via requests' `params=` kwarg so encoding stays
    consistent with the rest of the backend.

    Note: for very large FetchXML queries that may exceed URL length limits, $batch
    is the recommended pattern; this helper uses the inline form which is sufficient
    for the vast majority of queries.
    """
    if not fetch_xml or "<fetch" not in fetch_xml.lower():
        raise D365Error("fetch_xml must contain a <fetch> element.")

    headers: dict[str, str] | None = (
        {"Prefer": 'odata.include-annotations="*"'} if include_annotations else None
    )
    return as_dict(backend.get(
        entity_set,
        params={"fetchXml": fetch_xml},
        extra_headers=headers,
    ))


# ── Count ───────────────────────────────────────────────────────────────


def saved_query(
    backend: D365Backend,
    entity_set: str,
    savedquery_id: str,
    *,
    include_annotations: bool = False,
    page_size: int | None = None,
) -> dict[str, Any]:
    """Execute a system view (savedquery) by GUID.

    Equivalent to: GET /<set>?savedQuery=<guid>
    Reference: https://learn.microsoft.com/power-apps/developer/data-platform/webapi/retrieve-and-execute-predefined-queries
    """
    headers: dict[str, str] = {}
    if include_annotations or page_size is not None:
        prefer_parts: list[str] = []
        if include_annotations:
            prefer_parts.append('odata.include-annotations="*"')
        if page_size is not None:
            prefer_parts.append(f"odata.maxpagesize={page_size}")
        headers["Prefer"] = ",".join(prefer_parts)
    return as_dict(backend.get(
        entity_set,
        params={"savedQuery": savedquery_id},
        extra_headers=headers or None,
    ))


def user_query(
    backend: D365Backend,
    entity_set: str,
    userquery_id: str,
    *,
    include_annotations: bool = False,
    page_size: int | None = None,
) -> dict[str, Any]:
    """Execute a saved view (userquery) by GUID.

    Equivalent to: GET /<set>?userQuery=<guid>
    """
    headers: dict[str, str] = {}
    if include_annotations or page_size is not None:
        prefer_parts: list[str] = []
        if include_annotations:
            prefer_parts.append('odata.include-annotations="*"')
        if page_size is not None:
            prefer_parts.append(f"odata.maxpagesize={page_size}")
        headers["Prefer"] = ",".join(prefer_parts)
    return as_dict(backend.get(
        entity_set,
        params={"userQuery": userquery_id},
        extra_headers=headers or None,
    ))


def count_entity_set(backend: D365Backend, entity_set: str) -> int:
    """Return the integer record count for an entity set via /<set>/$count.

    Fast path: the `$count` endpoint returns a `text/plain` integer in one HTTP call.
    Fallback: if the body is missing, non-numeric, or otherwise unparseable (proxies
    occasionally strip text/plain bodies), fall through to `?$count=true` and read
    `@odata.count` from the resulting collection envelope. The fallback is
    belt-and-braces — preserves the resilience the previous implementation had.
    """
    result = backend.get(
        f"{entity_set}/$count",
        extra_headers={"Accept": "text/plain"},
        expect_json=False,
    )
    if isinstance(result, str) and result.strip():
        try:
            return int(result)
        except ValueError:
            pass  # fall through to the fallback

    # Fallback: ask the collection with $count=true and read @odata.count.
    raw = odata_query(backend, entity_set, top=1, count=True)
    c = raw.get("@odata.count")
    return int(c) if c is not None else 0


# ── RetrieveTotalRecordCount ─────────────────────────────────────────────


def total_record_count(backend: D365Backend, entity: str) -> int:
    """Call RetrieveTotalRecordCount for one entity logical name.

    D365 caches counts; value may lag inserts/deletes by minutes.
    """
    if not entity:
        raise D365Error("entity logical name is required")
    path = f"RetrieveTotalRecordCount(EntityNames=['{entity}'])"
    result: dict[str, Any] = as_dict(backend.get(path))
    coll: dict[str, Any] = result.get("EntityRecordCountCollection") or {}
    keys: list[Any] = coll.get("Keys") or []
    values: list[Any] = coll.get("Values") or []
    if not keys or not values:
        raise D365Error(
            f"RetrieveTotalRecordCount returned no rows for {entity!r}",
            response_body=result,
        )
    return int(values[0])
