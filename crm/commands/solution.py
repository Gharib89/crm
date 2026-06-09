"""Solution lifecycle commands."""
# pyright: basic
from __future__ import annotations
from pathlib import Path
import json
import click
from crm.core import async_ops as async_ops_mod
from crm.core import dependencies as dep_mod
from crm.core import solution as sol_mod
from crm.core import solution_validate as sv_mod
from crm.core import solutionpackager as sp_mod
from crm.core import session as session_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error,
    _confirm_destructive,
    _journal,
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


@solution_group.command("dependencies")
@click.argument("unique_name")
@pass_ctx
def solution_dependencies_cmd(ctx: CLIContext, unique_name):
    """Show blockers that would prevent UNINSTALLING a managed solution.

    Read-only. Calls RetrieveDependenciesForUninstall(SolutionUniqueName='<name>').
    """
    # An empty/blank name is a caller mistake (usage error, exit 2 — ADR 0001),
    # not an operational failure; validate before any network call.
    if not unique_name.strip():
        raise click.UsageError("solution unique name is required.")
    try:
        info = dep_mod.retrieve_dependencies_for_uninstall(ctx.backend(), unique_name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    meta = {"blockers": info["count"]}
    if ctx.json_mode:
        ctx.emit(True, data=info, meta=meta)
        return
    if info["blockers"]:
        headers = ["Dependent Type", "Dependent Id", "Required Type", "Dependency Type"]
        rows = [
            [b["dependent_type"], b["dependent_id"], b["required_type"], str(b["dependency_type"])]
            for b in info["blockers"]
        ]
        ctx.emit(True, table={"headers": headers, "rows": rows}, meta=meta)
    else:
        # Emit the scalar under `count` (not `blockers`) so the `blockers` key is
        # never an int here while it's a list in JSON mode (Copilot #135).
        ctx.emit(True, data={"solution": info["solution"], "count": 0}, meta=meta)


@solution_group.command("components")
@click.argument("unique_name")
@click.option("--diff", "diff_path", default=None,
              type=click.Path(exists=True, dir_okay=False, readable=True),
              help="Compare live components against this saved JSON snapshot; exits non-zero on drift.")
@click.option("--save", "save_path", default=None,
              type=click.Path(dir_okay=False),
              help="Write a normalized component inventory to this path as JSON.")
@pass_ctx
def solution_components_cmd(ctx: CLIContext, unique_name, diff_path, save_path):
    """List solution components; with --save write a normalized inventory, with --diff compare live vs expected (non-zero exit on drift)."""
    # A caller mistake (invalid flag combination) is a usage error (exit 2,
    # ADR 0001), not an operational failure — mirror entity update's pattern.
    if diff_path and save_path:
        raise click.UsageError("--diff and --save are mutually exclusive.")

    # Parse and validate the expected snapshot BEFORE any network call, so a
    # malformed --diff file fails fast without touching the org.
    expected: list | None = None
    if diff_path:
        try:
            text = Path(diff_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            ctx.emit(False, error=f"Could not read {diff_path!r}: {exc}")
            return
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            ctx.emit(False, error=f"Could not parse {diff_path!r} as JSON: {exc}")
            return
        if not isinstance(raw, list):
            ctx.emit(False, error=f"Expected a JSON list in {diff_path!r}, got {type(raw).__name__}.")
            return
        expected = raw

    try:
        items = sol_mod.solution_components(ctx.backend(), unique_name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return

    if save_path:
        normalized = sol_mod.normalize_components(items)
        out = Path(save_path)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
        except OSError as exc:
            ctx.emit(False, error=f"Could not write {save_path}: {exc}")
            return
        ctx.emit(True, data={"saved": str(out), "count": len(normalized)})
        return

    if diff_path:
        try:
            result = sol_mod.diff_components(items, expected or [])
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            ctx.emit(False, error=f"Malformed component row in {diff_path!r}: {exc}")
            return
        if not result["matches"]:
            msg = (f"Drift detected: {len(result['missing'])} missing, "
                   f"{len(result['unexpected'])} unexpected component(s).")
            ctx.emit(False, data=result, error=msg)
            return
        ctx.emit(True, data=result, meta={"matches": True})
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
    _journal(ctx, "solution create-publisher", name, info)


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
    _journal(ctx, "solution create", name, info)


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
    _journal(ctx, "solution set-version", unique_name, info)


@solution_group.command("add-component")
@click.option("--solution", required=True, help="Target unmanaged solution unique name.")
@click.option("--type", "type_", required=True,
              help="Component type: integer or friendly name (e.g. 61 or webresource).")
@click.option("--id", "component_id", required=True, metavar="GUID",
              help="Component GUID (objectid) to add.")
@click.option("--no-add-required", is_flag=True,
              help="Do not also add required components (AddRequiredComponents: false).")
@click.option("--no-subcomponents", is_flag=True,
              help="Exclude subcomponents (DoNotIncludeSubcomponents: true).")
@pass_ctx
def solution_add_component(ctx: CLIContext, solution, type_, component_id,
                           no_add_required, no_subcomponents):
    """Add an existing component to an unmanaged solution (AddSolutionComponent)."""
    try:
        component_type = sol_mod.resolve_component_type(type_)
        info = sol_mod.add_solution_component(
            ctx.backend(), solution=solution, component_id=component_id,
            component_type=component_type,
            add_required_components=not no_add_required,
            do_not_include_subcomponents=no_subcomponents,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
    _journal(ctx, "solution add-component", solution, info)


@solution_group.command("remove-component")
@click.option("--solution", required=True, help="Target unmanaged solution unique name.")
@click.option("--type", "type_", required=True,
              help="Component type: integer or friendly name (e.g. 61 or webresource).")
@click.option("--id", "component_id", required=True, metavar="GUID",
              help="Component GUID (objectid) to remove.")
@click.option("--yes", is_flag=True, help="Skip interactive confirmation.")
@pass_ctx
def solution_remove_component(ctx: CLIContext, solution, type_, component_id, yes):
    """Remove a component from an unmanaged solution (RemoveSolutionComponent)."""
    if not _confirm_destructive(
        "component", f"{component_id} from solution {solution!r}", yes,
        message=(f"Removing component {component_id} from solution {solution!r}. Continue?"),
    ):
        ctx.emit(False, error="aborted by user")
        return
    try:
        component_type = sol_mod.resolve_component_type(type_)
        info = sol_mod.remove_solution_component(
            ctx.backend(), solution=solution, component_id=component_id,
            component_type=component_type,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
    _journal(ctx, "solution remove-component", solution, info)


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
    data = result or {"published": True}
    ctx.emit(True, data=data)
    _journal(ctx, "solution publish-all", None, data)


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
    data = result or {"published": True}
    ctx.emit(True, data=data)
    _journal(ctx, "solution publish", None, data)


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
    data = {"cancelled": True, "id": async_operation_id}
    ctx.emit(True, data=data)
    _journal(ctx, "solution job-cancel", async_operation_id, data)


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
        _journal(ctx, "solution import", zip_path, info)


def _emit_packager_result(ctx: CLIContext, info: dict) -> None:
    """Emit a SolutionPackager envelope, failing the command (ADR 0001) when the
    tool returned a non-zero exit code — the data (exit_code, stdout_tail) is kept
    so the failure is diagnosable."""
    exit_code = info.get("exit_code")
    if exit_code:
        # Embed the tail in the error itself: human mode drops `data`, so a bare
        # "see stdout_tail" would point at output the user can't see (#107 review).
        tail = info.get("stdout_tail") or ""
        msg = f"SolutionPackager {info.get('action')} failed (exit {exit_code})."
        if tail:
            msg += f"\n{tail}"
        ctx.emit(False, data=info, error=msg)
        return
    ctx.emit(True, data=info)


@solution_group.command("extract")
@click.option("--zipfile", required=True, type=click.Path(exists=True, dir_okay=False),
              help="Exported solution zip to unpack.")
@click.option("--folder", required=True, type=click.Path(file_okay=False),
              help="Destination folder for the source-controllable tree.")
@click.option("--package-type", "package_type",
              type=click.Choice(["Unmanaged", "Managed", "Both"], case_sensitive=False),
              default="Unmanaged",
              help="SolutionPackager /packagetype (default Unmanaged).")
@click.option("--solutionpackager-path", "solutionpackager_path", default=None,
              type=click.Path(dir_okay=False),
              help="Path to SolutionPackager.exe (else CRM_SOLUTIONPACKAGER env, then PATH).")
@click.option("--timeout", type=int, default=None,
              help="SolutionPackager subprocess timeout in seconds.")
@pass_ctx
def solution_extract_cmd(ctx: CLIContext, zipfile, folder, package_type,
                         solutionpackager_path, timeout):
    """Extract a solution zip into a folder tree (offline; SolutionPackager.exe).

    OFFLINE local-file transform — no connection or profile required.
    """
    try:
        info = sp_mod.extract_solution(
            zipfile=zipfile, folder=folder, package_type=package_type,
            solutionpackager_path=solutionpackager_path, timeout=timeout,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_packager_result(ctx, info)


@solution_group.command("pack")
@click.option("--zipfile", required=True, type=click.Path(dir_okay=False),
              help="Destination solution zip to build.")
@click.option("--folder", required=True, type=click.Path(exists=True, file_okay=False),
              help="Source folder tree to pack.")
@click.option("--package-type", "package_type",
              type=click.Choice(["Unmanaged", "Managed", "Both"], case_sensitive=False),
              default="Unmanaged",
              help="SolutionPackager /packagetype (default Unmanaged).")
@click.option("--solutionpackager-path", "solutionpackager_path", default=None,
              type=click.Path(dir_okay=False),
              help="Path to SolutionPackager.exe (else CRM_SOLUTIONPACKAGER env, then PATH).")
@click.option("--timeout", type=int, default=None,
              help="SolutionPackager subprocess timeout in seconds.")
@pass_ctx
def solution_pack_cmd(ctx: CLIContext, zipfile, folder, package_type,
                      solutionpackager_path, timeout):
    """Pack a folder tree back into a solution zip (offline; SolutionPackager.exe).

    OFFLINE local-file transform — no connection or profile required.
    """
    try:
        info = sp_mod.pack_solution(
            zipfile=zipfile, folder=folder, package_type=package_type,
            solutionpackager_path=solutionpackager_path, timeout=timeout,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_packager_result(ctx, info)


@solution_group.command("validate")
@click.argument("zip_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--against-org", "against_org", is_flag=True,
              help="Also run online checks against the connected org "
                   "(form/view + BPF process-stage GUID collisions, web-resource "
                   "& option-set existence). Requires a connection/profile.")
@pass_ctx
def solution_validate_cmd(ctx: CLIContext, zip_path, against_org):
    """Statically validate a solution zip before import.

    OFFLINE by default -- no connection or profile required. --against-org adds
    online checks (GUID collisions, web-resource & option-set existence). Exits
    non-zero when any error-severity problem is found.
    """
    backend = ctx.backend() if against_org else None
    try:
        report = sv_mod.validate_solution(zip_path, backend=backend)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if report["valid"]:
        ctx.emit(True, data=report)
        return
    n = sum(1 for f in report["findings"] if f["severity"] == "error")
    ctx.emit(False, data=report, error=f"{n} validation error(s) found")


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
