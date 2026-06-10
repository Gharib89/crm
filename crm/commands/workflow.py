"""Workflow commands."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import workflow as workflow_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _confirm_destructive,
    _handle_d365_error,
    _admin_header_options,
    _admin_kwargs,
    _journal,
)


@click.group("workflow")
def workflow_group():
    """List, activate, and trigger D365 workflows."""


def _redirect_note_meta(info: dict) -> dict | None:
    """Meta carrying the activation-record redirect note, or None when the
    state change ran against the GUID that was passed. The note travels via
    meta (not gated on json_mode) so it renders in human mode too."""
    resolved_from = info.get("resolved_from_activation_id")
    if not resolved_from:
        return None
    return {"note": (
        f"Operated on parent definition {info['workflow_id']}; "
        f"activation-record GUID {resolved_from} was passed."
    )}


@workflow_group.command("list")
@click.option("--category", type=int, help="Filter by category (0=Workflow, 4=BPF, 5=Modern Flow).")
@click.option("--entity", "primary_entity", help="Filter by primary entity logical name.")
@click.option("--activated/--all", "activated_only", default=False,
              help="Restrict to activated workflows. Default returns all states.")
@click.option("--on-demand", "on_demand_only", is_flag=True, default=False,
              help="Only on-demand workflows.")
@pass_ctx
def workflow_list(ctx: CLIContext, category, primary_entity, activated_only, on_demand_only):
    """List workflow definitions."""
    try:
        items = workflow_mod.list_workflows(
            ctx.backend(),
            category=category,
            primary_entity=primary_entity,
            activated_only=activated_only,
            on_demand_only=on_demand_only,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=items, meta={"count": len(items)})


@workflow_group.command("activate")
@click.argument("workflow_id")
@_admin_header_options
@pass_ctx
def workflow_activate(ctx: CLIContext, workflow_id, as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Activate a workflow (statecode=1, statuscode=2).

    An activation-record GUID (type=2) is resolved to its parent definition
    automatically and the state change is applied to the parent.
    """
    try:
        info = workflow_mod.set_workflow_state(
            ctx.backend(), workflow_id, activate=True,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        # Resolve the activation-record hint only for the code the PATCH raises;
        # gating on the code keeps ctx.backend() (cached after a successful PATCH)
        # off the path where the original error came from building the backend.
        hint = (workflow_mod.activation_record_hint(ctx.backend(), workflow_id, exc)
                if exc.code == workflow_mod.ACTIVATION_PATCH_ERROR_CODE else None)
        _handle_d365_error(ctx, exc, hint=hint)
        return
    ctx.emit(True, data=info, meta=_redirect_note_meta(info))
    _journal(ctx, "workflow activate", workflow_id, info)


@workflow_group.command("deactivate")
@click.argument("workflow_id")
@_admin_header_options
@pass_ctx
def workflow_deactivate(ctx: CLIContext, workflow_id, as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Deactivate a workflow (statecode=0, statuscode=1).

    An activation-record GUID (type=2) is resolved to its parent definition
    automatically and the state change is applied to the parent.
    """
    try:
        info = workflow_mod.set_workflow_state(
            ctx.backend(), workflow_id, activate=False,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        # Resolve the activation-record hint only for the code the PATCH raises;
        # gating on the code keeps ctx.backend() (cached after a successful PATCH)
        # off the path where the original error came from building the backend.
        hint = (workflow_mod.activation_record_hint(ctx.backend(), workflow_id, exc)
                if exc.code == workflow_mod.ACTIVATION_PATCH_ERROR_CODE else None)
        _handle_d365_error(ctx, exc, hint=hint)
        return
    ctx.emit(True, data=info, meta=_redirect_note_meta(info))
    _journal(ctx, "workflow deactivate", workflow_id, info)


@workflow_group.command("delete")
@click.argument("workflow_id")
@click.option("--yes", is_flag=True, help="Skip interactive confirmation.")
@_admin_header_options
@pass_ctx
def workflow_delete(ctx: CLIContext, workflow_id, yes,
                    as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Delete a workflow definition, deactivating it first if active.

    An activation-record GUID (type=2) is resolved to its parent definition;
    deleting the definition removes the activation record server-side. Not
    atomic: if the deactivate lands but the delete fails, the definition
    remains a draft (no rollback).
    """
    admin = _admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins)
    try:
        target = workflow_mod.resolve_delete_target(
            ctx.backend(), workflow_id,
            caller_id=admin["caller_id"],
            caller_object_id=admin["caller_object_id"],
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    # The prompt must name the resolved target: with an activation-record GUID
    # the verb deletes a *different record* (the parent definition). desc is
    # plain — _confirm_destructive's default prompt applies !r itself, and the
    # custom message below adds !r explicitly so both paths render identically.
    desc = f"{target['name'] or '<unnamed>'} ({target['workflow_id']})"
    message = None
    if target["resolved_from_activation_id"]:
        message = (
            f"This will permanently delete workflow definition {desc!r} — you "
            f"passed its activation record {workflow_id}, which the server "
            "removes with the definition. Continue?"
        )
    if not _confirm_destructive("workflow definition", desc, yes, message=message):
        ctx.emit(False, error="aborted by user")
        return
    try:
        info = workflow_mod.delete_workflow(
            ctx.backend(), workflow_id, resolved=target, **admin)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info, meta=_redirect_note_meta(info))
    _journal(ctx, "workflow delete", workflow_id, info)


@workflow_group.command("run")
@click.argument("workflow_id")
@click.option("--target", "target_record_id", required=True,
              help="GUID of the record to run the workflow against.")
@_admin_header_options
@pass_ctx
def workflow_run(ctx: CLIContext, workflow_id, target_record_id,
                 as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Trigger an on-demand workflow against a target record via ExecuteWorkflow."""
    try:
        info = workflow_mod.execute_workflow(
            ctx.backend(), workflow_id, target_record_id,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
    _journal(ctx, "workflow run", workflow_id, info)


@workflow_group.command("clone")
@click.argument("workflow_id")
@click.option("--to-entity", "target_entity", required=True,
              help="Logical name of the entity to clone the workflow onto.")
@click.option("--name", default=None, help="Name for the clone. Default: '<source> (Clone)'.")
@click.option("--activate/--no-activate", default=True,
              help="Activate the clone after creating it (compiles the xaml). Default: activate.")
@click.option("--solution", default=None, help="Add the clone to this unmanaged solution.")
@_admin_header_options
@pass_ctx
def workflow_clone(ctx: CLIContext, workflow_id, target_entity, name, activate, solution,
                   as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Clone a workflow definition onto another entity (xaml-retargeted)."""
    try:
        info = workflow_mod.clone_workflow_to_entity(
            ctx.backend(), workflow_id, target_entity,
            name=name, activate=activate, solution=solution,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
    _journal(ctx, "workflow clone", workflow_id, info)


@workflow_group.command("export")
@click.argument("workflow_id")
@click.option("--out", "out_path", default=None,
              type=click.Path(file_okay=True, dir_okay=False),
              help="Write the workflow definition to this JSON file. Default: stdout only.")
@pass_ctx
def workflow_export(ctx: CLIContext, workflow_id, out_path):
    """Export a workflow definition (incl. xaml) to a JSON file."""
    try:
        info = workflow_mod.export_workflow(ctx.backend(), workflow_id, out_path=out_path)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@workflow_group.command("import")
@click.option("--file", "file_path", required=True,
              type=click.Path(exists=True, file_okay=True, dir_okay=False),
              help="Exported workflow JSON file to upsert.")
@click.option("--activate/--no-activate", default=False,
              help="Activate after import. Default: leave as draft.")
@_admin_header_options
@pass_ctx
def workflow_import(ctx: CLIContext, file_path, activate,
                    as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Import (upsert) a workflow definition from an exported JSON file."""
    try:
        info = workflow_mod.import_workflow(
            ctx.backend(), file_path=file_path, activate=activate,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
    _journal(ctx, "workflow import", info.get("workflow_id", ""), info)
