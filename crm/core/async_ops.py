"""Browse + control asyncoperation rows on D365 on-prem.

Reference: https://learn.microsoft.com/power-apps/developer/data-platform/asynchronous-service
"""

from __future__ import annotations

from typing import Any, cast

from crm.utils.d365_backend import (
    D365Backend,
    D365Error,
    as_dict,
    normalize_guid,
    odata_literal,
)
from crm.utils.d365_types import AsyncOperationRow

_SELECT = (
    "asyncoperationid,name,messagename,statecode,statuscode,"
    "createdon,startedon,completedon,_ownerid_value,errorcode,"
    "message,friendlymessage"
)


def list_async_operations(
    backend: D365Backend,
    *,
    state: int | None = None,
    message_name: str | None = None,
    owner_id: str | None = None,
    top: int = 50,
    order_by: str = "createdon desc",
    filter: str | None = None,
) -> list[AsyncOperationRow]:
    """List asyncoperation rows. Filters are AND-joined when multiple are set."""
    filters: list[str] = []
    if filter is not None:
        filters.append(f"({filter})")
    if state is not None:
        filters.append(f"statecode eq {int(state)}")
    if message_name is not None:
        filters.append(f"messagename eq {odata_literal(message_name)}")
    if owner_id is not None:
        normalized = normalize_guid(owner_id)
        if normalized is None:
            raise D365Error(f"owner_id must be a GUID; got {owner_id!r}")
        filters.append(f"_ownerid_value eq {normalized}")

    params: dict[str, Any] = {
        "$select": _SELECT,
        "$top": str(int(top)),
        "$orderby": order_by,
    }
    if filters:
        params["$filter"] = " and ".join(filters)
    result = as_dict(backend.get("asyncoperations", params=params))
    value: Any = result.get("value", [])
    if isinstance(value, list):
        return cast(list[AsyncOperationRow], value)
    return []


def get_async_operation(
    backend: D365Backend,
    async_operation_id: str,
) -> AsyncOperationRow:
    """GET asyncoperations(<id>) and return the row."""
    params = {"$select": _SELECT}
    return cast(AsyncOperationRow, as_dict(backend.get(
        f"asyncoperations({async_operation_id})",
        params=params,
    )))


def cancel_async_operation(
    backend: D365Backend,
    async_operation_id: str,
) -> None:
    """PATCH asyncoperations(<id>) to Completed/Cancelled.

    statecode=3 (Completed) + statuscode=32 (Cancelled). Only succeeds for
    state in {0=Ready, 1=Suspended}; server returns 400 otherwise.
    """
    backend.patch(
        f"asyncoperations({async_operation_id})",
        json_body={"statecode": 3, "statuscode": 32},
    )


def list_all_async_operations(
    backend: D365Backend,
    *,
    state: int | None = None,
    message_name: str | None = None,
    owner_id: str | None = None,
    page_size: int = 50,
    max_pages: int = 20,
    order_by: str = "createdon desc",
    filter: str | None = None,
) -> list[AsyncOperationRow]:
    """Paginated variant of list_async_operations: follows @odata.nextLink up to max_pages.

    The first call uses the same `$filter` / `$select` / `$top` shape as
    list_async_operations. Subsequent calls follow the absolute URL in
    @odata.nextLink. Stops when the server stops emitting nextLink or
    when max_pages is reached.
    """
    filters: list[str] = []
    if filter is not None:
        filters.append(f"({filter})")
    if state is not None:
        filters.append(f"statecode eq {int(state)}")
    if message_name is not None:
        filters.append(f"messagename eq {odata_literal(message_name)}")
    if owner_id is not None:
        normalized = normalize_guid(owner_id)
        if normalized is None:
            raise D365Error(f"owner_id must be a GUID; got {owner_id!r}")
        filters.append(f"_ownerid_value eq {normalized}")

    params: dict[str, Any] = {
        "$select": _SELECT,
        "$top": str(int(page_size)),
        "$orderby": order_by,
    }
    if filters:
        params["$filter"] = " and ".join(filters)

    out = backend.get_collection("asyncoperations", params=params, max_pages=max_pages)
    return [cast(AsyncOperationRow, r) for r in out]
