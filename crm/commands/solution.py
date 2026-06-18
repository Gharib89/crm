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
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _destructive_option,
    d365_errors,
    _confirm_destructive,
    _journal,
    _no_retry_scope,
    _active_profile,
    _EXPORT_SETTING_KEYS,
    _output_option,
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
    with d365_errors(ctx):
        items = sol_mod.list_solutions(ctx.backend(), managed=managed)
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
    with d365_errors(ctx):
        info = sol_mod.solution_info(ctx.backend(), unique_name)
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
    with d365_errors(ctx):
        info = dep_mod.retrieve_dependencies_for_uninstall(ctx.backend(), unique_name)
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

    with d365_errors(ctx):
        items = sol_mod.solution_components(ctx.backend(), unique_name)

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

    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["componenttype", "objectid", "rootcomponentbehavior"]
    rows = [[
        sol_mod.component_type_name(it.get("componenttype", 0)),
        it.get("objectid", ""),
        "" if it.get("rootcomponentbehavior") is None else it.get("rootcomponentbehavior"),
    ] for it in items]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta={"count": len(items)})


@solution_group.command("layer-conflicts")
@click.option("--solution", "managed_name", required=True,
              help="Managed solution unique name.")
@click.option("--unmanaged-solution", "unmanaged_name", required=True,
              help="Unmanaged solution unique name.")
@pass_ctx
def solution_layer_conflicts_cmd(ctx: CLIContext, managed_name, unmanaged_name):
    """Report components present in BOTH a managed and an unmanaged solution.

    Those are managed components that also carry unmanaged-layer customizations —
    the potential unmanaged-layer conflicts. Read-only; works identically on v9.x
    on-prem and Dataverse online. Matching is at solution-component granularity: a
    customized subcomponent (e.g. one attribute) of a whole-table managed component
    is its own component and will not show as a conflict.
    """
    with d365_errors(ctx):
        backend = ctx.backend()
        managed_info = sol_mod.solution_info(backend, managed_name)
        unmanaged_info = sol_mod.solution_info(backend, unmanaged_name)

    # Kind validation is data-dependent (`ismanaged` comes from the server), so a
    # mismatch is an in-command validation failure (exit 1, ADR 0001), not a Click
    # usage error. Check before fetching components / comparing.
    if not managed_info.get("ismanaged"):
        ctx.emit(False, error=f"--solution {managed_name!r} is not a managed solution.")
        return
    if unmanaged_info.get("ismanaged"):
        ctx.emit(False, error=f"--unmanaged-solution {unmanaged_name!r} is not an unmanaged solution.")
        return

    with d365_errors(ctx):
        managed_comps = sol_mod.solution_components(backend, managed_name)
        unmanaged_comps = sol_mod.solution_components(backend, unmanaged_name)
    conflicts = sol_mod.layer_conflicts(managed_comps, unmanaged_comps)

    meta = {"count": len(conflicts)}
    if ctx.json_mode:
        ctx.emit(True, data=conflicts, meta=meta)
        return
    if not conflicts:
        ctx.emit(True, data={"message": "no conflicts found"}, meta=meta)
        return
    headers = ["type", "type_name", "objectid", "managed_rcb", "unmanaged_rcb"]
    rows = [[str(c["componenttype"]), c["type_name"], c["objectid"],
             str(c["managed_rootcomponentbehavior"]),
             str(c["unmanaged_rootcomponentbehavior"])]
            for c in conflicts]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta=meta)


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
    with d365_errors(ctx):
        info = sol_mod.create_publisher(
            ctx.backend(), name=name, friendly_name=display, prefix=prefix,
            option_value_prefix=option_value_prefix, if_exists=if_exists,
        )
    if set_default:
        _autowire_profile(ctx, "publisher_prefix", prefix, info)
    ctx.emit(True, data=info)
    _journal(ctx, name, info)


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
    with d365_errors(ctx):
        info = sol_mod.create_solution(
            ctx.backend(), name=name, friendly_name=display, version=version,
            publisher_unique_name=publisher, publisher_id=publisher_id,
            if_exists=if_exists,
        )
    if set_default:
        _autowire_profile(ctx, "default_solution", name, info)
    ctx.emit(True, data=info)
    _journal(ctx, name, info)


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
    with d365_errors(ctx):
        info = sol_mod.update_solution(
            ctx.backend(), unique_name,
            version=version, friendly_name=friendly_name, description=description,
        )
    ctx.emit(True, data=info)
    _journal(ctx, unique_name, info)


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
    with d365_errors(ctx):
        component_type = sol_mod.resolve_component_type(type_)
        info = sol_mod.add_solution_component(
            ctx.backend(), solution=solution, component_id=component_id,
            component_type=component_type,
            add_required_components=not no_add_required,
            do_not_include_subcomponents=no_subcomponents,
        )
    meta = None
    if component_type == 1 and not no_add_required:  # entity + AddRequiredComponents
        meta = {"note": ("AddRequiredComponents was enabled: the server may have "
                         "silently added required components beyond the requested "
                         "entity; the response does not report them.")}
    ctx.emit(True, data=info, meta=meta)
    _journal(ctx, solution, info)


