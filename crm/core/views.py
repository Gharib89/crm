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

from crm.core.metadata import attribute_info_or_raise, maybe_publish
from crm.core.xml_edit import commit_xml_patches, parse_xml, serialize_xml
from crm.utils import safe_xml
from crm.utils.d365_backend import (
    D365Backend,
    D365Error,
    as_dict,
    normalize_guid,
    odata_literal,
)

QUERY_TYPES: dict[str, int] = {
    "public": 0,
    "advanced-find": 1,
    "associated": 2,
    "quick-find": 4,
    "lookup": 64,
}
"""savedquery.querytype codes by CLI name.

See https://learn.microsoft.com/power-apps/developer/model-driven-apps/customize-entity-views#types-of-views
"""


def build_layoutxml(entity: str, object_type_code: int, columns: list[tuple[str, int]]) -> str:
    id_attr = f"{entity}id"
    cells = "".join(f'<cell name={quoteattr(name)} width="{width}" />' for name, width in columns)
    jump = columns[0][0]
    return (
        f'<grid name="resultset" object="{object_type_code}" '
        f'jump={quoteattr(jump)} select="1" icon="1" preview="1">'
        f'<row name="result" id={quoteattr(id_attr)}>{cells}</row></grid>'
    )


def build_fetchxml(
    entity: str,
    columns: list[tuple[str, int]],
    order_by: str | None,
    filter_active: bool,
    order_desc: bool = False,
) -> str:
    id_attr = f"{entity}id"
    attrs = f"<attribute name={quoteattr(id_attr)} />" + "".join(
        f"<attribute name={quoteattr(name)} />" for name, _ in columns
    )
    descending = "true" if order_desc else "false"
    order = (
        f'<order attribute={quoteattr(order_by)} descending="{descending}" />' if order_by else ""
    )
    filt = (
        '<filter type="and"><condition attribute="statecode" operator="eq" value="0" /></filter>'
        if filter_active
        else ""
    )
    return (
        '<fetch version="1.0" output-format="xml-platform" mapping="logical">'
        f"<entity name={quoteattr(entity)}>{attrs}{order}{filt}</entity></fetch>"
    )


def parse_layout_columns(layoutxml: str) -> list[dict[str, Any]]:
    """Parse a savedquery layoutxml grid into ``[{name[, width:int]}]`` column dicts.

    The inverse of :func:`build_layoutxml`. A cell with no parseable ``width``
    yields a name-only dict; an absent/unparseable layoutxml yields ``[]``.
    """
    columns: list[dict[str, Any]] = []
    if not layoutxml:
        return columns
    try:
        root = safe_xml.fromstring(layoutxml)
    except ElementTree.ParseError:
        return columns
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
    return columns


