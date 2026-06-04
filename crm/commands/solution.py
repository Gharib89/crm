"""Solution lifecycle commands."""
# pyright: basic
from __future__ import annotations
from pathlib import Path
import click
from crm.core import async_ops as async_ops_mod
from crm.core import solution as sol_mod
from crm.core import session as session_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error,
    _confirm_destructive,
    _no_retry_scope,
    _active_profile,
    _EXPORT_SETTING_KEYS,
)


@click.group("solution")
def solution_group():
    """Solution lifecycle (create-publisher / create / list / info / components / export / import)."""


def _autowire_profile(ctx: CLIContext, field: str, value: str, result: dict) -> None:
    """Write `field=value` back to the active NAMED profile after a successful create.

    Command-layer only (the core create functions stay pure). No-op under --dry-run
    or when no named profile is active (env/dotenv connection). Records the outcome
    in `result` so it surfaces in the emitted envelope.
    """
    if ctx.dry_run or result.get("_dry_run"):
        return
    profile = _active_profile(ctx)
    if profile is None:
        result["profile_update"] = "skipped: no named profile"
        return
    setattr(profile, field, value)
    session_mod.save_profile(profile)
    result["profile_updated"] = {"profile": profile.name, field: value}


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


@solution_group.command("create-publisher")
@click.option("--name", required=True, help="Publisher unique name, e.g. 'crmworx'.")
@click.option("--display", "display", default=None,
              help="Friendly name (defaults to --name).")
@click.option("--prefix", required=True,
              help="Customization prefix: 2-8 alphanumeric, starts with a letter, "
                   "not 'mscrm'. e.g. 'cwx'.")
@click.option("--option-value-prefix", "option_value_prefix", type=int, required=True,
              help="Option-value prefix (integer 10000-99999).")
@click.option("--if-exists", type=click.Choice(["error", "skip"]), default="error")
@click.option("--set-default/--no-set-default", default=True,
              help="Write publisher_prefix back to the active named profile (default on).")
@pass_ctx
def solution_create_publisher(ctx: CLIContext, name, display, prefix,
                              option_value_prefix, if_exists, set_default):
    """Create a solution publisher (publishers)."""
    try:
        info = sol_mod.create_publisher(
            ctx.backend(), name=name, friendly_name=display, prefix=prefix,
            option_value_prefix=option_value_prefix, if_exists=if_exists,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if set_default:
        _autowire_profile(ctx, "publisher_prefix", prefix, info)
    ctx.emit(True, data=info)


@solution_group.command("create")
@click.option("--name", required=True, help="Solution unique name, e.g. 'CRMWorx'.")
@click.option("--display", "display", default=None,
              help="Friendly name (defaults to --name).")
@click.option("--version", default="1.0.0.0", help="Solution version (default 1.0.0.0).")
@click.option("--publisher", "publisher", default=None,
              help="Publisher unique name (mutually exclusive with --publisher-id).")
@click.option("--publisher-id", "publisher_id", default=None,
              help="Publisher GUID (mutually exclusive with --publisher).")
@click.option("--if-exists", type=click.Choice(["error", "skip"]), default="error")
@click.option("--set-default/--no-set-default", default=True,
              help="Write default_solution back to the active named profile (default on).")
@pass_ctx
def solution_create(ctx: CLIContext, name, display, version, publisher,
                    publisher_id, if_exists, set_default):
    """Create an unmanaged solution bound to a publisher (solutions)."""
    if bool(publisher) == bool(publisher_id):
        ctx.emit(False, error="Provide exactly one of --publisher or --publisher-id.")
        return
    try:
        info = sol_mod.create_solution(
            ctx.backend(), name=name, friendly_name=display, version=version,
            publisher_unique_name=publisher, publisher_id=publisher_id,
            if_exists=if_exists,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if set_default:
        _autowire_profile(ctx, "default_solution", name, info)
    ctx.emit(True, data=info)


@solution_group.command("set-version")
@click.argument("unique_name")
@click.option("--version", default=None,
              help="New 4-part dotted version, e.g. 2.0.0.0.")
@click.option("--friendly-name", "friendly_name", default=None,
              help="New friendly (display) name.")
@click.option("--description", default=None, help="New description.")
@pass_ctx
def solution_set_version(ctx: CLIContext, unique_name, version, friendly_name, description):
    """Update an unmanaged solution's version / friendly name / description in place."""
    try:
        info = sol_mod.update_solution(
            ctx.backend(), unique_name,
            version=version, friendly_name=friendly_name, description=description,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


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
@click.option("--yes", is_flag=True, help="Skip interactive confirmation.")
@pass_ctx
def solution_job_cancel(ctx: CLIContext, async_operation_id, yes):
    """Alias for `crm async cancel <id>`."""
    if not _confirm_destructive("async job", async_operation_id, yes):
        ctx.emit(False, error="aborted by user")
        return
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
@click.option("--formatted", is_flag=True,
              help="Also fetch the Excel-format RetrieveFormattedImportJobResults "
                   "report and attach it verbatim under formatted_results.")
@click.option("--yes", is_flag=True,
              help="Skip the overwrite confirmation prompt.")
@pass_ctx
def solution_import_cmd(ctx: CLIContext, zip_path, no_publish, no_overwrite, timeout, no_retry, quiet, formatted, yes):
    # An overwrite import (the default) clobbers unmanaged customizations in the
    # target org — gate it like a delete (#67). A `--no-overwrite` import is not
    # prompted here (the PreToolUse hook still requires --yes for any import).
    if not no_overwrite and not _confirm_destructive(
        "solution", zip_path, yes,
        message=(f"Importing {zip_path!r} will OVERWRITE unmanaged customizations "
                 f"in the target org. Continue?"),
    ):
        ctx.emit(False, error="aborted by user")
        return
    with _no_retry_scope(ctx, no_retry):
        try:
            info = sol_mod.import_solution(
                ctx.backend(), zip_path,
                publish_workflows=not no_publish,
                overwrite_unmanaged_customizations=not no_overwrite,
                timeout=timeout,
                quiet=quiet,
                formatted=formatted,
            )
        except D365Error as exc:
            _handle_d365_error(ctx, exc)
            return
        warnings = info.pop("warnings", None)
        ctx.emit(True, data=info, warnings=warnings)


@solution_group.command("import-result")
@click.argument("import_job_id")
@click.option("--formatted", is_flag=True,
              help="Also fetch the Excel-format RetrieveFormattedImportJobResults "
                   "report and attach it verbatim under formatted_results.")
@pass_ctx
def solution_import_result_cmd(ctx: CLIContext, import_job_id, formatted):
    """Re-fetch a prior ImportJob and parse its per-component pass/fail results."""
    try:
        info = sol_mod.import_result(ctx.backend(), import_job_id, formatted=formatted)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    warnings = info.pop("warnings", None)
    ctx.emit(True, data=info, warnings=warnings)
