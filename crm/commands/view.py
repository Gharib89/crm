"""View (savedquery) creation command."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import views as views_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error, _journal, _resolve_publish, _solution_option,
    _require_solution, _resolve_solution, _emit_with_warning,
)


@click.group("view")
def view_group():
    """Create and manage system views (savedquery)."""


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


@view_group.command("create")
@click.argument("entity")
@click.option("--name", required=True, help="View display name.")
@click.option("--otc", "object_type_code", type=int, required=True,
              help="Entity ObjectTypeCode (from `metadata entity <name>`).")
@click.option("--column", "columns", multiple=True, required=True,
              help="Repeatable 'logicalname[:width]'. Order preserved.")
@click.option("--order", "order_by", default=None, help="Attribute to sort by (ascending).")
@click.option("--filter-active", is_flag=True, help="Filter to statecode=0 (active) rows.")
@click.option("--default", "is_default", is_flag=True, help="Mark as the default view.")
@click.option("--if-exists", type=click.Choice(["error", "skip"]), default="error")
@_solution_option
@click.option("--publish/--no-publish", default=True,
              help="Run PublishAllXml after creation. Default: publish.")
@pass_ctx
def view_create(ctx: CLIContext, entity, name, object_type_code, columns,
                order_by, filter_active, is_default, if_exists,
                solution, require_solution, publish):
    """Create a public system view on ENTITY."""
    parsed = [_parse_column(c) for c in columns]
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    publish = _resolve_publish(ctx, publish)
    try:
        info = views_mod.create_view(
            ctx.backend(), entity=entity, object_type_code=object_type_code,
            name=name, columns=parsed, order_by=order_by,
            filter_active=filter_active, is_default=is_default,
            solution=solution, if_exists=if_exists, publish=publish,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning,
                       meta={"staged": True} if ctx.stage_only else None)
    _journal(ctx, "view create", name, info, solution=solution)