def parse_fetch_order_filter(fetchxml: str) -> tuple[str | None, bool, bool]:
    """Parse ``(order_by, order_desc, filter_active)`` from a savedquery fetchxml.

    The inverse of :func:`build_fetchxml`, scoped strictly to the ROOT ``<entity>``
    (its own ``<order>`` and direct-child ``<filter>``s): a ``<link-entity>``'s sort
    or statecode filter belongs to the joined table, not this view, so it must not
    surface as a main-view key. Returns ``(None, False, False)`` when fetchxml is
    absent/unparseable.

    Conditions are found with ``.//`` rather than ``Element.iter`` so the namespace
    wildcard applies; that also covers conditions nested in and/or sub-filters.
    """
    order_by: str | None = None
    order_desc = False
    filter_active = False
    if not fetchxml:
        return order_by, order_desc, filter_active
    try:
        fetch_root = safe_xml.fromstring(fetchxml)
    except ElementTree.ParseError:
        return order_by, order_desc, filter_active
    entity_el = fetch_root.find("{*}entity")
    if entity_el is not None:
        order_el = entity_el.find("{*}order")
        if order_el is not None:
            order_by = order_el.get("attribute") or None
            order_desc = (order_el.get("descending") or "").lower() == "true"
        for filt in entity_el.findall("{*}filter"):
            for cond in filt.findall(".//{*}condition"):
                if (
                    cond.get("attribute") == "statecode"
                    and cond.get("operator") == "eq"
                    and cond.get("value") == "0"
                ):
                    filter_active = True
                    break
            if filter_active:
                break
    return order_by, order_desc, filter_active


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

    savedqueries has no alternate key, so the existence check queries by name,
    entity and type.
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
            f"unknown query_type {query_type!r}; expected one of {', '.join(QUERY_TYPES)}."
        )
    querytype = QUERY_TYPES[query_type]

    existing = backend.get_collection(
        "savedqueries",
        params={
            "$filter": (
                f"name eq {odata_literal(name)} "
                f"and returnedtypecode eq {odata_literal(entity)} "
                f"and querytype eq {querytype}"
            ),
            "$select": "savedqueryid,name",
        },
    )
    if existing and not backend.dry_run:
        if if_exists == "error":
            raise D365Error(f"View {name!r} on {entity} already exists.", code="AlreadyExists")
        return {
            "skipped": True,
            "exists": True,
            "name": name,
            "savedqueryid": existing[0].get("savedqueryid"),
        }

    body: dict[str, Any] = {
        "name": name,
        "returnedtypecode": entity,
        "querytype": querytype,
        "isdefault": is_default,
        "layoutxml": build_layoutxml(entity, object_type_code, columns),
        "fetchxml": build_fetchxml(entity, columns, order_by, filter_active, order_desc),
    }
    if query_type == "quick-find":
        body["isquickfindquery"] = True
    if description is not None:
        body["description"] = description
    result = as_dict(backend.post("savedqueries", json_body=body, solution=solution))
    if result.get("_dry_run"):
        result["_exists"] = bool(existing)
        result["would_skip"] = bool(existing) and if_exists == "skip"
        return result

    entity_id_url = result.get("_entity_id_url") or ""
    sqid = result.get("_entity_id")
    out: dict[str, Any] = {
        "created": True,
        "name": name,
        "entity": entity,
        "savedqueryid": sqid,
        "solution": solution,
    }
    if sqid:
        try:
            rb = as_dict(
                backend.get(f"savedqueries({sqid})", params={"$select": "name,savedqueryid"})
            )
            out["name"] = rb.get("name", name)
        except D365Error as exc:
            out["view_lookup_error"] = f"Read-back failed: {exc}"
    else:
        out["view_lookup_error"] = f"Could not parse savedqueryid from response: {entity_id_url!r}"
    maybe_publish(backend, out, publish)
    return out


