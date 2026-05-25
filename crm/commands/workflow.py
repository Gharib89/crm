"""Workflow commands."""
from __future__ import annotations
import click
from crm.core import workflow as workflow_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error,
    _admin_header_options,
    _admin_kwargs,
)


@click.group("workflow")
def workflow_group():
    """List, activate, and trigger D365 workflows."""


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
def workflow_activate(ctx: CLIContext, workflow_id, as_user, suppress_dup_detection, bypass_plugins):
    """Activate a workflow (statecode=1, statuscode=2)."""
    try:
        info = workflow_mod.set_workflow_state(
            ctx.backend(), workflow_id, activate=True,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@workflow_group.command("deactivate")
@click.argument("workflow_id")
@_admin_header_options
@pass_ctx
def workflow_deactivate(ctx: CLIContext, workflow_id, as_user, suppress_dup_detection, bypass_plugins):
    """Deactivate a workflow (statecode=0, statuscode=1)."""
    try:
        info = workflow_mod.set_workflow_state(
            ctx.backend(), workflow_id, activate=False,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@workflow_group.command("run")
@click.argument("workflow_id")
@click.option("--target", "target_record_id", required=True,
              help="GUID of the record to run the workflow against.")
@_admin_header_options
@pass_ctx
def workflow_run(ctx: CLIContext, workflow_id, target_record_id,
                 as_user, suppress_dup_detection, bypass_plugins):
    """Trigger an on-demand workflow against a target record via ExecuteWorkflow."""
    try:
        info = workflow_mod.execute_workflow(
            ctx.backend(), workflow_id, target_record_id,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
