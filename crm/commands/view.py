"""View (savedquery) creation command."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import views as views_mod
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _publish_option,
    d365_errors, _journal, _resolve_publish, _solution_option,
    _resolve_solution, _emit_with_warning,
)


@click.group("view")
def view_group():
    """Create and manage system views (savedquery)."""


@view_group.command("list")
@click.argument("entity")
@pass_ctx
def view_list(ctx: CLIContext, entity: str) -> None:
    """List the public views (savedquery) for an entity."""
    with d365_errors(ctx):
        views = views_mod.read_entity_views(ctx.backend(), entity)
    # Project to the list-oriented fields only — read_entity_views also returns
    # columns + order_by (parsed from layout/fetch xml), which would bloat
    # --json output and surprise consumers expecting list columns.
    listed = [
        {"name": v.get("name", ""), "savedqueryid": v.get("savedqueryid"),
         "isdefault": bool(v.get("is_default", False)),
         "querytype": v.get("querytype")}
        for v in views
    ]
    rows = [
        [r["name"], r["savedqueryid"] or "", str(r["isdefault"]),
         "" if r["querytype"] is None else str(r["querytype"])]
        for r in listed
    ]
    ctx.emit(True, data=listed, table={
        "headers": ["name", "savedqueryid", "isdefault", "querytype"],
        "rows": rows,
    })


def _parse_column(raw: str) -> tuple[str, int]:
    """Parse 'logicalname[:width]' (width optional, default 100)."""
    name, sep, w = raw.partition(":")
    name = name.strip()
    if not name:
        raise click.BadParameter(f"column name must not be empty: {raw!r}")
    if not sep:
        return name, 100
    try:
        width = int(w.strip())
    except ValueError:
        raise click.BadParameter(f"column width must be an int: {raw!r}")
    if width <= 0:
        raise click.BadParameter(f"column width must be positive: {raw!r}")
    return name, width


def _parse_order(raw: str) -> tuple[str, bool]:
    """Parse '<attribute> [asc|desc]' → (attribute, descending).

    Mirrors the OData `$orderby` idiom (`query odata --orderby`). Direction
    token is case-insensitive; default ascending. Anything else is a usage error.
    """
    parts = raw.split()
    if len(parts) == 1:
        return parts[0], False
    if len(parts) == 2:
        direction = parts[1].lower()
        if direction == "asc":
            return parts[0], False
        if direction == "desc":
            return parts[0], True
    raise click.UsageError(
        f"--order must be '<attribute>' or '<attribute> asc|desc': {raw!r}")


@view_group.command("create")
@click.argument("entity")
@click.option("--name", required=True, help="View display name.")
@click.option("--otc", "object_type_code", type=int, required=True,
              help="Entity ObjectTypeCode (from `metadata entity <name>`).")
@click.option("--column", "columns", multiple=True, required=True,
              help="Repeatable 'logicalname[:width]'. Order preserved.")
@click.option("--order", "order_by", default=None,
              help="Sort attribute, optional 'asc'/'desc' suffix "
                   "(e.g. 'createdon desc'). Default: ascending.")
@click.option("--filter-active", is_flag=True, help="Filter to statecode=0 (active) rows.")
@click.option("--default", "is_default", is_flag=True, help="Mark as the default view.")
@click.option("--if-exists", type=click.Choice(["error", "skip"]), default="error")
@click.option("--query-type", type=click.Choice(list(views_mod.QUERY_TYPES)),
              default="public", show_default=True,
              help="Saved-query type to create.")
@click.option("--description", default=None, help="View description.")
@_solution_option
@_publish_option
@pass_ctx
def view_create(ctx: CLIContext, entity, name, object_type_code, columns,
                order_by, filter_active, is_default, if_exists,
                query_type, description,
                solution, require_solution, publish):
    """Create a system view on ENTITY (public by default; see --query-type)."""
    parsed = [_parse_column(c) for c in columns]
    order_desc = False
    if order_by is not None:
        order_by, order_desc = _parse_order(order_by)
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = views_mod.create_view(
            ctx.backend(), entity=entity, object_type_code=object_type_code,
            name=name, columns=parsed, order_by=order_by, order_desc=order_desc,
            filter_active=filter_active, is_default=is_default,
            solution=solution, if_exists=if_exists, publish=publish,
            query_type=query_type, description=description,
        )
    _emit_with_warning(ctx, info, warning,
                       meta=ctx.staged_meta())
    _journal(ctx, name, info, solution=solution)
