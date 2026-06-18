"""Query commands (OData, FetchXML, saved/user views)."""
# pyright: basic
from __future__ import annotations
import xml.etree.ElementTree as ET
from pathlib import Path
import click
from crm.core import query as query_mod
from crm.core.query import total_record_count
from crm.core.entity_names import resolve_logical_name
from crm.core.metadata import resolve_entity_set_name
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    d365_errors,
    _emit_query_result,
    _touch_session,
)


def _parse_entity_name_from_fetchxml(fetch_xml: str) -> str:
    """Extract the logical entity name from a FetchXML string.

    Parses the top-level <entity name="..."> attribute. Raises click.UsageError
    (exit 2) if the XML is unparseable or the name attribute is absent — the
    caller should pass ENTITY_SET explicitly instead.
    """
    try:
        root = ET.fromstring(fetch_xml)
    except ET.ParseError as exc:
        raise click.UsageError(
            f"Could not parse FetchXML: {exc}. "
            "Pass ENTITY_SET explicitly or fix the XML."
        )
    entity_el = root.find("entity")
    if entity_el is None:
        raise click.UsageError(
            "FetchXML has no <entity> element. "
            "Pass ENTITY_SET explicitly or add <entity name=\"...\"> to the XML."
        )
    name = entity_el.get("name", "").strip()
    if not name:
        raise click.UsageError(
            "FetchXML <entity> is missing the name= attribute. "
            "Pass ENTITY_SET explicitly or add name=\"<logical-name>\" to <entity>."
        )
    return name


@click.group("query")
def query_group():
    """Run OData and FetchXML queries."""


@query_group.command("odata")
@click.argument("entity_set", metavar="ENTITY_SET|BOUND_FUNC|METADATA_PATH")
@click.option("--select", multiple=True)
@click.option("--filter", "filter_", help="OData $filter expression.")
@click.option("--top", type=int)
@click.option("--orderby")
@click.option("--expand", multiple=True)
@click.option("--count", is_flag=True, help="Also request $count.")
@click.option("--page-size", type=int)
@click.option("--all", "all_pages", is_flag=True, default=False,
              help="Follow @odata.nextLink across all pages and merge the results "
                   "into one array (default: a single server page).")
@click.option("--max-records", type=int,
              help="Cap the total rows returned, following @odata.nextLink only as "
                   "far as needed. Implies page-following; bounds --all when both given.")
@click.option("--annotations/--no-annotations", default=True, help="Include formatted values.")
@click.option("--track-changes", is_flag=True, default=False,
              help="Request a change-tracking delta link (Prefer: odata.track-changes): "
                   "returns the current rows plus meta.delta_token/meta.delta_link to "
                   "resume from later. Rejects --filter/--orderby/--expand/--top/--all.")
@click.option("--delta-token",
              help="Resume change tracking from a prior meta.delta_token: returns only "
                   "rows created/updated/deleted since (deletes carry reason=\"deleted\").")
@click.option("--minimal", is_flag=True, default=False,
              help="JSON mode: drop every record key containing '@' (OData annotations "
                   "like @odata.etag, *@FormattedValue, *@lookuplogicalname); keeps "
                   "business fields, _*_value lookup GUIDs, and the primary id.")
@pass_ctx
def query_odata(ctx: CLIContext, entity_set, select, filter_, top, orderby, expand,
                count, page_size, all_pages, max_records, annotations,
                track_changes, delta_token, minimal):
    """OData v4 GET — entity set, bound-function path, or metadata path.

    \b
    Accepted forms:
      contacts                                         bare entity set
      RetrieveAppComponents(AppModuleId=<guid>)        bound-function path
      EntityDefinitions(LogicalName='account')/Keys    metadata path

    OData query options go through --select/--filter/etc., never inline.
    A '?' or '$' in the positional arg is rejected client-side before the request.
    """
    with d365_errors(ctx):
        result = query_mod.odata_query(
            ctx.backend(), entity_set,
            select=list(select) or None,
            filter_=filter_,
            top=top,
            orderby=orderby,
            expand=list(expand) or None,
            count=count,
            page_size=page_size,
            all_pages=all_pages,
            max_records=max_records,
            include_annotations=annotations,
            track_changes=track_changes,
            delta_token=delta_token,
        )
    # Surface the cap-hit signal in meta and drop the internal marker before render.
    extra_meta = {"truncated": True} if result.pop("@crm.truncated", False) else None
    _emit_query_result(ctx, result, entity_set, minimal=minimal, extra_meta=extra_meta)
    _touch_session(ctx, entity_set, last_query={"type": "odata", "filter": filter_})


@query_group.command("fetchxml")
@click.argument("entity_set", required=False, default=None)
@click.option("--xml", "xml_inline", help="Inline FetchXML string.")
@click.option("--file", "xml_file", type=click.Path(exists=True, dir_okay=False),
              help="Path to a FetchXML file.")
