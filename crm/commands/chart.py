"""Chart (savedqueryvisualization / userqueryvisualization) command group."""
# pyright: basic
from __future__ import annotations

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _publish_option,
    _resolve_publish,
    _resolve_solution,
    _solution_option,
    d365_errors,
    _emit_with_warning,
    _journal,
)
from crm.core import charts as charts_mod


@click.group("chart")
def chart_group() -> None:
    """Author system and user charts (savedqueryvisualization) headlessly."""


@chart_group.command("list")
@click.argument("entity")
@click.option("--user", "user_owned", is_flag=True,
              help="List user charts instead of system charts.")
@pass_ctx
def chart_list(ctx: CLIContext, entity: str, user_owned: bool) -> None:
    """List charts for ENTITY (system charts by default; --user for user charts)."""
    with d365_errors(ctx):
        charts = charts_mod.list_entity_charts(ctx.backend(), entity, user=user_owned)
    id_field = "userqueryvisualizationid" if user_owned else "savedqueryvisualizationid"
    headers = ["name", id_field]
    if not user_owned:
        headers.append("isdefault")
    rows = [
        ([c["name"], c.get(id_field) or ""]
         + ([] if user_owned else [str(c.get("isdefault", False))]))
        for c in charts
    ]
    ctx.emit(True, data=charts, table={"headers": headers, "rows": rows})


@chart_group.command("get")
@click.argument("chart_id")
@click.option("--user", "user_owned", is_flag=True,
              help="Look up in user charts instead of system charts.")
@pass_ctx
def chart_get(ctx: CLIContext, chart_id: str, user_owned: bool) -> None:
    """Get a chart by CHART_ID."""
    with d365_errors(ctx):
        info = charts_mod.get_chart(ctx.backend(), chart_id, user=user_owned)
    ctx.emit(True, data=info)


@chart_group.command("delete")
@click.argument("chart_id")
@click.option("--user", "user_owned", is_flag=True,
              help="Delete from user charts instead of system charts.")
@pass_ctx
def chart_delete(ctx: CLIContext, chart_id: str, user_owned: bool) -> None:
    """Delete a chart by CHART_ID."""
    with d365_errors(ctx):
        info = charts_mod.delete_chart(ctx.backend(), chart_id, user=user_owned)
    ctx.emit(True, data=info)


@chart_group.command("create")
@click.argument("entity")
@click.option("--name", required=True, help="Chart display name.")
@click.option("--data-description", "data_description_file",
              type=click.Path(exists=True),
              help="Path to datadescription XML file (mutually exclusive with --web-resource).")
@click.option("--presentation-description", "presentation_description_file",
              type=click.Path(exists=True),
              help="Path to presentationdescription XML file (mutually exclusive with --web-resource).")
@click.option("--web-resource", "web_resource",
              default=None,
              help="Web resource name or GUID (mutually exclusive with --data-description / "
                   "--presentation-description).")
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
          XML-based chart from files checked into source control.
      --web-resource NAME
          Script-based visualization backed by a web resource.
    """
    # Validate mutually exclusive option groups.
    has_xml = data_description_file is not None or presentation_description_file is not None
    has_wr = web_resource is not None
    if has_xml and has_wr:
        raise click.UsageError(
            "--web-resource and --data-description / --presentation-description "
            "are mutually exclusive."
        )
    if not has_xml and not has_wr:
        raise click.UsageError(
            "Provide either --data-description + --presentation-description "
            "(XML mode) or --web-resource (web resource mode)."
        )
    if has_xml and (data_description_file is None or presentation_description_file is None):
        raise click.UsageError(
            "Both --data-description and --presentation-description are required "
            "in XML mode."
        )

    data_xml: str | None = None
    pres_xml: str | None = None
    if data_description_file is not None:
        with open(data_description_file, encoding="utf-8") as fh:
            data_xml = fh.read()
    if presentation_description_file is not None:
        with open(presentation_description_file, encoding="utf-8") as fh:
            pres_xml = fh.read()

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
