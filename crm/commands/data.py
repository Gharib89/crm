"""Data bulk export commands."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import export as export_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import _handle_d365_error


@click.group("data")
def data_group():
    """Bulk CSV/JSON dataset export."""


@data_group.command("export")
@click.argument("entity_set")
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False))
@click.option("--select", multiple=True)
@click.option("--filter", "filter_", help="OData $filter.")
@click.option("--page-size", type=int, default=500)
@click.option("--max-records", type=int, default=None)
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]))
@pass_ctx
def data_export(ctx: CLIContext, entity_set, output, select, filter_, page_size, max_records, fmt):
    select_list: list[str] = []
    for s in select:
        select_list.extend(part.strip() for part in s.split(",") if part.strip())
    try:
        info = export_mod.export_records(
            ctx.backend(), entity_set, output,
            select=select_list or None,
            filter_=filter_,
            page_size=page_size,
            max_records=max_records,
            fmt=fmt,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
