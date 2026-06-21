"""Dashboard (systemform type=0) command group."""
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
from crm.core import dashboard as dashboard_mod


@click.group("dashboard")
def dashboard_group() -> None:
    """Author organization-owned system dashboards (systemform type=0) from
    FormXml headlessly, without the dashboard designer."""


@dashboard_group.command("list")
@pass_ctx
def dashboard_list(ctx: CLIContext) -> None:
    """List organization-owned dashboards."""
    # list returns list columns only (no formxml) — use `dashboard get <id>`
    # for a dashboard's FormXml.
    with d365_errors(ctx):
        dashboards = dashboard_mod.list_dashboards(ctx.backend())
    headers = ["name", "formid", "isdefault"]
    rows = [[d["name"], d.get("formid") or "", str(d["isdefault"])] for d in dashboards]
    ctx.emit(True, data=dashboards, table={"headers": headers, "rows": rows})


@dashboard_group.command("get")
@click.argument("dashboard_id")
@pass_ctx
def dashboard_get(ctx: CLIContext, dashboard_id: str) -> None:
    """Get a single dashboard by DASHBOARD_ID (its FormXml included for export)."""
    with d365_errors(ctx):
        info = dashboard_mod.get_dashboard(ctx.backend(), dashboard_id)
    ctx.emit(True, data=info)


@dashboard_group.command("delete")
@click.argument("dashboard_id")
@pass_ctx
def dashboard_delete(ctx: CLIContext, dashboard_id: str) -> None:
    """Delete a dashboard by DASHBOARD_ID."""
    with d365_errors(ctx):
        info = dashboard_mod.delete_dashboard(ctx.backend(), dashboard_id)
    ctx.emit(True, data=info)


def _layout_options(fn):
    """Stack the shared tile-placement options (tab/section/rowspan/colspan/force)."""
    fn = click.option("--tab", default=None,
                      help="Target tab (name or id; default: first tab).")(fn)
    fn = click.option("--section", default=None,
                      help="Add to an existing section (name or id); default: "
                           "a new section per tile (one component per section).")(fn)
    fn = click.option("--rowspan", type=click.IntRange(min=1), default=1,
                      show_default=True,
                      help="Cell rowspan; the section is padded to match it.")(fn)
    fn = click.option("--colspan", type=click.IntRange(min=1), default=1,
                      show_default=True, help="Cell colspan.")(fn)
    fn = click.option("--force", is_flag=True,
                      help="Add beyond the default six-component cap.")(fn)
    return fn


@dashboard_group.command("add-chart")
@click.argument("dashboard_id")
@click.option("--view", required=True,
              help="savedquery id (GUID) whose data the grid shows.")
@click.option("--chart", required=True,
              help="savedqueryvisualization id (GUID) to render; its primary "
                   "entity must match the view's.")
@_layout_options
@_solution_option
@_publish_option
@pass_ctx
def dashboard_add_chart(
    ctx: CLIContext, dashboard_id: str, view: str, chart: str,
    tab: str | None, section: str | None, rowspan: int, colspan: int,
    force: bool, solution: str | None, require_solution: bool, publish: bool,
) -> None:
    """Add a chart tile (ChartGrid) to dashboard DASHBOARD_ID's FormXml."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = dashboard_mod.add_chart_to_dashboard(
            ctx.backend(), dashboard_id, view=view, chart=chart,
            tab=tab, section=section, rowspan=rowspan, colspan=colspan,
            force=force, solution=solution, publish=publish)
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, dashboard_id, info, solution=solution)


@dashboard_group.command("add-view")
@click.argument("dashboard_id")
@click.option("--view", required=True,
              help="savedquery id (GUID) whose data the grid shows.")
@click.option("--mode", type=click.Choice(["list", "all"]), default="list",
              show_default=True,
              help="Grid only (list) or grid with the chart toggle (all).")
@click.option("--records-per-page", "records_per_page",
              type=click.IntRange(min=1), default=10, show_default=True,
              help="Rows shown per page in the grid.")
@_layout_options
@_solution_option
@_publish_option
@pass_ctx
def dashboard_add_view(
    ctx: CLIContext, dashboard_id: str, view: str, mode: str,
    records_per_page: int, tab: str | None, section: str | None,
    rowspan: int, colspan: int, force: bool,
    solution: str | None, require_solution: bool, publish: bool,
) -> None:
    """Add a view-only grid tile (ChartGrid) to dashboard DASHBOARD_ID's FormXml."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = dashboard_mod.add_view_to_dashboard(
            ctx.backend(), dashboard_id, view=view, mode=mode,
            records_per_page=records_per_page, tab=tab, section=section,
            rowspan=rowspan, colspan=colspan, force=force,
            solution=solution, publish=publish)
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, dashboard_id, info, solution=solution)


@dashboard_group.command("add-iframe")
@click.argument("dashboard_id")
@click.option("--url", required=True,
              help="The IFRAME URL (must be non-empty).")
@click.option("--security", is_flag=True,
              help="Restrict cross-frame scripting (the FormXml Security flag).")
