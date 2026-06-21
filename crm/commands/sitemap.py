"""Live SiteMap navigation editors (add/remove Area, Group, SubArea).

Complements ``app build-sitemap`` / ``app set-sitemap`` (which POST a whole new
SiteMapXml): these verbs edit an *existing* sitemap in place over the
read-modify-write seam (GET → mutate → PATCH). ``--dry-run`` previews the
resulting SiteMapXml without writing; ``--publish`` runs PublishAllXml and a T3
read-back. Pass the target sitemap's GUID (``query odata sitemaps`` to find it).
"""
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
from crm.core import sitemap as sitemap_mod


@click.group("sitemap")
def sitemap_group() -> None:
    """Edit a live model-driven app SiteMap's navigation (areas/groups/subareas)."""


def _emit(ctx: CLIContext, info: dict, warning: str | None) -> None:
    """Fold any cascade advisory in with the solution warning, then emit.

    The cascade advisory is moved out of ``data`` and onto the structured
    warnings channel only — keeping the JSON ``data`` payload to identifying
    fields, like the other mutating verbs.
    """
    cascade = info.pop("cascade_warning", None)
    merged = " ".join(w for w in (warning, cascade) if w) or None
    _emit_with_warning(ctx, info, merged, meta=ctx.staged_meta())


@sitemap_group.command("add-area")
@click.argument("sitemap_id")
@click.option("--id", "area_id", required=True, help="New Area Id ([a-zA-Z0-9_]+).")
@click.option("--title", required=True, help="Area display title.")
@click.option("--icon", default=None,
              help="Area icon (a path or '$webresource:<name>').")
@click.option("--show-groups", is_flag=True, default=False,
              help="Set ShowGroups='true' on the new Area.")
@_solution_option
@_publish_option
@pass_ctx
def sitemap_add_area(ctx: CLIContext, sitemap_id, area_id, title, icon,
                     show_groups, solution, require_solution, publish):
    """Add an Area to the sitemap SITEMAP_ID."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = sitemap_mod.add_area(
            ctx.backend(), sitemap_id, area_id=area_id, title=title, icon=icon,
            show_groups=show_groups, publish=publish, solution=solution)
    _emit(ctx, info, warning)
    _journal(ctx, sitemap_id, info, solution=solution)


@sitemap_group.command("add-group")
@click.argument("sitemap_id")
@click.option("--area", "area_id", required=True, help="Parent Area Id.")
@click.option("--id", "group_id", required=True, help="New Group Id ([a-zA-Z0-9_]+).")
@click.option("--title", required=True, help="Group display title.")
@_solution_option
@_publish_option
@pass_ctx
def sitemap_add_group(ctx: CLIContext, sitemap_id, area_id, group_id, title,
                      solution, require_solution, publish):
    """Add a Group under an Area in the sitemap SITEMAP_ID."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = sitemap_mod.add_group(
            ctx.backend(), sitemap_id, area_id=area_id, group_id=group_id,
            title=title, publish=publish, solution=solution)
    _emit(ctx, info, warning)
    _journal(ctx, sitemap_id, info, solution=solution)


@sitemap_group.command("add-subarea")
@click.argument("sitemap_id")
@click.option("--area", "area_id", required=True, help="Parent Area Id.")
@click.option("--group", "group_id", required=True, help="Parent Group Id.")
@click.option("--id", "sub_id", required=True, help="New SubArea Id ([a-zA-Z0-9_]+).")
@click.option("--entity", default=None,
              help="Bind a table by logical name (validated to exist).")
@click.option("--url", default=None,
              help="Link to a URL (incl. an HTML web resource).")
@click.option("--dashboard", default=None, help="Open a dashboard by GUID.")
@click.option("--title", default=None, help="SubArea display title.")
@click.option("--icon", default=None,
              help="SubArea icon (a path or '$webresource:<name>').")
@_solution_option
@_publish_option
@pass_ctx
def sitemap_add_subarea(ctx: CLIContext, sitemap_id, area_id, group_id, sub_id,
                        entity, url, dashboard, title, icon,
                        solution, require_solution, publish):
    """Add a SubArea under a Group (exactly one of --entity/--url/--dashboard)."""
    # Strip first, so a blank-ish flag (--url '' or --url '   ') is treated as
    # missing and still yields a usage error (exit 2), not a confusing core error
    # (exit 1) or a node bound to a whitespace value.
    entity = (entity or "").strip() or None
    url = (url or "").strip() or None
    dashboard = (dashboard or "").strip() or None
    if sum(1 for v in (entity, url, dashboard) if v) != 1:
        raise click.UsageError(
            "Provide exactly one of --entity, --url or --dashboard.")
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = sitemap_mod.add_subarea(
            ctx.backend(), sitemap_id, area_id=area_id, group_id=group_id,
            sub_id=sub_id, entity=entity, url=url, dashboard=dashboard,
            title=title, icon=icon, publish=publish, solution=solution)
    _emit(ctx, info, warning)
    _journal(ctx, sitemap_id, info, solution=solution)


@sitemap_group.command("remove-node")
@click.argument("sitemap_id")
@click.option("--id", "node_id", required=True,
              help="Id of the Area/Group/SubArea to remove.")
@click.option("--comment-out", is_flag=True, default=False,
              help="Replace the node with an XML comment instead of deleting it.")
@_solution_option
@_publish_option
@pass_ctx
def sitemap_remove_node(ctx: CLIContext, sitemap_id, node_id, comment_out,
                        solution, require_solution, publish):
    """Remove (or comment out) a node from the sitemap SITEMAP_ID."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = sitemap_mod.remove_node(
            ctx.backend(), sitemap_id, node_id=node_id, comment_out=comment_out,
            publish=publish, solution=solution)
    _emit(ctx, info, warning)
    _journal(ctx, sitemap_id, info, solution=solution)
