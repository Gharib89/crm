"""Solution lifecycle commands."""
from __future__ import annotations
from pathlib import Path
import click
from crm.core import async_ops as async_ops_mod
from crm.core import solution as sol_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error,
    _no_retry_scope,
    _EXPORT_SETTING_KEYS,
)


@click.group("solution")
def solution_group():
    """Solution lifecycle (list / info / components / export / import)."""


@solution_group.command("list")
@click.option("--managed/--unmanaged", default=None, help="Filter by managed flag.")
@pass_ctx
def solution_list(ctx: CLIContext, managed):
    try:
        items = sol_mod.list_solutions(ctx.backend(), managed=managed)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["uniquename", "friendlyname", "version", "ismanaged"]
    rows = [[it.get(h, "") for h in headers] for it in items]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta={"count": len(items)})


@solution_group.command("info")
@click.argument("unique_name")
@pass_ctx
def solution_info_cmd(ctx: CLIContext, unique_name):
    try:
        info = sol_mod.solution_info(ctx.backend(), unique_name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@solution_group.command("components")
@click.argument("unique_name")
@pass_ctx
def solution_components_cmd(ctx: CLIContext, unique_name):
    try:
        items = sol_mod.solution_components(ctx.backend(), unique_name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=items, meta={"count": len(items)})


@solution_group.command("export")
@click.argument("unique_name")
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False))
@click.option("--managed", is_flag=True)
@click.option(
    "--export-setting",
    "export_settings",
    multiple=True,
    type=click.Choice(sorted(_EXPORT_SETTING_KEYS.keys())),
    help="Repeatable; include a named export setting in the solution payload.",
)
@click.option("--timeout", type=int, default=None,
              help="Async operation timeout in seconds. Overrides profile.async_timeout.")
@click.option("--no-retry", is_flag=True,
              help="Disable the 429/5xx retry loop for this invocation.")
@pass_ctx
def solution_export_cmd(ctx: CLIContext, unique_name, output, managed, export_settings, timeout, no_retry):
    kwargs = {_EXPORT_SETTING_KEYS[name]: True for name in export_settings}
    with _no_retry_scope(ctx, no_retry):
        try:
            info = sol_mod.export_solution(
                ctx.backend(), unique_name, output, managed=managed,
                timeout=timeout, **kwargs,
            )
        except D365Error as exc:
            _handle_d365_error(ctx, exc)
            return
        ctx.emit(True, data=info)


@solution_group.command("publish-all")
@pass_ctx
def solution_publish_all(ctx: CLIContext):
    """Call PublishAllXml — publish every unpublished customization."""
    try:
        result = sol_mod.publish_all(ctx.backend())
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result or {"published": True})


@solution_group.command("publish")
@click.option("--xml", "parameter_xml", help="Inline Publish Request Schema XML.")
@click.option("--xml-file", type=click.Path(exists=True, dir_okay=False),
              help="Path to a Publish Request Schema XML file.")
@pass_ctx
def solution_publish(ctx: CLIContext, parameter_xml, xml_file):
    """Call PublishXml with a Publish Request Schema XML payload."""
    if parameter_xml and xml_file:
        ctx.emit(False, error="Provide --xml or --xml-file, not both.")
        return
    if xml_file:
        parameter_xml = Path(xml_file).read_text(encoding="utf-8")
    if not parameter_xml:
        ctx.emit(False, error="Either --xml or --xml-file is required.")
        return
    try:
        result = sol_mod.publish_xml(ctx.backend(), parameter_xml)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result or {"published": True})


@solution_group.command("job-status")
@click.argument("async_operation_id")
@pass_ctx
def solution_job_status(ctx: CLIContext, async_operation_id):
    """Alias for `crm async get <id>` — inspect a solution import/export job."""
    try:
        row = async_ops_mod.get_async_operation(ctx.backend(), async_operation_id)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=row)


@solution_group.command("job-cancel")
@click.argument("async_operation_id")
@click.confirmation_option(prompt="Cancel this job?")
@pass_ctx
def solution_job_cancel(ctx: CLIContext, async_operation_id):
    """Alias for `crm async cancel <id>`."""
    try:
        async_ops_mod.cancel_async_operation(ctx.backend(), async_operation_id)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data={"cancelled": True, "id": async_operation_id})


@solution_group.command("import")
@click.argument("zip_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--no-publish", is_flag=True)
@click.option("--no-overwrite", is_flag=True)
@click.option("--timeout", type=int, default=None,
              help="Async operation timeout in seconds. Overrides profile.async_timeout.")
@click.option("--no-retry", is_flag=True,
              help="Disable the 429/5xx retry loop for this invocation.")
@click.option("--quiet", "-q", is_flag=True,
              help="Suppress per-tick import-progress lines on stderr.")
@pass_ctx
def solution_import_cmd(ctx: CLIContext, zip_path, no_publish, no_overwrite, timeout, no_retry, quiet):
    with _no_retry_scope(ctx, no_retry):
        try:
            info = sol_mod.import_solution(
                ctx.backend(), zip_path,
                publish_workflows=not no_publish,
                overwrite_unmanaged_customizations=not no_overwrite,
                timeout=timeout,
                quiet=quiet,
            )
        except D365Error as exc:
            _handle_d365_error(ctx, exc)
            return
        ctx.emit(True, data=info)
