"""Custom view (savedquery) creation.

Generates LayoutXml (grid columns) + FetchXml (columns, order, optional
active-state filter) and POSTs a public view (querytype 0). Read-back on
create is non-fatal, matching the metadata-write precedent.
"""

from __future__ import annotations

import re
from typing import Any
from xml.sax.saxutils import quoteattr

from crm.utils.d365_backend import D365Backend, D365Error, as_dict
from crm.core.metadata import maybe_publish


def _build_layoutxml(entity: str, object_type_code: int,
                     columns: list[tuple[str, int]]) -> str:
    id_attr = f"{entity}id"
    cells = "".join(
        f'<cell name={quoteattr(name)} width="{width}" />'
        for name, width in columns
    )
    jump = columns[0][0]
    return (
        f'<grid name="resultset" object="{object_type_code}" '
        f'jump={quoteattr(jump)} select="1" icon="1" preview="1">'
        f'<row name="result" id={quoteattr(id_attr)}>{cells}</row></grid>'
    )


def _build_fetchxml(entity: str, columns: list[tuple[str, int]],
                    order_by: str | None, filter_active: bool) -> str:
    id_attr = f"{entity}id"
    attrs = f'<attribute name={quoteattr(id_attr)} />' + "".join(
        f'<attribute name={quoteattr(name)} />' for name, _ in columns
    )
    order = (
        f'<order attribute={quoteattr(order_by)} descending="false" />'
        if order_by else ""
    )
    filt = (
        '<filter type="and"><condition attribute="statecode" '
        'operator="eq" value="0" /></filter>'
        if filter_active else ""
    )
    return (
        '<fetch version="1.0" output-format="xml-platform" mapping="logical">'
        f'<entity name={quoteattr(entity)}>{attrs}{order}{filt}</entity></fetch>'
    )


def create_view(
    backend: D365Backend,
    *,
    entity: str,
    object_type_code: int,
    name: str,
    columns: list[tuple[str, int]],
    order_by: str | None = None,
    filter_active: bool = False,
    is_default: bool = False,
    publish: bool = False,
    solution: str | None = None,
    if_exists: str = "error",
) -> dict[str, Any]:
    """Create a public system view (savedquery). Returns `{created, savedqueryid, ...}`.

    Assumes the entity's primary-id attribute is ``<entity>id`` (always true for
    custom tables, which is what this command targets).
    """
    if not name:
        raise D365Error("name is required.")
    if not columns:
        raise D365Error("at least one column is required.")
    for col_name, width in columns:
        if not col_name:
            raise D365Error("column names must be non-empty.")
        if width <= 0:
            raise D365Error(f"column width must be positive: {col_name!r}={width}.")
    if if_exists not in ("error", "skip"):
        raise D365Error("if_exists must be 'error' or 'skip'.")

    # Existence guard — savedqueries has no alternate key, so query by name+type.
    # Force a real read even in dry-run: the GET never mutates, and an accurate
    # preview (_exists/would_skip) needs the live answer (cf. metadata.target_exists).
    name_lit = name.replace("'", "''")
    was_dry = backend.dry_run
    backend.dry_run = False
    try:
        existing = as_dict(backend.get(
            "savedqueries",
            params={
                "$filter": (f"name eq '{name_lit}' and returnedtypecode eq '{entity}' "
                            "and querytype eq 0"),
                "$select": "savedqueryid,name",
            },
        )).get("value", [])
    finally:
        backend.dry_run = was_dry
    if existing and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(f"View {name!r} on {entity} already exists.",
                            code="AlreadyExists")
        return {"skipped": True, "exists": True, "name": name,
                "savedqueryid": existing[0].get("savedqueryid")}

    body: dict[str, Any] = {
        "name": name,
        "returnedtypecode": entity,
        "querytype": 0,
        "isdefault": is_default,
        "layoutxml": _build_layoutxml(entity, object_type_code, columns),
        "fetchxml": _build_fetchxml(entity, columns, order_by, filter_active),
    }
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post("savedqueries", json_body=body,
                                  extra_headers=headers))
    if result.get("_dry_run"):
        result["_exists"] = bool(existing)
        result["would_skip"] = bool(existing) and if_exists == "skip"
        return result

    entity_id_url = result.get("_entity_id_url") or ""
    m = re.search(r"savedqueries\(([0-9a-fA-F-]{36})\)", entity_id_url)
    sqid = m.group(1) if m else None
    out: dict[str, Any] = {
        "created": True, "name": name, "entity": entity,
        "savedqueryid": sqid, "solution": solution,
    }
    if sqid:
        try:
            rb = as_dict(backend.get(f"savedqueries({sqid})",
                                     params={"$select": "name,savedqueryid"}))
            out["name"] = rb.get("name", name)
        except D365Error as exc:
            out["view_lookup_error"] = f"Read-back failed: {exc}"
    maybe_publish(backend, out, publish)
    return out