def update_view(
    backend: D365Backend,
    *,
    savedqueryid: str,
    changes: dict[str, Any],
    solution: str | None = None,
) -> dict[str, Any]:
    """Record-PATCH a savedquery's reconciled fields, in place.

    A thin write primitive for apply's view reconcile (ADR 0018): ``changes`` is the
    already-diffed set of savedquery columns to write — any of ``fetchxml`` /
    ``layoutxml`` / ``isdefault`` / ``description``. Under ``backend.dry_run`` no
    write is issued (the caller's field-level diff is the preview). Returns
    ``{updated|_dry_run, savedqueryid}``.
    """
    if not changes:
        raise D365Error("nothing to update on the view.")
    if backend.dry_run:
        return {"_dry_run": True, "would_update": True, "savedqueryid": savedqueryid}
    backend.patch(f"savedqueries({savedqueryid})", json_body=changes, solution=solution)
    return {"updated": True, "savedqueryid": savedqueryid}


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
    - ``order_desc``: bool — omitted unless the sort is descending
    - ``filter_active``: bool — omitted unless fetchxml carries the active-state
      (``statecode eq 0``) filter
    - ``is_default``: bool
    - ``description``: str — omitted unless the saved query has a non-empty
      description; emitted because apply reconciles it, so documentation
      metadata round-trips (#701)

    ``query_type`` is not projected: the read filters to ``querytype eq 0``
    (public views), so it is always the ``"public"`` default the apply adapter
    assumes.

    Callers that need apply-valid projections (e.g. ``build_entity_spec``) are
    responsible for dropping views with an empty ``name`` or empty ``columns``
    before inserting them into a spec.
    """
    rows = backend.get_collection(
        "savedqueries",
        params={
            "$filter": (
                f"returnedtypecode eq {odata_literal(entity_logical_name)} and querytype eq 0"
            ),
            "$select": "name,savedqueryid,querytype,layoutxml,fetchxml,isdefault,description",
        },
    )

    result: list[dict[str, Any]] = []
    for row in rows:
        columns = parse_layout_columns(row.get("layoutxml") or "")
        order_by, order_desc, filter_active = parse_fetch_order_filter(row.get("fetchxml") or "")

        view: dict[str, Any] = {
            "name": row.get("name", ""),
            "savedqueryid": row.get("savedqueryid"),
            "querytype": row.get("querytype"),
            "columns": columns,
            "is_default": bool(row.get("isdefault", False)),
        }
        description = row.get("description")
        if isinstance(description, str) and description:
            view["description"] = description
        if order_by is not None:
            view["order_by"] = order_by
        if order_desc:
            view["order_desc"] = True
        if filter_active:
            view["filter_active"] = True

        result.append(view)

    return result


_EDITABLE_QUERYTYPES = frozenset(QUERY_TYPES.values())
"""The savedquery types whose layoutxml is a column-editable grid.

System query types with no grid layout (such as 8192, offline filters) are refused.
"""

_VIEW_EDIT_SELECT = (
    "savedqueryid,name,returnedtypecode,querytype,layoutxml,fetchxml,layoutjson,iscustomizable"
)


def _require_customizable(row: dict[str, Any]) -> None:
    """Refuse to PATCH a view whose ``IsCustomizable`` managed property is false.

    ``iscustomizable`` is a ``BooleanManagedProperty`` (``{"Value": bool, ...}``);
    only an explicit ``False`` blocks the edit — a missing property can't be
    judged, and the server is the final authority.
    """
    ic = row.get("iscustomizable")
    value: Any = cast("dict[str, Any]", ic).get("Value") if isinstance(ic, dict) else ic
    if value is False:
        raise D365Error(
            f"View {row.get('name')!r} is not customizable "
            "(IsCustomizable.Value is false); refusing to PATCH.",
            code="NotCustomizable",
        )


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
            f"unknown query_type {query_type!r}; expected one of {', '.join(QUERY_TYPES)}."
        )
    querytype = QUERY_TYPES[query_type]
    vid = normalize_guid(view)
    if vid is not None:
        row = as_dict(backend.get(f"savedqueries({vid})", params={"$select": _VIEW_EDIT_SELECT}))
        if not row.get("savedqueryid"):
            raise D365Error(f"No savedquery with id {view!r}.", code="NotFound")
        rtc = row.get("returnedtypecode")
        if rtc and rtc != entity:
            raise D365Error(
                f"savedquery {view!r} is on {rtc!r}, not {entity!r}.", code="EntityMismatch"
            )
    else:
        rows = backend.get_collection(
            "savedqueries",
            params={
                "$filter": (
                    f"name eq {odata_literal(view)} "
                    f"and returnedtypecode eq {odata_literal(entity)} "
                    f"and querytype eq {querytype}"
                ),
                "$select": _VIEW_EDIT_SELECT,
            },
        )
        if not rows:
            raise D365Error(f"No {query_type} view named {view!r} on {entity}.", code="NotFound")
        if len(rows) > 1:
            raise D365Error(
                f"{len(rows)} views named {view!r} on {entity} "
                f"(querytype {querytype}); resolve by savedqueryid instead.",
                code="Ambiguous",
            )
        row = rows[0]
    qt = row.get("querytype")
    if qt not in _EDITABLE_QUERYTYPES:
        raise D365Error(
            f"savedquery querytype {qt} has no editable grid layout; "
            f"edit-columns / set-order support querytypes "
            f"{sorted(_EDITABLE_QUERYTYPES)}.",
            code="NotEditable",
        )
    _require_customizable(row)
    return row


def _layout_row(root: ElementTree.Element) -> ElementTree.Element:
    row = root.find(".//row")
    if row is None:
        raise D365Error("layoutxml has no <row>; cannot edit columns.")
    return row


def _fetch_entity(root: ElementTree.Element) -> ElementTree.Element:
    ent = root.find("entity")
    if ent is None:
        raise D365Error("fetchxml has no <entity>; cannot edit.")
    return ent


def _cell_names(row: ElementTree.Element) -> list[str]:
    return [c.get("name") or "" for c in row.findall("cell")]


def _attr_names(entity: ElementTree.Element) -> list[str]:
    return [a.get("name") or "" for a in entity.findall("attribute")]


def _find_cell(row: ElementTree.Element, name: str) -> ElementTree.Element | None:
    for c in row.findall("cell"):
        if c.get("name") == name:
            return c
    return None


def _find_attr(entity: ElementTree.Element, name: str) -> ElementTree.Element | None:
    for a in entity.findall("attribute"):
        if a.get("name") == name:
            return a
    return None


def _last_attribute_index(entity: ElementTree.Element) -> int:
    """Return the index of the last ``<attribute>`` child, or -1.

    A new attribute goes after the last one, not at the end: an attribute placed
    after the ``<order>`` or ``<filter>`` siblings is invalid or ignored FetchXML.
    """
    last = -1
    for i, child in enumerate(list(entity)):
        if child.tag == "attribute":
            last = i
    return last


def _assert_mismatch_invariant(
    layout_row: ElementTree.Element,
    fetch_entity: ElementTree.Element,
    *,
    pk: str,
) -> None:
    """Every non-PK layout ``<cell name>`` must have a fetch ``<attribute name>``.

    A column with no backing fetch attribute renders empty, so the layout and
    fetch documents must stay coupled. Dotted/aliased cell names (linked-entity
    columns) are left to the server — this tool only adds/removes simple columns.
    The PK attribute must always remain in the fetch.
    """
    attrs = set(_attr_names(fetch_entity))
    if pk not in attrs:
        raise D365Error(f"fetchxml is missing the primary-key attribute {pk!r}.")
    for name in _cell_names(layout_row):
        if name and name != pk and "." not in name and name not in attrs:
            raise D365Error(
                f"column {name!r} has no matching fetch <attribute>; "
                "the layout and fetch would be out of sync."
            )


def edit_view_columns(
    backend: D365Backend,
    *,
    entity: str,
    view: str,
    query_type: str = "public",
    add: list[tuple[str, int]] | None = None,
    remove: list[str] | None = None,
    width: list[tuple[str, int]] | None = None,
    reorder: list[str] | None = None,
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Edit an existing view's grid columns (layoutxml + fetchxml), in place.

    ``add`` is ``[(logicalname, width)]`` (adds both the layout cell and the
    fetch attribute), ``remove`` drops both, ``width`` resizes existing cells,
    and ``reorder`` is a permutation of the current column names. ``reorder`` is
    exclusive of the other operations. Each added column is validated to exist,
    width must be > 0, and the primary-key column cannot be removed.

    The primary key comes from the layout ``<row id>``, which is authoritative
    because a few system tables do not follow the ``<entity>id`` convention.
    layoutjson is cleared on every edit so the platform rebuilds it from the new
    layoutxml; a stale layoutjson otherwise drives the modern read-only grid with
    the old columns.
    """
    add = add or []
    remove = remove or []
    width = width or []
    has_other = bool(add or remove or width)
    if reorder is not None and has_other:
        raise D365Error("--reorder cannot be combined with --add / --remove / --width.")
    if reorder is None and not has_other:
        raise D365Error("nothing to do: pass --add, --remove, --width, or --reorder.")

    row = _resolve_editable_view(backend, entity=entity, view=view, query_type=query_type)
    sqid = row.get("savedqueryid")
    layoutxml = row.get("layoutxml") or ""
    fetchxml = row.get("fetchxml") or ""
    if not layoutxml:
        raise D365Error(f"view {view!r} has no layoutxml; it is not column-editable.")
    if not fetchxml:
        raise D365Error(f"view {view!r} has no fetchxml; cannot edit columns.")

    layout_root = parse_xml(layoutxml, label="layoutxml")
    fetch_root = parse_xml(fetchxml, label="fetchxml")
    lrow = _layout_row(layout_root)
    fent = _fetch_entity(fetch_root)
    pk = lrow.get("id") or f"{entity}id"
    fetch_changed = False

    existing = _cell_names(lrow)
    for name, w in add:
        if w <= 0:
            raise D365Error(f"column width must be positive: {name!r}={w}.")
        if name in existing:
            raise D365Error(f"column {name!r} is already on the view.")
        attribute_info_or_raise(backend, entity, name)
        cell = ElementTree.SubElement(lrow, "cell")
        cell.set("name", name)
        cell.set("width", str(w))
        existing.append(name)
        if _find_attr(fent, name) is None:
            attr = ElementTree.Element("attribute")
            attr.set("name", name)
            fent.insert(_last_attribute_index(fent) + 1, attr)
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
                f"({', '.join(current)}); got {', '.join(reorder)}."
            )
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
        "savedqueryid": sqid,
        "name": row.get("name", ""),
        "entity": entity,
        "action": "edit-columns",
        "columns": _cell_names(lrow),
    }

    def _verify(cols: dict[str, str]) -> None:
        """Check the mismatch invariant on the read-back.

        The read-back returns only the PATCHed columns. A width or reorder edit
        leaves the fetch untouched and absent from it, and the local fetch then
        equals the server's, so checking against the local one checks the server.
        """
        lr = _layout_row(parse_xml(cols["layoutxml"], label="layoutxml"))
        fe = (
            _fetch_entity(parse_xml(cols["fetchxml"], label="fetchxml"))
            if cols.get("fetchxml")
            else fent
        )
        _assert_mismatch_invariant(lr, fe, pk=pk)

    return commit_xml_patches(
        backend,
        entity_set="savedqueries",
        record_id=str(sqid),
        columns=columns,
        result=result,
        dry_run_flag="would_update",
        publish=publish,
        solution=solution,
        read_back=_verify if publish else None,
    )


