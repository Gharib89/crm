"""Model-driven app (appmodule) commands."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import appmodule as app_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error, _resolve_publish, _solution_option,
    _require_solution, _resolve_solution, _emit_with_warning,
)


@click.group("app")
def app_group():
    """Create and manage model-driven apps (appmodule)."""


@app_group.command("create")
@click.option("--name", required=True, help="App display name.")
@click.option("--unique-name", required=True,
              help="Publisher-prefixed unique name, e.g. 'cwx_crmworx'.")
@click.option("--description", default=None)
@click.option("--if-exists", type=click.Choice(["error", "skip"]), default="error")
@_solution_option
@click.option("--publish/--no-publish", default=True)
@pass_ctx
def app_create(ctx: CLIContext, name, unique_name, description, if_exists,
               solution, require_solution, publish):
    """Create a model-driven app."""
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    publish = _resolve_publish(ctx, publish)
    try:
        info = app_mod.create_app(
            ctx.backend(), name=name, unique_name=unique_name,
            description=description, solution=solution, if_exists=if_exists,
        )
        if publish and not info.get("_dry_run") and not info.get("skipped"):
            from crm.core import solution as sol_mod
            sol_mod.publish_all(ctx.backend())
            info["published"] = True
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning,
                       meta={"staged": True} if ctx.stage_only else None)


@app_group.command("add-components")
@click.argument("app_id")
@click.option("--component", "components", multiple=True, required=True,
              help="Repeatable 'kind:guid' (kind: view|chart|form|dashboard|sitemap|bpf).")
@pass_ctx
def app_add_components(ctx: CLIContext, app_id, components):
    """Bind components to an app (AddAppComponents)."""
    parsed: list[tuple[str, str]] = []
    for raw in components:
        kind, _, guid = raw.partition(":")
        if not guid:
            raise click.BadParameter(f"--component must be 'kind:guid': {raw!r}")
        parsed.append((kind, guid))
    try:
        info = app_mod.add_app_components(ctx.backend(), app_id=app_id,
                                          components=parsed)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@app_group.command("set-sitemap")
@click.argument("sitemap_name")
@click.option("--xml-file", type=click.Path(exists=True, dir_okay=False), required=True,
              help="Path to a file containing the SiteMapXml.")
@click.option("--unique-name", default=None,
              help="App uniquename to link the sitemap to (sets sitemapnameunique).")
@_solution_option
@pass_ctx
def app_set_sitemap(ctx: CLIContext, sitemap_name, xml_file, unique_name,
                    solution, require_solution):
    """Create a sitemap from a SiteMapXml file."""
    with open(xml_file, "r", encoding="utf-8") as fh:
        xml = fh.read()
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    try:
        info = app_mod.set_sitemap(ctx.backend(), sitemap_name=sitemap_name,
                                   sitemap_xml=xml, unique_name=unique_name,
                                   solution=solution)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning)
