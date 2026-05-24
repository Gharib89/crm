"""Entity record CRUD via the D365 Web API.

Every public function returns a plain dict (or list of dicts) — callers are responsible
for formatting.
"""

from __future__ import annotations

import re
from typing import Any

from crm.utils.d365_backend import D365Backend, D365Error, as_dict


_GUID_RE = re.compile(
    r"^[{(]?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}[)}]?$"
)


def _normalize_id(record_id: str) -> str:
    """Strip braces and validate GUID format."""
    rid = record_id.strip().lstrip("{(").rstrip("})")
    if not _GUID_RE.match(rid):
        raise D365Error(f"Invalid record id (expected GUID): {record_id!r}")
    return rid


def _build_record_path(entity_set: str, record_id: str) -> str:
    return f"{entity_set}({_normalize_id(record_id)})"


# ── Read ────────────────────────────────────────────────────────────────


def retrieve(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    *,
    select: list[str] | None = None,
    expand: list[str] | None = None,
    include_annotations: bool = False,
) -> dict[str, Any]:
    """GET a single record by GUID."""
    params: dict[str, Any] = {}
    if select:
        params["$select"] = ",".join(select)
    if expand:
        params["$expand"] = ",".join(expand)
    headers = {"Prefer": 'odata.include-annotations="*"'} if include_annotations else None
    result = backend.get(
        _build_record_path(entity_set, record_id),
        params=params or None,
        extra_headers=headers,
    )
    return as_dict(result)


# ── Create ──────────────────────────────────────────────────────────────


def create(
    backend: D365Backend,
    entity_set: str,
    payload: dict[str, Any],
    *,
    return_record: bool = True,
) -> dict[str, Any]:
    """POST a new record.

    With return_record=True we add `Prefer: return=representation` to get the created
    record back in the response. Otherwise we extract the GUID from the
    `OData-EntityId` header and return `{ "id": "<guid>" }`.
    """
    headers = {"If-None-Match": "null"}
    if return_record:
        headers["Prefer"] = "return=representation"

    result = backend.post(entity_set, json_body=payload, extra_headers=headers)
    result_dict = as_dict(result)
    if not result_dict:
        return {}

    if "_dry_run" in result_dict:
        return result_dict

    if return_record:
        return result_dict

    # 204 path: response carried OData-EntityId we surfaced through _entity_id_url
    entity_id_url = result_dict.get("_entity_id_url")
    if entity_id_url:
        m = re.search(r"\(([0-9a-fA-F-]{36})\)", entity_id_url)
        if m:
            return {"id": m.group(1), "entity_id_url": entity_id_url}
    return result_dict


# ── Update ──────────────────────────────────────────────────────────────


def update(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    payload: dict[str, Any],
    *,
    prevent_create: bool = True,
    return_record: bool = False,
) -> dict[str, Any]:
    """PATCH an existing record. By default prevents accidental upsert via If-Match: *."""
    headers: dict[str, str] = {}
    if prevent_create:
        headers["If-Match"] = "*"
    if return_record:
        headers["Prefer"] = "return=representation"

    result = backend.patch(
        _build_record_path(entity_set, record_id),
        json_body=payload,
        extra_headers=headers or None,
    )
    return as_dict(result)


# ── Upsert ──────────────────────────────────────────────────────────────


def upsert(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """PATCH that creates if missing (no If-Match header)."""
    result = backend.patch(
        _build_record_path(entity_set, record_id),
        json_body=payload,
    )
    return as_dict(result)


# ── Delete ──────────────────────────────────────────────────────────────


def delete(backend: D365Backend, entity_set: str, record_id: str) -> dict[str, Any]:
    """DELETE a record."""
    result = backend.delete(_build_record_path(entity_set, record_id))
    return result if isinstance(result, dict) else {"deleted": True, "id": _normalize_id(record_id)}


# ── Associate / Disassociate ────────────────────────────────────────────


def associate(
    backend: D365Backend,
    target_set: str,
    target_id: str,
    navigation_property: str,
    related_set: str,
    related_id: str,
) -> dict[str, Any]:
    """POST to a collection-valued navigation property to associate two records.

    Use for 1:N (from the "one" side) and N:N relationships. For setting a
    single-valued lookup (N:1), use `update()` with `@odata.bind` instead.

    Reference: https://learn.microsoft.com/power-apps/developer/data-platform/webapi/associate-disassociate-entities-using-web-api
    """
    target_path = _build_record_path(target_set, target_id)
    related_url = backend.url_for(_build_record_path(related_set, related_id))
    path = f"{target_path}/{navigation_property}/$ref"
    result = as_dict(backend.post(path, json_body={"@odata.id": related_url}))
    return result if result else {"associated": True, "target": target_id, "related": related_id}


def disassociate(
    backend: D365Backend,
    target_set: str,
    target_id: str,
    navigation_property: str,
    *,
    related_set: str | None = None,
    related_id: str | None = None,
) -> dict[str, Any]:
    """DELETE a relationship.

    For collection-valued nav properties (1:N from the one side, or N:N) the related
    set + id MUST be supplied — the URL is /<nav>/$ref?$id=<related url>.

    For single-valued nav properties (N:1 lookup), omit related_set/related_id; the
    URL becomes /<nav>/$ref and removes the reference.
    """
    target_path = _build_record_path(target_set, target_id)
    if related_set and related_id:
        related_url = backend.url_for(_build_record_path(related_set, related_id))
        from urllib.parse import quote
        path = f"{target_path}/{navigation_property}/$ref?$id={quote(related_url, safe='')}"
    else:
        path = f"{target_path}/{navigation_property}/$ref"
    backend.delete(path)
    return {"disassociated": True, "target": target_id, "related": related_id}


def set_lookup(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    navigation_property: str,
    related_set: str,
    related_id: str,
) -> dict[str, Any]:
    """Set or change a single-valued lookup by `@odata.bind` PATCH.

    Equivalent to: PATCH /<set>(<id>)  { "<nav>@odata.bind": "/<related_set>(<related_id>)" }
    """
    bind_value = f"/{related_set}({_normalize_id(related_id)})"
    payload = {f"{navigation_property}@odata.bind": bind_value}
    return update(backend, entity_set, record_id, payload, prevent_create=True)


def clear_lookup(
    backend: D365Backend,
    entity_set: str,
    record_id: str,
    navigation_property: str,
) -> dict[str, Any]:
    """Clear a single-valued lookup via DELETE /<set>(<id>)/<nav>/$ref."""
    target_path = _build_record_path(entity_set, record_id)
    backend.delete(f"{target_path}/{navigation_property}/$ref")
    return {"cleared": True, "id": _normalize_id(record_id), "nav": navigation_property}