_NO_VALUE_OPS = frozenset(
    {
        "null",
        "not-null",
        "today",
        "tomorrow",
        "yesterday",
        "last-seven-days",
        "next-seven-days",
        "last-week",
        "this-week",
        "next-week",
        "last-month",
        "this-month",
        "next-month",
        "last-year",
        "this-year",
        "next-year",
        "last-fiscal-period",
        "this-fiscal-period",
        "next-fiscal-period",
        "last-fiscal-year",
        "this-fiscal-year",
        "next-fiscal-year",
        "eq-userid",
        "ne-userid",
        "eq-businessid",
        "ne-businessid",
        "eq-userlanguage",
        "eq-useroruserhierarchy",
        "eq-useroruserhierarchyandteams",
        "eq-useroruserteams",
        "eq-userteams",
    }
)

_TWO_VALUE_OPS = frozenset(
    {
        "between",
        "not-between",
        "in-fiscal-period-and-year",
        "in-or-after-fiscal-period-and-year",
        "in-or-before-fiscal-period-and-year",
    }
)

_MULTI_VALUE_OPS = frozenset(
    {
        "in",
        "not-in",
        "contain-values",
        "not-contain-values",
    }
)

_SINGLE_VALUE_OPS = frozenset(
    {
        "eq",
        "ne",
        "neq",
        "lt",
        "le",
        "gt",
        "ge",
        "like",
        "not-like",
        "begins-with",
        "not-begin-with",
        "ends-with",
        "not-end-with",
        "on",
        "on-or-after",
        "on-or-before",
        "above",
        "eq-or-above",
        "under",
        "eq-or-under",
        "not-under",
        "in-fiscal-period",
        "in-fiscal-year",
        "last-x-days",
        "last-x-weeks",
        "last-x-months",
        "last-x-years",
        "last-x-hours",
        "last-x-fiscal-periods",
        "last-x-fiscal-years",
        "next-x-days",
        "next-x-weeks",
        "next-x-months",
        "next-x-years",
        "next-x-hours",
        "next-x-fiscal-periods",
        "next-x-fiscal-years",
        "olderthan-x-days",
        "olderthan-x-weeks",
        "olderthan-x-months",
        "olderthan-x-years",
        "olderthan-x-hours",
        "olderthan-x-minutes",
    }
)
"""Operators carrying one value in the ``value`` attribute.

``neq`` is the deprecated alias of ``ne``; the fetch.xsd enum still lists it
("remains for backward compatability only"), so it is accepted.
"""

