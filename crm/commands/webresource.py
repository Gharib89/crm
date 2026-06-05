"""Web resource (webresourceset) commands."""
# pyright: basic
from __future__ import annotations
from pathlib import Path
import click
from crm.core import webresource as wr_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error, _resolve_publish, _solution_option,
    _require_solution, _resolve_solution, _emit_with_warning,
)


@click.group("webresource")
def webresource_group():
    """Create and manage web resources (HTML/JS/CSS/images)."""


@webresource_group.command("create")
@click.option("--name", required=True,
              help="Web resource unique name, e.g. 'cwx_/scripts/foo.js'.")
@click.option("--file", required=True, type=click.Path(exists=True, dir_okay=False),
              help="Source file whose bytes become the web resource content.")
@click.option("--display-name", "display_name", default=None)
@click.option("--type", "wr_type", type=int, default=None,
              help="Override the D365 webresourcetype int; inferred from the "
                   "file extension if omitted.")
@_solution_option
@click.option("--publish/--no-publish", default=True)
@pass_ctx
def webresource_create(ctx: CLIContext, name, file, display_name, wr_type,
                       solution, require_solution, publish):
    """Create a web resource."""
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    publish = _resolve_publish(ctx, publish)
    try:
        wtype = wr_mod.resolve_webresourcetype(file, wr_type)
        content = Path(file).read_bytes()
        info = wr_mod.create_webresource(
            ctx.backend(), name=name, content=content, webresourcetype=wtype,
            display_name=display_name, solution=solution, publish=publish)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning,
                       meta={"staged": True} if ctx.stage_only else None)


@webresource_group.command("update")
@click.argument("name")
@click.option("--file", default=None, type=click.Path(exists=True, dir_okay=False),
              help="New source file whose bytes replace the web resource content.")
@click.option("--display-name", "display_name", default=None)
@_solution_option
@click.option("--publish/--no-publish", default=True)
@pass_ctx
def webresource_update(ctx: CLIContext, name, file, display_name,
                       solution, require_solution, publish):
    """Update a web resource by name (content and/or display name)."""
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    publish = _resolve_publish(ctx, publish)
    content = Path(file).read_bytes() if file else None
    try:
        info = wr_mod.update_webresource(
            ctx.backend(), name, content=content, display_name=display_name,
            solution=solution, publish=publish)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning,
                       meta={"staged": True} if ctx.stage_only else None)


@webresource_group.command("get")
@click.argument("name")
@pass_ctx
def webresource_get(ctx: CLIContext, name):
    """Resolve a web resource by name and print its record."""
    try:
        record = wr_mod.get_webresource(ctx.backend(), name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=record)


@webresource_group.command("list")
@click.option("--custom-only", is_flag=True, help="Only unmanaged web resources.")
@click.option("--top", type=int, default=None, help="Limit to the first N rows.")
@pass_ctx
def webresource_list(ctx: CLIContext, custom_only, top):
    """List web resources."""
    try:
        items = wr_mod.list_webresources(
            ctx.backend(), custom_only=custom_only, top=top)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["name", "displayname", "webresourcetype", "ismanaged"]
    rows = [[it.get(h, "") for h in headers] for it in items]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"count": len(items)})
