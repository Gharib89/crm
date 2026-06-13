"""Custom view (savedquery) creation and reading.

Generates LayoutXml (grid columns) + FetchXml (columns, order, optional
active-state filter) and POSTs a public view (querytype 0). Read-back on
create is non-fatal, matching the metadata-write precedent.
"""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree
from xml.sax.saxutils import quoteattr

from crm.utils.d365_backend import D365Backend, D365Error, as_dict, odata_literal
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
                    order_by: str | None, filter_active: bool,
                    order_desc: bool = False) -> str:
    id_attr = f"{entity}id"
    attrs = f'<attribute name={quoteattr(id_attr)} />' + "".join(
        f'<attribute name={quoteattr(name)} />' for name, _ in columns
    )
    descending = "true" if order_desc else "false"
    order = (
        f'<order attribute={quoteattr(order_by)} descending="{descending}" />'
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
    order_desc: bool = False,
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
    existing = backend.get_collection(
        "savedqueries",
        params={
            "$filter": (f"name eq {odata_literal(name)} "
                        f"and returnedtypecode eq {odata_literal(entity)} "
                        "and querytype eq 0"),
            "$select": "savedqueryid,name",
        },
    )
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
        "fetchxml": _build_fetchxml(entity, columns, order_by, filter_active,
                                    order_desc),
    }
    headers = {"MSCRM.SolutionUniqueName": solution} if solution else None
    result = as_dict(backend.post("savedqueries", json_body=body,
                                  extra_headers=headers))
    if result.get("_dry_run"):
        result["_exists"] = bool(existing)
        result["would_skip"] = bool(existing) and if_exists == "skip"
        return result

    entity_id_url = result.get("_entity_id_url") or ""
    sqid = result.get("_entity_id")
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
    else:
        out["view_lookup_error"] = (
            f"Could not parse savedqueryid from response: {entity_id_url!r}")
    maybe_publish(backend, out, publish)
    return out


def read_entity_views(
    backend: D365Backend,
    entity_logical_name: str,
) -> list[dict[str, Any]]:
    """Read an entity's public saved-query views as view projection dicts.

    Returns a list of dicts with keys:
    - ``name``: view name (may be empty string for nameless rows)
    - ``columns``: list of ``{"name": str, "width": int}`` (may be empty when
      layoutxml is absent or unparseable)
    - ``order_by``: attribute name string (omitted if no <order> element)
    - ``is_default``: bool

    Callers that need apply-valid projections (e.g. ``build_entity_spec``) are
    responsible for dropping views with an empty ``name`` or empty ``columns``
    before inserting them into a spec.
    """
    rows = backend.get_collection(
        "savedqueries",
        params={
            "$filter": (
                f"returnedtypecode eq {odata_literal(entity_logical_name)} "
                "and querytype eq 0"
            ),
            "$select": "name,layoutxml,fetchxml,isdefault",
        },
    )

    result: list[dict[str, Any]] = []
    for row in rows:
        # --- parse columns from layoutxml ---
        columns: list[dict[str, Any]] = []
        layoutxml = row.get("layoutxml") or ""
        if layoutxml:
            try:
                root = ElementTree.fromstring(layoutxml)
            except ElementTree.ParseError:
                root = None
            if root is not None:
                for cell in root.iter("cell"):
                    col_name = cell.get("name")
                    if not col_name:
                        continue
                    col: dict[str, Any] = {"name": col_name}
                    width_str = cell.get("width")
                    if width_str is not None:
                        try:
                            col["width"] = int(width_str)
                        except ValueError:
                            pass
                    columns.append(col)

        # --- parse order_by from fetchxml ---
        order_by: str | None = None
        fetchxml = row.get("fetchxml") or ""
        if fetchxml:
            try:
                fetch_root = ElementTree.fromstring(fetchxml)
            except ElementTree.ParseError:
                fetch_root = None
            if fetch_root is not None:
                order_el = fetch_root.find(".//{*}order")
                if order_el is not None:
                    order_by = order_el.get("attribute") or None

        view: dict[str, Any] = {
            "name": row.get("name", ""),
            "columns": columns,
            "is_default": bool(row.get("isdefault", False)),
        }
        if order_by is not None:
            view["order_by"] = order_by

        result.append(view)

    return result
