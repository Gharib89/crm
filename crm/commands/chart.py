"""Chart (savedqueryvisualization / userqueryvisualization) command group."""
# pyright: basic
from __future__ import annotations

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _emit_with_warning,
    _journal,
    _publish_option,
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
    if path is None:
        return None
    # click.Path(exists=True, readable=True) validates at parse time, but a
    # permission edge or a delete-after-check race can still fail the open —
    # surface it as a clean usage error (mirrors _helpers.parsing._load_payload).
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise click.UsageError(f"cannot read {path}: {exc}") from exc


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
