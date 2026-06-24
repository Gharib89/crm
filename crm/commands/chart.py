"""Chart (savedqueryvisualization / userqueryvisualization) command group."""
# pyright: basic
from __future__ import annotations

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _emit_with_warning,
    _journal,
    _publish_option,
    _read_file as _read_required,
    _resolve_publish,
    _resolve_solution,
    _solution_option,
    d365_errors,
)
from crm.core import charts as charts_mod


@click.group("chart")
def chart_group() -> None:
    """Author system and user charts (savedqueryvisualization /
    userqueryvisualization) headlessly, without the chart designer."""


@chart_group.command("list")
@click.argument("entity")
@click.option("--user", "user_owned", is_flag=True,
              help="List user charts instead of system charts.")
@pass_ctx
def chart_list(ctx: CLIContext, entity: str, user_owned: bool) -> None:
    """List charts for ENTITY (system charts by default; --user for user charts)."""
    # list_entity_charts returns list-column summaries only (no datadescription/
    # presentationdescription XML) — use `chart get <id>` for a chart's XML.
    with d365_errors(ctx):
        charts = charts_mod.list_entity_charts(ctx.backend(), entity, user=user_owned)
    id_field = "userqueryvisualizationid" if user_owned else "savedqueryvisualizationid"
    headers = ["name", id_field] + ([] if user_owned else ["isdefault"])
    rows = [
        [c["name"], c.get(id_field) or ""]
        + ([] if user_owned else [str(c["isdefault"])])
        for c in charts
    ]
    ctx.emit(True, data=charts, table={"headers": headers, "rows": rows})


@chart_group.command("get")
@click.argument("chart_id")
@click.option("--user", "user_owned", is_flag=True,
              help="Look up a user chart instead of a system chart.")
@pass_ctx
def chart_get(ctx: CLIContext, chart_id: str, user_owned: bool) -> None:
    """Get a single chart by CHART_ID (its XML included for --json export)."""
    with d365_errors(ctx):
        info = charts_mod.get_chart(ctx.backend(), chart_id, user=user_owned)
    ctx.emit(True, data=info)


@chart_group.command("delete")
@click.argument("chart_id")
@click.option("--user", "user_owned", is_flag=True,
              help="Delete a user chart instead of a system chart.")
@pass_ctx
def chart_delete(ctx: CLIContext, chart_id: str, user_owned: bool) -> None:
    """Delete a chart by CHART_ID."""
    with d365_errors(ctx):
        info = charts_mod.delete_chart(ctx.backend(), chart_id, user=user_owned)
    ctx.emit(True, data=info)


def _read_file(path: str | None) -> str | None:
    """str|None front for the shared file reader (chart's XML args are optional)."""
    return None if path is None else _read_required(path)


@chart_group.command("create")
@click.argument("entity")
@click.option("--name", required=True, help="Chart display name.")
@click.option("--data-description", "data_description_file",
              type=click.Path(exists=True, dir_okay=False, readable=True),
              help="Path to the datadescription XML file "
                   "(use with --presentation-description; not with --web-resource).")
@click.option("--presentation-description", "presentation_description_file",
              type=click.Path(exists=True, dir_okay=False, readable=True),
              help="Path to the presentationdescription XML file "
                   "(use with --data-description; not with --web-resource).")
@click.option("--web-resource", "web_resource", default=None,
              help="Web resource name or GUID for a script-based visualization "
                   "(not with --data-description / --presentation-description).")
@click.option("--user", "user_owned", is_flag=True,
              help="Create a user chart (userqueryvisualization) instead of a system chart.")
@click.option("--description", default=None, help="Chart description.")
@_solution_option
@_publish_option
@pass_ctx
def chart_create(
    ctx: CLIContext,
    entity: str,
    name: str,
    data_description_file: str | None,
    presentation_description_file: str | None,
    web_resource: str | None,
    user_owned: bool,
    description: str | None,
    solution: str | None,
    require_solution: bool,
    publish: bool,
) -> None:
    """Create a chart on ENTITY.

    \b
    Modes (mutually exclusive):
      --data-description FILE + --presentation-description FILE
          XML-based chart authored from files in source control.
      --web-resource NAME
          Script-based visualization backed by a web resource.
    """
    has_xml = data_description_file is not None or presentation_description_file is not None
    has_wr = web_resource is not None
    if has_xml and has_wr:
        raise click.UsageError(
            "--web-resource and --data-description / --presentation-description "
            "are mutually exclusive.")
    if not has_xml and not has_wr:
        raise click.UsageError(
            "Provide either --data-description + --presentation-description "
            "(XML mode) or --web-resource (web resource mode).")
    if has_xml and (data_description_file is None or presentation_description_file is None):
        raise click.UsageError(
            "Both --data-description and --presentation-description are required "
            "in XML mode.")

    data_xml = _read_file(data_description_file)
    pres_xml = _read_file(presentation_description_file)

    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)

    with d365_errors(ctx):
        info = charts_mod.create_chart(
            ctx.backend(),
            entity=entity,
            name=name,
            data_description=data_xml,
            presentation_description=pres_xml,
            web_resource=web_resource,
            user=user_owned,
            solution=solution,
            publish=publish,
            description=description,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, name, info, solution=solution)


