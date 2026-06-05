"""Model-driven app (appmodule) commands."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import appmodule as app_mod
from crm.core import webresource as wr_mod
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
@click.option("--icon-webresource", "icon_webresource", default=None,
              help="Web resource (name or GUID) for the app icon. "
                   "Defaults to the platform icon when omitted.")
@_solution_option
@click.option("--publish/--no-publish", default=True)
@pass_ctx
def app_create(ctx: CLIContext, name, unique_name, description, if_exists,
               icon_webresource, solution, require_solution, publish):
    """Create a model-driven app."""
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    publish = _resolve_publish(ctx, publish)
    try:
        backend = ctx.backend()
        if icon_webresource:
            web_resource_id = wr_mod.resolve_webresource_id(backend, icon_webresource)
        else:
            web_resource_id = app_mod.DEFAULT_APP_ICON
        info = app_mod.create_app(
            backend, name=name, unique_name=unique_name,
            description=description, web_resource_id=web_resource_id,
            solution=solution, if_exists=if_exists, publish=publish,
        )
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
        kind, guid = kind.strip(), guid.strip()
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


def _parse_area(raw: str) -> tuple[str, str]:
    """Parse 'id:Title' (title optional; core defaults an empty title)."""
    area_id, _, title = raw.partition(":")
    area_id = area_id.strip()
    if not area_id:
        raise click.BadParameter(f"--area must be 'id:Title': {raw!r}")
    return area_id, title.strip()


def _parse_group(raw: str) -> tuple[str, str, str]:
    """Parse 'areaId/groupId:Title' into (area_id, group_id, title)."""
    ref, _, title = raw.partition(":")
    area_id, sep, group_id = ref.partition("/")
    area_id, group_id = area_id.strip(), group_id.strip()
    if not sep or not area_id or not group_id:
        raise click.BadParameter(
            f"--group must be 'areaId/groupId:Title': {raw!r}")
    return area_id, group_id, title.strip()


def _parse_subarea(raw: str) -> tuple[str, str, str, str | None]:
    """Parse 'areaId/groupId:entity=<logical>[:Title]'.

    Returns (area_id, group_id, entity, title_or_None). The title is None when
    no second ':' segment is given (or it is whitespace); core then derives the
    label from the entity.
    """
    ref, sep, rest = raw.partition(":")
    if not sep:
        raise click.BadParameter(
            f"--subarea must be 'areaId/groupId:entity=<logical>[:Title]': {raw!r}")
    area_id, ref_sep, group_id = ref.partition("/")
    area_id, group_id = area_id.strip(), group_id.strip()
    if not ref_sep or not area_id or not group_id:
        raise click.BadParameter(
            f"--subarea must be 'areaId/groupId:entity=<logical>[:Title]': {raw!r}")
    ent_part, _, title = rest.partition(":")
    ent_part = ent_part.strip()
    if not ent_part.startswith("entity="):
        raise click.BadParameter(
            f"--subarea must bind a table via 'entity=<logical>': {raw!r}")
    entity = ent_part[len("entity="):].strip()
    if not entity:
        raise click.BadParameter(f"--subarea entity must not be empty: {raw!r}")
    title = title.strip()
    return area_id, group_id, entity, (title or None)


@app_group.command("build-sitemap")
@click.argument("sitemap_name")
@click.option("--area", "areas", multiple=True, required=True,
              help="Repeatable 'id:Title'.")
@click.option("--group", "groups", multiple=True,
              help="Repeatable 'areaId/groupId:Title'.")
@click.option("--subarea", "subareas", multiple=True,
              help="Repeatable 'areaId/groupId:entity=<logical>[:Title]'. "
                   "Binds a table via Entity=.")
@click.option("--unique-name", default=None,
              help="App uniquename to link the sitemap to (sets sitemapnameunique).")
@_solution_option
@click.option("--publish/--no-publish", default=True,
              help="Run PublishAllXml after creation. Default: publish.")
@pass_ctx
def app_build_sitemap(ctx: CLIContext, sitemap_name, areas, groups, subareas,
                      unique_name, solution, require_solution, publish):
    """Build a SiteMapXml from areas/groups/subareas and create the sitemap."""
    parsed_areas = [_parse_area(a) for a in areas]
    parsed_groups = [_parse_group(g) for g in groups]
    parsed_subareas = [_parse_subarea(s) for s in subareas]
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    publish = _resolve_publish(ctx, publish)
    try:
        info = app_mod.build_sitemap(
            ctx.backend(), sitemap_name=sitemap_name, areas=parsed_areas,
            groups=parsed_groups, subareas=parsed_subareas,
            unique_name=unique_name, solution=solution, publish=publish,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning,
                       meta={"staged": True} if ctx.stage_only else None)


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
