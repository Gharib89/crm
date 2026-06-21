"""Custom view (savedquery) creation and reading.

Generates LayoutXml (grid columns) + FetchXml (columns, order, optional
active-state filter) and POSTs a saved view (querytype defaults to public/0;
see ``QUERY_TYPES``). Read-back on create is non-fatal, matching the
metadata-write precedent.
"""

from __future__ import annotations

from typing import Any, cast
from xml.etree import ElementTree
from xml.sax.saxutils import quoteattr

from crm.utils.d365_backend import (
    D365Backend, D365Error, as_dict, normalize_guid, odata_literal,
)
from crm.core.metadata import attribute_info, maybe_publish
from crm.core.xml_edit import commit_xml_patches, parse_xml, serialize_xml

# savedquery.querytype optionset values (friendly name → code). See
# https://learn.microsoft.com/power-apps/developer/model-driven-apps/customize-entity-views#types-of-views
QUERY_TYPES: dict[str, int] = {
    "public": 0,
    "advanced-find": 1,
    "associated": 2,
    "quick-find": 4,
    "lookup": 64,
}


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
    query_type: str = "public",
    description: str | None = None,
) -> dict[str, Any]:
    """Create a system view (savedquery). Returns `{created, savedqueryid, ...}`.

    ``query_type`` selects the savedquery type (see ``QUERY_TYPES``); defaults to
    ``public``. Assumes the entity's primary-id attribute is ``<entity>id``
    (always true for custom tables, which is what this command targets).
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
    if query_type not in QUERY_TYPES:
        raise D365Error(
            f"unknown query_type {query_type!r}; "
            f"expected one of {', '.join(QUERY_TYPES)}.")
    querytype = QUERY_TYPES[query_type]

    # Existence guard — savedqueries has no alternate key, so query by name+type.
    existing = backend.get_collection(
        "savedqueries",
        params={
            "$filter": (f"name eq {odata_literal(name)} "
                        f"and returnedtypecode eq {odata_literal(entity)} "
                        f"and querytype eq {querytype}"),
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
        "querytype": querytype,
        "isdefault": is_default,
        "layoutxml": _build_layoutxml(entity, object_type_code, columns),
        "fetchxml": _build_fetchxml(entity, columns, order_by, filter_active,
                                    order_desc),
    }
    if query_type == "quick-find":
        body["isquickfindquery"] = True
    if description is not None:
        body["description"] = description
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
    - ``savedqueryid``: view id (GUID string)
    - ``querytype``: saved-query type int (always 0 here — public views only)
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
            "$select": "name,savedqueryid,querytype,layoutxml,fetchxml,isdefault",
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
            "savedqueryid": row.get("savedqueryid"),
            "querytype": row.get("querytype"),
            "columns": columns,
            "is_default": bool(row.get("isdefault", False)),
        }
        if order_by is not None:
            view["order_by"] = order_by

        result.append(view)

    return result


# --- View XML editors (edit-columns / set-order) --------------------------------
#
# Edit an existing saved query's grid columns (layoutxml) and sort order
# (fetchxml) in place, keeping the two documents coupled. The MISMATCH INVARIANT
# — every non-PK layoutxml ``<cell name>`` must have a fetchxml
# ``<attribute name>`` — is what keeps a column from rendering with no data
# source: add writes BOTH, remove drops BOTH, and the primary-key cell+attribute
# are protected. layoutjson is cleared on a column edit so the platform rebuilds
# it from the new layoutxml (a stale layoutjson otherwise drives the modern
# read-only grid with the old columns).

# The savedquery types whose layoutxml is a column-editable grid. System query
# types with no grid layout (e.g. 8192, offline filters) are refused.
_EDITABLE_QUERYTYPES = frozenset(QUERY_TYPES.values())

_VIEW_EDIT_SELECT = (
    "savedqueryid,name,returnedtypecode,querytype,"
    "layoutxml,fetchxml,layoutjson,iscustomizable"
)


def _require_customizable(row: dict[str, Any]) -> None:
    """Refuse to PATCH a view whose ``IsCustomizable`` managed property is false.

    ``iscustomizable`` is a ``BooleanManagedProperty`` (``{"Value": bool, ...}``);
    only an explicit ``False`` blocks the edit — a missing property can't be
    judged, and the server is the final authority.
    """
    ic = row.get("iscustomizable")
    value: Any = (
        cast("dict[str, Any]", ic).get("Value") if isinstance(ic, dict) else ic)
    if value is False:
        raise D365Error(
            f"View {row.get('name')!r} is not customizable "
            "(IsCustomizable.Value is false); refusing to PATCH.",
            code="NotCustomizable")


def _resolve_editable_view(
    backend: D365Backend, *, entity: str, view: str, query_type: str
) -> dict[str, Any]:
    """Resolve EXACTLY ONE editable savedquery by name+returnedtypecode+querytype.

    ``view`` is either a savedqueryid (GUID) or a view name. savedqueries has no
    alternate key, so a name resolves by ``name`` + ``returnedtypecode`` +
    ``querytype`` and must match exactly one row (a GUID resolves directly).
    Refuses a query type with no editable grid layout and a non-customizable
    view. Returns the raw row (layoutxml/fetchxml/layoutjson/iscustomizable).
    """
    if query_type not in QUERY_TYPES:
        raise D365Error(
            f"unknown query_type {query_type!r}; "
            f"expected one of {', '.join(QUERY_TYPES)}.")
    querytype = QUERY_TYPES[query_type]
    vid = normalize_guid(view)
    if vid is not None:
        row = as_dict(backend.get(
            f"savedqueries({vid})", params={"$select": _VIEW_EDIT_SELECT}))
        if not row.get("savedqueryid"):
            raise D365Error(f"No savedquery with id {view!r}.", code="NotFound")
        rtc = row.get("returnedtypecode")
        if rtc and rtc != entity:
            raise D365Error(
                f"savedquery {view!r} is on {rtc!r}, not {entity!r}.",
                code="EntityMismatch")
    else:
        rows = backend.get_collection(
            "savedqueries",
            params={
                "$filter": (f"name eq {odata_literal(view)} "
                            f"and returnedtypecode eq {odata_literal(entity)} "
                            f"and querytype eq {querytype}"),
                "$select": _VIEW_EDIT_SELECT,
            })
        if not rows:
            raise D365Error(
                f"No {query_type} view named {view!r} on {entity}.",
                code="NotFound")
        if len(rows) > 1:
            raise D365Error(
                f"{len(rows)} views named {view!r} on {entity} "
                f"(querytype {querytype}); resolve by savedqueryid instead.",
                code="Ambiguous")
        row = rows[0]
    qt = row.get("querytype")
    if qt not in _EDITABLE_QUERYTYPES:
        raise D365Error(
            f"savedquery querytype {qt} has no editable grid layout; "
            f"edit-columns / set-order support querytypes "
            f"{sorted(_EDITABLE_QUERYTYPES)}.",
            code="NotEditable")
    _require_customizable(row)
    return row


# --- pure XML helpers (no backend; unit-tested independently) -------------------


def _layout_row(root: "ElementTree.Element") -> "ElementTree.Element":
    row = root.find(".//row")
    if row is None:
        raise D365Error("layoutxml has no <row>; cannot edit columns.")
    return row


def _fetch_entity(root: "ElementTree.Element") -> "ElementTree.Element":
    ent = root.find("entity")
    if ent is None:
        raise D365Error("fetchxml has no <entity>; cannot edit.")
    return ent


def _cell_names(row: "ElementTree.Element") -> list[str]:
    return [c.get("name") or "" for c in row.findall("cell")]


def _attr_names(entity: "ElementTree.Element") -> list[str]:
    return [a.get("name") or "" for a in entity.findall("attribute")]


def _find_cell(row: "ElementTree.Element",
               name: str) -> "ElementTree.Element | None":
    for c in row.findall("cell"):
        if c.get("name") == name:
            return c
    return None


def _find_attr(entity: "ElementTree.Element",
               name: str) -> "ElementTree.Element | None":
    for a in entity.findall("attribute"):
        if a.get("name") == name:
            return a
    return None


def _last_attribute_index(entity: "ElementTree.Element") -> int:
    last = -1
    for i, child in enumerate(list(entity)):
        if child.tag == "attribute":
            last = i
    return last


def _require_attribute(backend: D365Backend, entity: str, column: str) -> None:
    """Confirm ``column`` exists on ``entity``, with a clean error if it does not."""
    try:
        attribute_info(backend, entity, column)
    except D365Error as exc:
        raise D365Error(
            f"attribute {column!r} does not exist on {entity!r}.") from exc


def _assert_mismatch_invariant(
    layout_row: "ElementTree.Element",
    fetch_entity: "ElementTree.Element",
    *, pk: str,
) -> None:
    """Every non-PK layout ``<cell name>`` must have a fetch ``<attribute name>``.

    A column with no backing fetch attribute renders empty, so the layout and
    fetch documents must stay coupled. Dotted/aliased cell names (linked-entity
    columns) are left to the server — this tool only adds/removes simple columns.
    The PK attribute must always remain in the fetch.
    """
    attrs = set(_attr_names(fetch_entity))
    if pk not in attrs:
        raise D365Error(
            f"fetchxml is missing the primary-key attribute {pk!r}.")
    for name in _cell_names(layout_row):
        if name and name != pk and "." not in name and name not in attrs:
            raise D365Error(
                f"column {name!r} has no matching fetch <attribute>; "
                "the layout and fetch would be out of sync.")


def edit_view_columns(
    backend: D365Backend,
    *,
    entity: str,
    view: str,
    query_type: str = "public",
    add: "list[tuple[str, int]] | None" = None,
    remove: "list[str] | None" = None,
    width: "list[tuple[str, int]] | None" = None,
    reorder: "list[str] | None" = None,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Edit an existing view's grid columns (layoutxml + fetchxml), in place.

    ``add`` is ``[(logicalname, width)]`` (adds both the layout cell and the
    fetch attribute), ``remove`` drops both, ``width`` resizes existing cells,
    and ``reorder`` is a permutation of the current column names. ``reorder`` is
    exclusive of the other operations. Each added column is validated to exist,
    width must be > 0, and the primary-key column cannot be removed.
    """
    add = add or []
    remove = remove or []
    width = width or []
    has_other = bool(add or remove or width)
    if reorder is not None and has_other:
        raise D365Error(
            "--reorder cannot be combined with --add / --remove / --width.")
    if reorder is None and not has_other:
        raise D365Error(
            "nothing to do: pass --add, --remove, --width, or --reorder.")

    row = _resolve_editable_view(backend, entity=entity, view=view,
                                 query_type=query_type)
    sqid = row.get("savedqueryid")
    layoutxml = row.get("layoutxml") or ""
    fetchxml = row.get("fetchxml") or ""
    if not layoutxml:
        raise D365Error(
            f"view {view!r} has no layoutxml; it is not column-editable.")
    if not fetchxml:
        raise D365Error(f"view {view!r} has no fetchxml; cannot edit columns.")

    pk = f"{entity}id"
    layout_root = parse_xml(layoutxml, label="layoutxml")
    fetch_root = parse_xml(fetchxml, label="fetchxml")
    lrow = _layout_row(layout_root)
    fent = _fetch_entity(fetch_root)
    fetch_changed = False

    existing = _cell_names(lrow)
    for name, w in add:
        if w <= 0:
            raise D365Error(f"column width must be positive: {name!r}={w}.")
        if name in existing:
            raise D365Error(f"column {name!r} is already on the view.")
        _require_attribute(backend, entity, name)
        cell = ElementTree.SubElement(lrow, "cell")
        cell.set("name", name)
        cell.set("width", str(w))
        existing.append(name)
        if _find_attr(fent, name) is None:
            ElementTree.SubElement(fent, "attribute").set("name", name)
            fetch_changed = True

    for name in remove:
        if name == pk:
            raise D365Error(f"cannot remove the primary-key column {pk!r}.")
        cell = _find_cell(lrow, name)
        if cell is None:
            raise D365Error(f"column {name!r} is not on the view.")
        lrow.remove(cell)
        attr = _find_attr(fent, name)
        if attr is not None:
            fent.remove(attr)
            fetch_changed = True

    for name, w in width:
        if w <= 0:
            raise D365Error(f"column width must be positive: {name!r}={w}.")
        cell = _find_cell(lrow, name)
        if cell is None:
            raise D365Error(f"column {name!r} is not on the view.")
        cell.set("width", str(w))

    if reorder is not None:
        current = _cell_names(lrow)
        if sorted(reorder) != sorted(current):
            raise D365Error(
                "--reorder must list exactly the current columns "
                f"({', '.join(current)}); got {', '.join(reorder)}.")
        by_name = {c.get("name"): c for c in lrow.findall("cell")}
        for c in lrow.findall("cell"):
            lrow.remove(c)
        for name in reorder:
            lrow.append(by_name[name])

    _assert_mismatch_invariant(lrow, fent, pk=pk)

    columns: dict[str, str] = {
        "layoutxml": serialize_xml(layout_root),
        "layoutjson": "",
    }
    if fetch_changed:
        columns["fetchxml"] = serialize_xml(fetch_root)

    result: dict[str, Any] = {
        "savedqueryid": sqid, "name": row.get("name", ""),
        "entity": entity, "action": "edit-columns",
        "columns": _cell_names(lrow),
    }

    def _verify(cols: dict[str, str]) -> None:
        lr = _layout_row(parse_xml(cols["layoutxml"], label="layoutxml"))
        fe = (_fetch_entity(parse_xml(cols["fetchxml"], label="fetchxml"))
              if cols.get("fetchxml") else fent)
        _assert_mismatch_invariant(lr, fe, pk=pk)

    return commit_xml_patches(
        backend, entity_set="savedqueries", record_id=str(sqid),
        columns=columns, result=result, dry_run_flag="would_update",
        publish=publish, solution=solution,
        read_back=_verify if publish else None)