_user_option = click.option(
    "--user", "user_owned", is_flag=True,
    help="Edit a user chart (userqueryvisualization) instead of a system chart. "
         "User charts are never published.")


@chart_group.command("update")
@click.argument("chart_id")
@click.option("--data-description", "data_description_file",
              type=click.Path(exists=True, dir_okay=False, readable=True),
              help="Replace the datadescription XML from this file.")
@click.option("--presentation-description", "presentation_description_file",
              type=click.Path(exists=True, dir_okay=False, readable=True),
              help="Replace the presentationdescription XML from this file.")
@click.option("--name", default=None, help="New chart display name.")
@click.option("--description", default=None, help="New chart description.")
@click.option("--type", "chart_type", default=None,
              help="Set the chart type (ChartType) on every series, e.g. Column, Bar, Line, Pie.")
@_user_option
@_solution_option
@_publish_option
@pass_ctx
def chart_update(
    ctx: CLIContext,
    chart_id: str,
    data_description_file: str | None,
    presentation_description_file: str | None,
    name: str | None,
    description: str | None,
    chart_type: str | None,
    user_owned: bool,
    solution: str | None,
    require_solution: bool,
    publish: bool,
) -> None:
    """Update a chart's XML, name/description, or series chart type.

    On a partial XML update (only one of --data-description /
    --presentation-description) the other column is read live so the
    alias-coupling pair is validated together. The chart's host entity
    (primaryentitytypecode) is never changed.
    """
    data_xml = _read_file(data_description_file)
    pres_xml = _read_file(presentation_description_file)
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = charts_mod.update_chart(
            ctx.backend(), chart_id,
            data_description=data_xml, presentation_description=pres_xml,
            name=name, description=description, chart_type=chart_type,
            user=user_owned, publish=publish, solution=solution)
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, chart_id, info, solution=solution)


@chart_group.command("set-fetch")
@click.argument("chart_id")
@click.option("--fetch", "fetch_file", required=True,
              type=click.Path(exists=True, dir_okay=False, readable=True),
              help="Path to a file with the replacement <fetch> element.")
@_user_option
@_solution_option
@_publish_option
@pass_ctx
def chart_set_fetch(
    ctx: CLIContext,
    chart_id: str,
    fetch_file: str,
    user_owned: bool,
    solution: str | None,
    require_solution: bool,
    publish: bool,
) -> None:
    """Replace the inner <fetch> of a chart's datadescription (keeps its categories)."""
    fetch_xml = _read_file(fetch_file) or ""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = charts_mod.set_chart_fetch(
            ctx.backend(), chart_id, fetch=fetch_xml,
            user=user_owned, publish=publish, solution=solution)
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, chart_id, info, solution=solution)


@chart_group.command("add-series")
@click.argument("chart_id")
@click.option("--column", required=True, help="Logical name of the column to aggregate.")
@click.option("--aggregate", required=True,
              type=click.Choice(["count", "countcolumn", "sum", "avg", "min", "max"]),
              help="Aggregate function applied to --column.")
@click.option("--alias", required=True, help="Unique alias for the new series.")
@_user_option
@_solution_option
@_publish_option
@pass_ctx
def chart_add_series(
    ctx: CLIContext,
    chart_id: str,
    column: str,
    aggregate: str,
    alias: str,
    user_owned: bool,
    solution: str | None,
    require_solution: bool,
    publish: bool,
) -> None:
    """Add an aggregate series to a chart (fetch attribute + measure + presentation series)."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = charts_mod.add_chart_series(
            ctx.backend(), chart_id, column=column, aggregate=aggregate, alias=alias,
            user=user_owned, publish=publish, solution=solution)
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, chart_id, info, solution=solution)


@chart_group.command("remove-series")
@click.argument("chart_id")
@click.option("--alias", required=True, help="Alias of the series to remove.")
@_user_option
@_solution_option
@_publish_option
@pass_ctx
def chart_remove_series(
    ctx: CLIContext,
    chart_id: str,
    alias: str,
    user_owned: bool,
    solution: str | None,
    require_solution: bool,
    publish: bool,
) -> None:
    """Remove an aggregate series from a chart by its alias (refuses the last series)."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = charts_mod.remove_chart_series(
            ctx.backend(), chart_id, alias=alias,
            user=user_owned, publish=publish, solution=solution)
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, chart_id, info, solution=solution)


@chart_group.command("set-groupby")
@click.argument("chart_id")
@click.option("--column", required=True, help="Logical name of the grouping (category) column.")
@click.option("--dategrouping", default=None,
              type=click.Choice(
                  ["day", "week", "month", "quarter", "year",
                   "fiscal-period", "fiscal-year"]),
              help="Date grouping interval (only for date columns).")
@_user_option
@_solution_option
@_publish_option
@pass_ctx
def chart_set_groupby(
    ctx: CLIContext,
    chart_id: str,
    column: str,
    dategrouping: str | None,
    user_owned: bool,
    solution: str | None,
    require_solution: bool,
    publish: bool,
) -> None:
    """Set a chart's grouping (category) column, optionally with a date grouping."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = charts_mod.set_chart_groupby(
            ctx.backend(), chart_id, column=column, dategrouping=dategrouping,
            user=user_owned, publish=publish, solution=solution)
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, chart_id, info, solution=solution)