_FETCH_OPERATORS = _NO_VALUE_OPS | _TWO_VALUE_OPS | _MULTI_VALUE_OPS | _SINGLE_VALUE_OPS
"""The fetch.xsd ConditionOperator enum, split by value cardinality.

The tables are transcribed from the FetchXML "condition operator values" reference
(learn.microsoft.com/power-apps/developer/data-platform/fetchxml/reference/operators,
retrieved 2026-06). Some operators are version-gated server-side (on v9.1, for
one): the filter editors validate only the enum and the cardinality, and leave any
further rejection to the server.
"""


def _validate_operator(op: str) -> None:
    if op not in _FETCH_OPERATORS:
        raise D365Error(
            f"unknown FetchXML operator {op!r}; see the fetch.xsd condition-operator enum."
        )


def _coerce_values(op: str, tokens: list[str]) -> list[str]:
    """Interpret the raw value tokens for an operator, enforcing its cardinality.

    A single-value operator joins its tokens into one value (so a value may
    contain spaces); multi/two-value operators keep each whitespace token as a
    separate value; no-value operators must be given none.
    """
    if op in _NO_VALUE_OPS:
        if tokens:
            raise D365Error(f"operator {op!r} takes no value; got {tokens}.")
        return []
    if op in _TWO_VALUE_OPS:
        if len(tokens) != 2:
            raise D365Error(f"operator {op!r} requires exactly two values; got {len(tokens)}.")
        return list(tokens)
    if op in _MULTI_VALUE_OPS:
        if not tokens:
            raise D365Error(f"operator {op!r} requires at least one value.")
        return list(tokens)
    if not tokens:
        raise D365Error(f"operator {op!r} requires a value.")
    return [" ".join(tokens)]