def set_view_order(
    backend: D365Backend,
    *,
    entity: str,
    view: str,
    query_type: str = "public",
    order: "list[tuple[str, bool]] | None" = None,
    add_order: "list[tuple[str, bool]] | None" = None,
    clear_order: bool = False,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Set an existing view's sort order (fetchxml ``<order>`` elements).

    ``order`` replaces the current sort with ``[(attribute, descending)]``,
    ``add_order`` appends, and ``clear_order`` removes all sorting. Each order
    attribute is validated to exist. Only the entity's direct ``<order>``
    children are touched — ``<filter>`` / ``<condition>`` / ``<link-entity>``
    siblings are left intact.
    """
    order = order or []
    add_order = add_order or []
    if not (order or add_order or clear_order):
        raise D365Error(
            "nothing to do: pass --order, --add-order, or --clear-order.")

    row = _resolve_editable_view(backend, entity=entity, view=view,
                                 query_type=query_type)
    sqid = row.get("savedqueryid")
    fetchxml = row.get("fetchxml") or ""
    if not fetchxml:
        raise D365Error(f"view {view!r} has no fetchxml; cannot set order.")
    fetch_root = parse_xml(fetchxml, label="fetchxml")
    fent = _fetch_entity(fetch_root)

    if clear_order or order:
        new_orders: list[tuple[str, bool]] = list(order)
    else:
        new_orders = [
            (o.get("attribute") or "",
             (o.get("descending") or "").lower() == "true")
            for o in fent.findall("order")
        ]
    new_orders += list(add_order)

    for attr, _desc in new_orders:
        _require_attribute(backend, entity, attr)

    for o in fent.findall("order"):
        fent.remove(o)
    insert_at = _last_attribute_index(fent) + 1
    for i, (attr, desc) in enumerate(new_orders):
        el = ElementTree.Element("order")
        el.set("attribute", attr)
        el.set("descending", "true" if desc else "false")
        fent.insert(insert_at + i, el)

    result: dict[str, Any] = {
        "savedqueryid": sqid, "name": row.get("name", ""),
        "entity": entity, "action": "set-order",
        "order": [{"attribute": a, "descending": d} for a, d in new_orders],
    }

    def _verify(cols: dict[str, str]) -> None:
        fe = _fetch_entity(parse_xml(cols["fetchxml"], label="fetchxml"))
        got = [
            (o.get("attribute") or "",
             (o.get("descending") or "").lower() == "true")
            for o in fe.findall("order")
        ]
        if got != new_orders:
            raise D365Error(
                "read-back: the view sort order did not land as expected.")

    return commit_xml_patches(
        backend, entity_set="savedqueries", record_id=str(sqid),
        columns={"fetchxml": serialize_xml(fetch_root)},
        result=result, dry_run_flag="would_update",
        publish=publish, solution=solution,
        read_back=_verify if publish else None)
