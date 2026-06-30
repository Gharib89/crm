"""Web resource (webresourceset) commands."""
# pyright: basic
from __future__ import annotations
from pathlib import Path
import click
from crm.core import webresource as wr_mod
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _publish_option,
    d365_errors, _journal, _resolve_publish, _solution_option,
    _resolve_solution, _emit_with_warning,
    _confirm_destructive, _destructive_option,
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
@_publish_option
@pass_ctx
def webresource_create(ctx: CLIContext, name, file, display_name, wr_type,
                       solution, publish):
    """Create a web resource."""
    solution, warning = _resolve_solution(ctx, solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        wtype = wr_mod.resolve_webresourcetype(file, wr_type)
        content = Path(file).read_bytes()
        info = wr_mod.create_webresource(
            ctx.backend(), name=name, content=content, webresourcetype=wtype,
            display_name=display_name, solution=solution, publish=publish)
    _emit_with_warning(ctx, info, warning,
                       meta=ctx.staged_meta())
    _journal(ctx, name, info, solution=solution)


@webresource_group.command("update")
@click.argument("name")
@click.option("--file", default=None, type=click.Path(exists=True, dir_okay=False),
              help="New source file whose bytes replace the web resource content.")
@click.option("--display-name", "display_name", default=None)
@_solution_option
@_publish_option
@pass_ctx
def webresource_update(ctx: CLIContext, name, file, display_name,
                       solution, publish):
    """Update a web resource by name (content and/or display name)."""
    solution, warning = _resolve_solution(ctx, solution)
    publish = _resolve_publish(ctx, publish)
    content = Path(file).read_bytes() if file else None
    with d365_errors(ctx):
        info = wr_mod.update_webresource(
            ctx.backend(), name, content=content, display_name=display_name,
            solution=solution, publish=publish)
    _emit_with_warning(ctx, info, warning,
                       meta=ctx.staged_meta())
    _journal(ctx, name, info, solution=solution)


@webresource_group.command("delete")
@click.argument("name")
@_destructive_option
@click.option("--check-dependencies", "check_dependencies", is_flag=True, default=False,
              help="Preview blocking dependencies (RetrieveDependenciesForDelete) in "
                   "the result; pairs with --dry-run. Informational — does not block.")
@pass_ctx
def webresource_delete(ctx: CLIContext, name, yes, check_dependencies):
    """Delete a web resource by unique name or id."""
    _confirm_destructive(ctx, "web resource", name, yes)
    with d365_errors(ctx):
        info = wr_mod.delete_webresource(
            ctx.backend(), name, check_dependencies=check_dependencies)
    _emit_with_warning(ctx, info, None)
    _journal(ctx, name, info)


def _validate_prefix(_ctx, _param, value):
    """Validate --prefix at parse time so a bad value fails as a usage error
    (exit 2) before the backend is resolved (which can launch the profile
    wizard in human mode). The core re-checks for non-CLI callers."""
    from crm.core.solution import validate_customization_prefix
    from crm.utils.d365_backend import D365Error
    try:
        validate_customization_prefix(value)
    except D365Error as exc:
        raise click.BadParameter(str(exc))
    return value


@webresource_group.command("push")
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option("--prefix", required=True, callback=_validate_prefix,
              help="Publisher customization prefix (2-8 alphanumerics, starts "
                   "with a letter, not 'mscrm'); each file maps to web resource "
                   "name '<prefix>_<relpath>' (path relative to DIRECTORY, '/' "
                   "separators, type inferred from extension).")
@_solution_option
@_publish_option
@pass_ctx
def webresource_push(ctx: CLIContext, directory, prefix, solution,
                     publish):
    """Walk DIRECTORY and upsert every file as a web resource, publishing once.

    The filesystem is the source of truth: a missing resource is created, a
    changed one updated, a byte-identical one skipped, and all changes are
    published once at the end (PublishAllXml) — nothing publishes mid-run. A
    per-file failure is reported without aborting the rest. Honors the global
    --dry-run flag (lists the would-create / would-update sets, writes nothing)
    and --stage-only / --no-publish (write the resources, defer the publish).
    For a continuous redeploy loop, pair with a file watcher, e.g.
    `find webresources -name '*.js' | entr crm webresource push webresources --prefix cwx`.
    """
    solution, warning = _resolve_solution(ctx, solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        res = wr_mod.push_webresources(
            ctx.backend(), directory, prefix=prefix, solution=solution,
            publish=publish)
    ok = not res["failed"]
    error = None
    if res["failed"]:
        error = f"{len(res['failed'])} file(s) failed — " + "; ".join(
            f"{e['name']}: {e['error']}" for e in res["failed"])
    warnings = [warning] if warning else None
    meta = ctx.staged_meta()
    if ctx.json_mode:
        ctx.emit(ok, data=res, error=error, meta=meta, warnings=warnings)
        return
    # Human mode renders the counts; full per-file detail is --json-only. On a
    # failure the human path prints only `error`, so it carries the per-file list.
    keys = (("would_create", "would_update", "skipped", "published")
            if res.get("_dry_run")
            else ("pushed", "updated", "skipped", "published"))
    ctx.emit(ok, data={k: res[k] for k in keys}, error=error, meta=meta,
             warnings=warnings)


@webresource_group.command("get")
@click.argument("name")
@pass_ctx
def webresource_get(ctx: CLIContext, name):
    """Resolve a web resource by name and print its record."""
    with d365_errors(ctx):
        record = wr_mod.get_webresource(ctx.backend(), name)
    ctx.emit(True, data=record)


@webresource_group.command("list")
@click.option("--custom-only", is_flag=True, help="Only unmanaged web resources.")
@click.option("--top", type=int, default=None, help="Limit to the first N rows.")
@pass_ctx
def webresource_list(ctx: CLIContext, custom_only, top):
    """List web resources."""
    with d365_errors(ctx):
        items = wr_mod.list_webresources(
            ctx.backend(), custom_only=custom_only, top=top)
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["name", "displayname", "webresourcetype", "ismanaged"]
    rows = [[it.get(h, "") for h in headers] for it in items]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"count": len(items)})