@click.option("--annotations/--no-annotations", default=True, help="Include formatted values.")
@click.option("--minimal", is_flag=True, default=False,
              help="JSON mode: drop every record key containing '@' (OData annotations "
                   "like @odata.etag, *@FormattedValue, *@lookuplogicalname); keeps "
                   "business fields, _*_value lookup GUIDs, and the primary id.")
@pass_ctx
def query_fetchxml(ctx: CLIContext, entity_set, xml_inline, xml_file, annotations, minimal):
    """Run a FetchXML query.

    \b
    ENTITY_SET is the OData entity-set name (e.g. "accounts"). When omitted,
    the logical entity name is parsed from the <entity name="..."> attribute of
    the FetchXML and resolved to the entity-set name via one metadata GET
    (EntityDefinitions). Pass ENTITY_SET explicitly to skip that resolution call.

    \b
    Examples:
      crm --json query fetchxml --xml '<fetch><entity name="account">...</entity></fetch>'
      crm --json query fetchxml accounts --xml '<fetch>...</fetch>'
    """
    if xml_inline and xml_file:
        ctx.emit(False, error="Provide --xml or --file, not both.")
        return
    fetch_xml = xml_inline or (Path(xml_file).read_text(encoding="utf-8") if xml_file else None)
    if not fetch_xml:
        ctx.emit(False, error="Either --xml or --file is required.")
        return

    # Derive entity_set from the FetchXML when the positional is omitted.
    # Parse raises click.UsageError (exit 2) for bad XML — escapes the D365Error try below.
    if entity_set is None:
        logical_name = _parse_entity_name_from_fetchxml(fetch_xml)
        with d365_errors(ctx):
            entity_set = resolve_entity_set_name(ctx.backend(), logical_name)

    with d365_errors(ctx):
        result = query_mod.fetchxml_query(
            ctx.backend(), entity_set, fetch_xml,
            include_annotations=annotations,
        )
    _emit_query_result(ctx, result, entity_set, minimal=minimal)
    _touch_session(ctx, entity_set, last_query={"type": "fetchxml"})


@query_group.command("saved")
@click.argument("entity_set")
@click.argument("savedquery_id")
@click.option("--annotations/--no-annotations", default=True, help="Include formatted values.")
@click.option("--page-size", type=int)
@click.option("--minimal", is_flag=True, default=False,
              help="JSON mode: drop every record key containing '@' (OData annotations "
                   "like @odata.etag, *@FormattedValue, *@lookuplogicalname); keeps "
                   "business fields, _*_value lookup GUIDs, and the primary id.")
@pass_ctx
def query_saved(ctx: CLIContext, entity_set, savedquery_id, annotations, page_size, minimal):
    """Execute a system view (savedquery) by GUID. Use `--json query odata savedqueries` to discover IDs."""
    with d365_errors(ctx):
        result = query_mod.saved_query(
            ctx.backend(), entity_set, savedquery_id,
            include_annotations=annotations, page_size=page_size,
        )
    _emit_query_result(ctx, result, entity_set, minimal=minimal)


@query_group.command("user")
@click.argument("entity_set")
@click.argument("userquery_id")
@click.option("--annotations/--no-annotations", default=True, help="Include formatted values.")
@click.option("--page-size", type=int)
@click.option("--minimal", is_flag=True, default=False,
              help="JSON mode: drop every record key containing '@' (OData annotations "
                   "like @odata.etag, *@FormattedValue, *@lookuplogicalname); keeps "
                   "business fields, _*_value lookup GUIDs, and the primary id.")
@pass_ctx
def query_user(ctx: CLIContext, entity_set, userquery_id, annotations, page_size, minimal):
    """Execute a saved view (userquery) by GUID."""
    with d365_errors(ctx):
        result = query_mod.user_query(
            ctx.backend(), entity_set, userquery_id,
            include_annotations=annotations, page_size=page_size,
        )
    _emit_query_result(ctx, result, entity_set, minimal=minimal)


@query_group.command("count")
@click.argument("entity")
@pass_ctx
def query_count(ctx: CLIContext, entity: str):
    """Count rows for an entity via RetrieveTotalRecordCount (cached server-side).

    ENTITY may be the entity-set name ("accounts") or the logical name
    ("account"), in any case — it is resolved to the logical name that
    RetrieveTotalRecordCount requires, so it matches the entity-set form used
    everywhere else in the CLI.
    """
    with d365_errors(ctx):
        backend = ctx.backend()
        logical = resolve_logical_name(backend, entity)
        n = total_record_count(backend, logical)
    ctx.emit(True, data={"entity": logical, "count": n})