@click.option("--scrolling", is_flag=True, help="Show IFRAME scrollbars.")
@click.option("--border", is_flag=True, help="Draw a border around the IFRAME.")
@click.option("--pass-parameters", "pass_parameters", is_flag=True,
              help="Pass record object-type code and id as URL parameters.")
@_layout_options
@_solution_option
@_publish_option
@pass_ctx
def dashboard_add_iframe(
    ctx: CLIContext, dashboard_id: str, url: str, security: bool,
    scrolling: bool, border: bool, pass_parameters: bool,
    tab: str | None, section: str | None, rowspan: int, colspan: int,
    force: bool, solution: str | None, require_solution: bool, publish: bool,
) -> None:
    """Add an IFRAME tile to dashboard DASHBOARD_ID's FormXml."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = dashboard_mod.add_iframe_to_dashboard(
            ctx.backend(), dashboard_id, url=url, security=security,
            scrolling=scrolling, border=border, pass_parameters=pass_parameters,
            tab=tab, section=section, rowspan=rowspan, colspan=colspan,
            force=force, solution=solution, publish=publish)
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, dashboard_id, info, solution=solution)


@dashboard_group.command("add-webresource")
@click.argument("dashboard_id")
@click.option("--webresource", required=True,
              help="Web resource id (GUID) or unique name to embed.")
@_layout_options
@_solution_option
@_publish_option
@pass_ctx
def dashboard_add_webresource(
    ctx: CLIContext, dashboard_id: str, webresource: str,
    tab: str | None, section: str | None, rowspan: int, colspan: int,
    force: bool, solution: str | None, require_solution: bool, publish: bool,
) -> None:
    """Add a web-resource tile to dashboard DASHBOARD_ID's FormXml."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = dashboard_mod.add_webresource_to_dashboard(
            ctx.backend(), dashboard_id, webresource=webresource,
            tab=tab, section=section, rowspan=rowspan, colspan=colspan,
            force=force, solution=solution, publish=publish)
    # fold a not-form-enabled advisory into the warnings channel
    wr_warning = info.pop("warning", None) if isinstance(info, dict) else None
    combined = "; ".join(w for w in (warning, wr_warning) if w) or None
    _emit_with_warning(ctx, info, combined, meta=ctx.staged_meta())
    _journal(ctx, dashboard_id, info, solution=solution)


@dashboard_group.command("remove-component")
@click.argument("dashboard_id")
@click.option("--cell-id", "cell_id", default=None,
              help="Remove the component cell with this id.")
@click.option("--index", type=int, default=None,
              help="Remove the component at this 0-based position.")
@click.option("--view", default=None,
              help="Remove the component whose ViewId is this savedquery id.")
@click.option("--chart", default=None,
              help="Remove the component whose VisualizationId is this id.")
@click.option("--url", default=None,
              help="Remove the IFRAME/web-resource component with this Url.")
@_solution_option
@_publish_option
@pass_ctx
def dashboard_remove_component(
    ctx: CLIContext, dashboard_id: str, cell_id: str | None, index: int | None,
    view: str | None, chart: str | None, url: str | None,
    solution: str | None, require_solution: bool, publish: bool,
) -> None:
    """Remove one component from dashboard DASHBOARD_ID, selected by exactly one
    of --cell-id / --index / --view / --chart / --url."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = dashboard_mod.remove_component_from_dashboard(
            ctx.backend(), dashboard_id, cell_id=cell_id, index=index,
            view=view, chart=chart, url=url,
            solution=solution, publish=publish)
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, dashboard_id, info, solution=solution)


def _read_file(path: str) -> str:
    # click.Path(exists=True, readable=True) validates at parse time, but a
    # permission edge or a delete-after-check race can still fail the open —
    # surface it as a clean usage error (mirrors crm.commands.chart._read_file).
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise click.UsageError(f"cannot read {path}: {exc}") from exc


@dashboard_group.command("create")
@click.option("--name", required=True, help="Dashboard display name.")
@click.option("--formxml", "formxml_file", required=True,
              type=click.Path(exists=True, dir_okay=False, readable=True),
              help="Path to the dashboard FormXml file.")
@click.option("--description", default=None, help="Dashboard description.")
@click.option("--interactive", is_flag=True,
              help="(Rejected) interactive-experience (type-10) dashboards are "
                   "not creatable over the Web API — see the error for details.")
@_solution_option
@_publish_option
@pass_ctx
def dashboard_create(
    ctx: CLIContext,
    name: str,
    formxml_file: str,
    description: str | None,
    interactive: bool,
    solution: str | None,
    require_solution: bool,
    publish: bool,
) -> None:
    """Create an organization-owned system dashboard from a FormXml file."""
    if interactive:
        raise click.UsageError(
            "Interactive-experience (type-10) dashboards are not programmatically "
            "creatable over the Web API — author them in the dashboard designer. "
            "Omit --interactive to create a standard system dashboard (type-0).")

    formxml = _read_file(formxml_file)
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)

    with d365_errors(ctx):
        info = dashboard_mod.create_dashboard(
            ctx.backend(),
            name=name,
            formxml=formxml,
            description=description,
            solution=solution,
            publish=publish,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, name, info, solution=solution)