@solution_group.command("remove-component")
@click.option("--solution", required=True, help="Target unmanaged solution unique name.")
@click.option("--type", "type_", required=True,
              help="Component type: integer or friendly name (e.g. 61 or webresource).")
@click.option("--id", "component_id", required=True, metavar="GUID",
              help="Component GUID (objectid) to remove.")
@_destructive_option
@pass_ctx
def solution_remove_component(ctx: CLIContext, solution, type_, component_id, yes):
    """Remove a component from an unmanaged solution (RemoveSolutionComponent)."""
    _confirm_destructive(
        ctx, "component", f"{component_id} from solution {solution!r}", yes,
        message=(f"Removing component {component_id} from solution {solution!r}. Continue?"),
    )
    with d365_errors(ctx):
        component_type = sol_mod.resolve_component_type(type_)
        info = sol_mod.remove_solution_component(
            ctx.backend(), solution=solution, component_id=component_id,
            component_type=component_type,
        )
    ctx.emit(True, data=info)
    _journal(ctx, solution, info)


@solution_group.command("clone-as-patch")
@click.option("--solution", "parent_solution", required=True,
              help="Parent solution unique name to clone a patch from.")
@click.option("--display", "display", default=None,
              help="Patch display name (defaults to the parent's friendly name).")
@click.option("--version", default=None,
              help="Patch version (4-part dotted). Must share the parent's "
                   "major.minor; defaults to the parent version with the "
                   "revision bumped.")
@pass_ctx
def solution_clone_as_patch(ctx: CLIContext, parent_solution, display, version):
    """Create a solution patch from a parent solution (CloneAsPatch)."""
    with d365_errors(ctx):
        info = sol_mod.clone_as_patch(
            ctx.backend(), parent_solution=parent_solution,
            display_name=display, version=version,
        )
    ctx.emit(True, data=info)
    _journal(ctx, parent_solution, info)


@solution_group.command("uninstall")
@click.option("--solution", "unique_name", required=True,
              help="Unique name of the solution to uninstall.")
@click.option("--force", is_flag=True,
              help="Uninstall even if dependency blockers exist (skip the pre-check).")
@_destructive_option
@pass_ctx
def solution_uninstall(ctx: CLIContext, unique_name, force, yes):
    """Uninstall (delete) a solution (DELETE /solutions).

    Pre-checks RetrieveDependenciesForUninstall and refuses with the blocker
    list unless --force. For a managed base solution the server also uninstalls
    its patches.
    """
    _confirm_destructive(
        ctx, "solution", unique_name, yes,
        message=(f"Uninstalling solution {unique_name!r} removes it (and, for a "
                 f"managed base solution, all of its patches). Continue?"),
    )
    with d365_errors(ctx):
        info = sol_mod.uninstall_solution(ctx.backend(), unique_name, force=force)
    ctx.emit(True, data=info)
    _journal(ctx, unique_name, info)


@solution_group.command("stage-and-upgrade")
@click.argument("zip_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--promote", is_flag=True,
              help="After staging, apply the upgrade with DeleteAndPromote "
                   "(replaces the base solution). Requires --solution.")
@click.option("--solution", "solution_name", default=None,
              help="Unique name of the staged solution to promote "
                   "(required with --promote).")
@click.option("--no-publish", is_flag=True)
@click.option("--no-overwrite", is_flag=True)
@click.option("--timeout", type=int, default=None,
              help="Async operation timeout in seconds. Overrides profile.async_timeout.")
@click.option("--no-retry", is_flag=True,
              help="Disable the 429/5xx retry loop for this invocation.")
@click.option("--quiet", "-q", is_flag=True,
              help="Suppress per-tick import-progress lines on stderr.")
@click.option("--yes", is_flag=True, help="Skip the staging/promote confirmation prompt.")
@pass_ctx
def solution_stage_and_upgrade_cmd(ctx: CLIContext, zip_path, promote, solution_name,
                                   no_publish, no_overwrite, timeout, no_retry, quiet, yes):
    """Stage a managed-solution upgrade as a holding solution (ImportSolution HoldingSolution).

    Stages only by default; pass --promote (with --solution) to also apply the
    upgrade via DeleteAndPromote, replacing the base solution.
    """
    # --promote needs an explicit target (usage error, exit 2 — ADR 0001).
    if promote and not solution_name:
        raise click.UsageError("--promote requires --solution <unique name>.")

    action = (f"Staging {zip_path!r} as a holding solution and promoting it over "
              f"{solution_name!r} (DeleteAndPromote replaces the base solution)."
              if promote else
              f"Staging {zip_path!r} as a holding solution for upgrade.")
    _confirm_destructive(ctx, "solution", zip_path, yes,
                         message=f"{action} Continue?")

    with _no_retry_scope(ctx, no_retry):
        with d365_errors(ctx):
            info = sol_mod.import_solution(
                ctx.backend(), zip_path,
                publish_workflows=not no_publish,
                overwrite_unmanaged_customizations=not no_overwrite,
                holding_solution=True,
                timeout=timeout,
                quiet=quiet,
            )
            # Promote only a real, succeeded stage — never under --dry-run.
            if promote and not info.get("_dry_run"):
                info["promote"] = sol_mod.delete_and_promote(ctx.backend(), solution_name)
        warnings = info.pop("warnings", None)
        ctx.emit(True, data=info, warnings=warnings)
        _journal(ctx, zip_path, info)


