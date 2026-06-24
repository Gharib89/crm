"""Typed Web API response shapes consumed by crm.core.

These TypedDicts cover only the fields the codebase actually reads — they
are not a complete model of the Dataverse Web API. Use TypedDict.__total__
False for response shapes where the server may omit fields depending on
the operation.

Reference: https://learn.microsoft.com/power-apps/developer/data-platform/webapi/
"""

from __future__ import annotations

from typing import Any, TypedDict, Union


class BatchOperation(TypedDict, total=False):
    """One operation inside a $batch request.

    `method` and `url` are required. `body` is required on POST/PATCH and
    rejected on GET/DELETE. `headers` is optional (per-op overrides such
    as `If-Match` or `MSCRMCallerID`). `content_id` is optional;
    consumed only inside changesets for `$<n>` back-references.
    """

    method: str
    url: str
    body: dict[str, Any]
    headers: dict[str, str]
    content_id: Union[str, int]


class BatchResult(TypedDict):
    """One result inside a $batch response, aligned to input order."""

    method: str
    url: str
    status: int
    headers: dict[str, str]
    body: Union[dict[str, Any], str, None]
    error: Union[str, None]


class AsyncOperationRow(TypedDict, total=False):
    """Subset of asyncoperation fields the CLI reads + displays."""

    asyncoperationid: str
    name: str
    messagename: str
    statecode: int
    statuscode: int
    createdon: str
    startedon: str
    completedon: str
    _ownerid_value: str
    errorcode: int
    message: str
    friendlymessage: str