def _build_condition(attr: str, op: str, values: list[str]) -> ElementTree.Element:
    """Build a `<condition>` element from a validated (attr, op, values) spec."""
    cond = ElementTree.Element("condition")
    cond.set("attribute", attr)
    cond.set("operator", op)
    if not values:
        return cond
    if op in _MULTI_VALUE_OPS or op in _TWO_VALUE_OPS:
        for v in values:
            ve = ElementTree.SubElement(cond, "value")
            ve.text = v
    else:
        cond.set("value", values[0])
    return cond


def _condition_values(cond: ElementTree.Element) -> list[str]:
    """The values on a `<condition>` — its `value` attribute or child elements."""
    v = cond.get("value")
    if v is not None:
        return [v]
    return [(ve.text or "") for ve in cond.findall("value")]


def _entity_filter_of_type(
    entity: ElementTree.Element, filter_type: str
) -> ElementTree.Element | None:
    """The entity's direct-child `<filter>` of the given type (default 'and')."""
    for f in entity.findall("filter"):
        if (f.get("type") or "and") == filter_type:
            return f
    return None


def _filter_insert_index(entity: ElementTree.Element) -> int:
    """Index to insert a new `<filter>`: after the last attribute/order/filter,
    which keeps it before any `<link-entity>` (link-entities follow filters).
    """
    idx = 0
    for i, child in enumerate(list(entity)):
        if child.tag in ("attribute", "all-attributes", "order", "filter"):
            idx = i + 1
    return idx


def _iter_entity_conditions(
    entity: ElementTree.Element,
) -> list[tuple[ElementTree.Element, ElementTree.Element, ElementTree.Element]]:
    """All `(parent, filter, condition)` reachable from the entity without
    descending into `<link-entity>` — so link-entity filters stay untouched.

    Recurses through nested `<filter>` elements. ``parent`` is the filter's own
    parent element (needed to prune a filter left empty by a removal).
    """
    found: list[tuple[ElementTree.Element, ElementTree.Element, ElementTree.Element]] = []

    def walk(el: ElementTree.Element) -> None:
        for child in list(el):
            if child.tag == "link-entity":
                continue
            if child.tag == "filter":
                for cond in child.findall("condition"):
                    found.append((el, child, cond))
                walk(child)

    walk(entity)
    return found


def _prune_empty_filters(entity: ElementTree.Element, emptied: list[ElementTree.Element]) -> None:
    """Remove each filter in ``emptied`` that has no conditions and no child
    filters, cascading to any ancestor filter the prune leaves empty.

    Only filters reachable from ``emptied`` (the ones a removal touched) and the
    ancestors they empty are pruned — a pre-existing empty filter elsewhere is
    left alone. Dedupe by identity so a filter listed twice isn't removed twice.
    """
    parent_of: dict[int, ElementTree.Element] = {}
    by_id: dict[int, ElementTree.Element] = {}

    def walk(el: ElementTree.Element) -> None:
        for child in list(el):
            if child.tag == "link-entity":
                continue
            if child.tag == "filter":
                parent_of[id(child)] = el
                by_id[id(child)] = child
                walk(child)

    walk(entity)
    queue = [id(f) for f in emptied]
    pruned: set[int] = set()
    while queue:
        fid = queue.pop()
        if fid in pruned:
            continue
        filt = by_id.get(fid)
        parent = parent_of.get(fid)
        if filt is None or parent is None:
            continue
        if filt.findall("condition") or filt.findall("filter"):
            continue
        parent.remove(filt)
        pruned.add(fid)
        if parent.tag == "filter":
            queue.append(id(parent))