@solution_group.command("apply-upgrade")
@click.argument("unique_name")
@_destructive_option
@pass_ctx
def solution_apply_upgrade_cmd(ctx: CLIContext, unique_name, yes):
    """Apply a previously-staged holding-solution upgrade (DeleteAndPromote).

    Promotes a solution already staged via `stage-and-upgrade` (run without
    --promote), replacing the base solution and deleting its patches. This is
    the separate-promote path that decouples stage-time from promote-time;
    `stage-and-upgrade --promote` remains the one-shot path.
    """
    _confirm_destructive(
        ctx, "solution", unique_name, yes,
        message=(f"Promoting the staged upgrade for solution {unique_name!r} via "
                 f"DeleteAndPromote (replaces the base solution and deletes its "
                 f"patches). Continue?"),
    )
    with d365_errors(ctx):
        info = sol_mod.delete_and_promote(ctx.backend(), unique_name)
    ctx.emit(True, data=info)
    _journal(ctx, unique_name, info)


@solution_group.command("export")
@click.argument("unique_name")
@_output_option(required=True)
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
        with d365_errors(ctx):
            info = sol_mod.export_solution(
                ctx.backend(), unique_name, output, managed=managed,
                timeout=timeout, **kwargs,
            )
        ctx.emit(True, data=info)


@solution_group.command("publish-all")
@pass_ctx
def solution_publish_all(ctx: CLIContext):
    """Call PublishAllXml — publish every unpublished customization."""
    with d365_errors(ctx):
        result = sol_mod.publish_all(ctx.backend())
    data = result or {"published": True}
    ctx.emit(True, data=data)
    _journal(ctx, None, data)


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
    with d365_errors(ctx):
        result = sol_mod.publish_xml(ctx.backend(), parameter_xml)
    data = result or {"published": True}
    ctx.emit(True, data=data)
    _journal(ctx, None, data)


@solution_group.command("job-status")
@click.argument("async_operation_id")
@pass_ctx
def solution_job_status(ctx: CLIContext, async_operation_id):
    """Alias for `crm async get <id>` — inspect a solution import/export job."""
    with d365_errors(ctx):
        row = async_ops_mod.get_async_operation(ctx.backend(), async_operation_id)
    ctx.emit(True, data=row)


@solution_group.command("job-cancel")
@click.argument("async_operation_id")
@_destructive_option
@pass_ctx
def solution_job_cancel(ctx: CLIContext, async_operation_id, yes):
    """Alias for `crm async cancel <id>`."""
    _confirm_destructive(ctx, "async job", async_operation_id, yes)
    with d365_errors(ctx):
        async_ops_mod.cancel_async_operation(ctx.backend(), async_operation_id)
    data = {"cancelled": True, "id": async_operation_id}
    ctx.emit(True, data=data)
    _journal(ctx, async_operation_id, data)


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
    if not no_overwrite:
        _confirm_destructive(
            ctx, "solution", zip_path, yes,
            message=(f"Importing {zip_path!r} will OVERWRITE unmanaged customizations "
                     f"in the target org. Continue?"),
        )
    with _no_retry_scope(ctx, no_retry):
        with d365_errors(ctx):
            info = sol_mod.import_solution(
                ctx.backend(), zip_path,
                publish_workflows=not no_publish,
                overwrite_unmanaged_customizations=not no_overwrite,
                timeout=timeout,
                quiet=quiet,
                formatted=formatted,
            )
        warnings = info.pop("warnings", None)
        ctx.emit(True, data=info, warnings=warnings)
        _journal(ctx, zip_path, info)


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
    with d365_errors(ctx):
        info = sp_mod.extract_solution(
            zipfile=zipfile, folder=folder, package_type=package_type,
            solutionpackager_path=solutionpackager_path, timeout=timeout,
        )
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
    with d365_errors(ctx):
        info = sp_mod.pack_solution(
            zipfile=zipfile, folder=folder, package_type=package_type,
            solutionpackager_path=solutionpackager_path, timeout=timeout,
        )
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
    with d365_errors(ctx):
        report = sv_mod.validate_solution(zip_path, backend=backend)
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
    with d365_errors(ctx):
        info = sol_mod.import_result(ctx.backend(), import_job_id, formatted=formatted)
    warnings = info.pop("warnings", None)
    ctx.emit(True, data=info, warnings=warnings)
