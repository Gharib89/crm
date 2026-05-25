"""Query commands (OData, FetchXML, saved/user views)."""
# pyright: basic
from __future__ import annotations
from pathlib import Path
import click
from crm.core import query as query_mod
from crm.core.query import total_record_count
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error,
    _emit_query_result,
    _touch_session,
)


@click.group("query")
def query_group():
    """Run OData and FetchXML queries."""


@query_group.command("odata")
@click.argument("entity_set")
@click.option("--select", multiple=True)
@click.option("--filter", "filter_", help="OData $filter expression.")
@click.option("--top", type=int)
@click.option("--orderby")
@click.option("--expand", multiple=True)
@click.option("--count", is_flag=True, help="Also request $count.")
@click.option("--page-size", type=int)
@click.option("--annotations/--no-annotations", default=False)
@pass_ctx
def query_odata(ctx: CLIContext, entity_set, select, filter_, top, orderby, expand,
                count, page_size, annotations):
    """OData v4 query over an entity set."""
    try:
        result = query_mod.odata_query(
            ctx.backend(), entity_set,
            select=list(select) or None,
            filter_=filter_,
            top=top,
            orderby=orderby,
            expand=list(expand) or None,
            count=count,
            page_size=page_size,
            include_annotations=annotations,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_query_result(ctx, result, entity_set)
    _touch_session(ctx, entity_set, last_query={"type": "odata", "filter": filter_})


@query_group.command("fetchxml")
@click.argument("entity_set")
@click.option("--xml", "xml_inline", help="Inline FetchXML string.")
@click.option("--file", "xml_file", type=click.Path(exists=True, dir_okay=False),
              help="Path to a FetchXML file.")
@click.option("--annotations/--no-annotations", default=False)
@pass_ctx
def query_fetchxml(ctx: CLIContext, entity_set, xml_inline, xml_file, annotations):
    """Run a FetchXML query."""
    if xml_inline and xml_file:
        ctx.emit(False, error="Provide --xml or --file, not both.")
        return
    fetch_xml = xml_inline or (Path(xml_file).read_text(encoding="utf-8") if xml_file else None)
    if not fetch_xml:
        ctx.emit(False, error="Either --xml or --file is required.")
        return
    try:
        result = query_mod.fetchxml_query(
            ctx.backend(), entity_set, fetch_xml,
            include_annotations=annotations,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_query_result(ctx, result, entity_set)
    _touch_session(ctx, entity_set, last_query={"type": "fetchxml"})


@query_group.command("saved")
@click.argument("entity_set")
@click.argument("savedquery_id")
@click.option("--annotations/--no-annotations", default=True)
@click.option("--page-size", type=int)
@pass_ctx
def query_saved(ctx: CLIContext, entity_set, savedquery_id, annotations, page_size):
    """Execute a system view (savedquery) by GUID. Use `--json query odata savedqueries` to discover IDs."""
    try:
        result = query_mod.saved_query(
            ctx.backend(), entity_set, savedquery_id,
            include_annotations=annotations, page_size=page_size,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_query_result(ctx, result, entity_set)


@query_group.command("user")
@click.argument("entity_set")
@click.argument("userquery_id")
@click.option("--annotations/--no-annotations", default=True)
@click.option("--page-size", type=int)
@pass_ctx
def query_user(ctx: CLIContext, entity_set, userquery_id, annotations, page_size):
    """Execute a saved view (userquery) by GUID."""
    try:
        result = query_mod.user_query(
            ctx.backend(), entity_set, userquery_id,
            include_annotations=annotations, page_size=page_size,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_query_result(ctx, result, entity_set)


@query_group.command("count")
@click.argument("entity")
@pass_ctx
def query_count(ctx: CLIContext, entity: str):
    """Count rows for an entity via RetrieveTotalRecordCount (cached server-side)."""
    try:
        n = total_record_count(ctx.backend(), entity)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data={"entity": entity, "count": n})