def add_view_filter(
    backend: D365Backend,
    *,
    entity: str,
    view: str,
    query_type: str = "public",
    conditions: list[tuple[str, str, list[str]]],
    filter_type: str = "and",
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Add FetchXML `<condition>` filters to an existing view, in place.

    Each ``conditions`` entry is ``(attribute, operator, value_tokens)``. The
    attribute is validated to exist, the operator against the fetch.xsd enum, and
    the token count against the operator's cardinality. New conditions are
    appended to the entity-level ``<filter type=filter_type>`` (created if absent);
    ``filter_type`` is ``and`` or ``or``. Existing conditions and link-entity
    filters are left intact. Each condition is validated (operator and
    cardinality) before its attribute metadata GET, so an invalid one fails with
    no wasted round trip.
    """
    if not conditions:
        raise D365Error("nothing to do: pass at least one --condition.")
    if filter_type not in ("and", "or"):
        raise D365Error(f"filter type must be 'and' or 'or'; got {filter_type!r}.")

    row = _resolve_editable_view(backend, entity=entity, view=view, query_type=query_type)
    sqid = row.get("savedqueryid")
    fetchxml = row.get("fetchxml") or ""
    if not fetchxml:
        raise D365Error(f"view {view!r} has no fetchxml; cannot add a filter.")
    fetch_root = parse_xml(fetchxml, label="fetchxml")
    fent = _fetch_entity(fetch_root)

    added: list[dict[str, Any]] = []
    for attr, op, tokens in conditions:
        _validate_operator(op)
        values = _coerce_values(op, tokens)
        attribute_info_or_raise(backend, entity, attr)
        target = _entity_filter_of_type(fent, filter_type)
        if target is None:
            target = ElementTree.Element("filter")
            target.set("type", filter_type)
            fent.insert(_filter_insert_index(fent), target)
        target.append(_build_condition(attr, op, values))
        added.append({"attribute": attr, "operator": op, "values": values})

    result: dict[str, Any] = {
        "savedqueryid": sqid,
        "name": row.get("name", ""),
        "entity": entity,
        "action": "add-filter",
        "filter_type": filter_type,
        "conditions": added,
    }

    def _verify(cols: dict[str, str]) -> None:
        fe = _fetch_entity(parse_xml(cols["fetchxml"], label="fetchxml"))
        present = _iter_entity_conditions(fe)
        for spec in added:
            if not any(
                c.get("attribute") == spec["attribute"]
                and c.get("operator") == spec["operator"]
                and _condition_values(c) == spec["values"]
                for _p, _f, c in present
            ):
                raise D365Error(
                    f"read-back: condition {spec['attribute']} {spec['operator']} did not land."
                )

    return commit_xml_patches(
        backend,
        entity_set="savedqueries",
        record_id=str(sqid),
        columns={"fetchxml": serialize_xml(fetch_root)},
        result=result,
        dry_run_flag="would_update",
        publish=publish,
        solution=solution,
        read_back=_verify if publish else None,
    )


def remove_view_filter(
    backend: D365Backend,
    *,
    entity: str,
    view: str,
    query_type: str = "public",
    conditions: list[tuple[str, str, list[str]]],
    publish: bool = False,
    solution: str | None = None,
) -> dict[str, Any]:
    """Remove FetchXML `<condition>` filters from an existing view, in place.

    Each ``conditions`` entry is ``(attribute, operator, value_tokens)``; a
    condition matches on attribute + operator, and on its values too when value
    tokens are given (use them to disambiguate). A spec matching no condition, or
    more than one, is an error (add a value to disambiguate). A filter left with
    no conditions or nested filters is removed. link-entity filters are never
    searched, and the attribute is not required to still exist (so a filter on a
    deleted column can be cleaned up). Candidates are re-derived for each spec,
    because an earlier spec may have removed elements: a list built once would
    hold stale elements and could remove a condition twice.
    """
    if not conditions:
        raise D365Error("nothing to do: pass at least one --condition.")

    row = _resolve_editable_view(backend, entity=entity, view=view, query_type=query_type)
    sqid = row.get("savedqueryid")
    fetchxml = row.get("fetchxml") or ""
    if not fetchxml:
        raise D365Error(f"view {view!r} has no fetchxml; cannot remove a filter.")
    fetch_root = parse_xml(fetchxml, label="fetchxml")
    fent = _fetch_entity(fetch_root)

    removed: list[dict[str, Any]] = []
    touched_filters: list[tuple[ElementTree.Element, ElementTree.Element]] = []
    for attr, op, tokens in conditions:
        _validate_operator(op)
        want_values = _coerce_values(op, tokens) if tokens else None
        matches = [
            (parent, filt, cond)
            for parent, filt, cond in _iter_entity_conditions(fent)
            if cond.get("attribute") == attr
            and cond.get("operator") == op
            and (want_values is None or _condition_values(cond) == want_values)
        ]
        if not matches:
            raise D365Error(
                f"no condition matches {attr} {op}"
                + (f" {' '.join(tokens)}" if tokens else "")
                + ".",
                code="NotFound",
            )
        if len(matches) > 1:
            raise D365Error(
                f"{len(matches)} conditions match {attr} {op}; specify the value to disambiguate.",
                code="Ambiguous",
            )
        parent, filt, cond = matches[0]
        filt.remove(cond)
        touched_filters.append((parent, filt))
        removed.append({"attribute": attr, "operator": op, "values": want_values or []})

    _prune_empty_filters(fent, [f for _p, f in touched_filters])

    result: dict[str, Any] = {
        "savedqueryid": sqid,
        "name": row.get("name", ""),
        "entity": entity,
        "action": "remove-filter",
        "conditions": removed,
    }

    def _verify(cols: dict[str, str]) -> None:
        fe = _fetch_entity(parse_xml(cols["fetchxml"], label="fetchxml"))
        present = _iter_entity_conditions(fe)
        for spec in removed:
            if any(
                c.get("attribute") == spec["attribute"]
                and c.get("operator") == spec["operator"]
                and (not spec["values"] or _condition_values(c) == spec["values"])
                for _p, _f, c in present
            ):
                raise D365Error(
                    f"read-back: condition {spec['attribute']} {spec['operator']} is still present."
                )

    return commit_xml_patches(
        backend,
        entity_set="savedqueries",
        record_id=str(sqid),
        columns={"fetchxml": serialize_xml(fetch_root)},
        result=result,
        dry_run_flag="would_update",
        publish=publish,
        solution=solution,
        read_back=_verify if publish else None,
    )


def set_view_order(
    backend: D365Backend,
    *,
    entity: str,
    view: str,
    query_type: str = "public",
    order: list[tuple[str, bool]] | None = None,
    add_order: list[tuple[str, bool]] | None = None,
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
        raise D365Error("nothing to do: pass --order, --add-order, or --clear-order.")

    row = _resolve_editable_view(backend, entity=entity, view=view, query_type=query_type)
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
            (o.get("attribute") or "", (o.get("descending") or "").lower() == "true")
            for o in fent.findall("order")
        ]
    new_orders += list(add_order)

    for attr, _desc in new_orders:
        attribute_info_or_raise(backend, entity, attr)

    for o in fent.findall("order"):
        fent.remove(o)
    insert_at = _last_attribute_index(fent) + 1
    for i, (attr, desc) in enumerate(new_orders):
        el = ElementTree.Element("order")
        el.set("attribute", attr)
        el.set("descending", "true" if desc else "false")
        fent.insert(insert_at + i, el)

    result: dict[str, Any] = {
        "savedqueryid": sqid,
        "name": row.get("name", ""),
        "entity": entity,
        "action": "set-order",
        "order": [{"attribute": a, "descending": d} for a, d in new_orders],
    }

    def _verify(cols: dict[str, str]) -> None:
        fe = _fetch_entity(parse_xml(cols["fetchxml"], label="fetchxml"))
        got = [
            (o.get("attribute") or "", (o.get("descending") or "").lower() == "true")
            for o in fe.findall("order")
        ]
        if got != new_orders:
            raise D365Error("read-back: the view sort order did not land as expected.")

    return commit_xml_patches(
        backend,
        entity_set="savedqueries",
        record_id=str(sqid),
        columns={"fetchxml": serialize_xml(fetch_root)},
        result=result,
        dry_run_flag="would_update",
        publish=publish,
        solution=solution,
        read_back=_verify if publish else None,
    )
