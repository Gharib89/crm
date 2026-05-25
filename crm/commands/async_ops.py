"""Async-operations commands."""
from __future__ import annotations
import click
from crm.core import async_ops as async_ops_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import _handle_d365_error, _resolve_async_state


@click.group("async")
def async_group():
    """List, inspect, and cancel asynchronous operations."""


@async_group.command("list")
@click.option("--state", default=None,
              help="ready | suspended | locked | completed | <int>")
@click.option("--message", "message_name", default=None,
              help="Filter by messagename (e.g. ImportSolution).")
@click.option("--owner", "owner_id", default=None,
              help="Filter by systemuser GUID.")
@click.option("--top", type=int, default=50, help="Page size per call (default 50).")
@click.option("--all", "fetch_all", is_flag=True, default=False,
              help="Follow @odata.nextLink until exhausted (caps at --max-pages).")
@click.option("--max-pages", type=int, default=20,
              help="Safety cap on pagination depth when --all is set (default 20).")
@pass_ctx
def async_list(ctx: CLIContext, state, message_name, owner_id, top, fetch_all, max_pages):
    """List asyncoperation rows."""
    try:
        state_int = _resolve_async_state(state)
        backend = ctx.backend()
        if fetch_all:
            rows = async_ops_mod.list_all_async_operations(
                backend, state=state_int, message_name=message_name,
                owner_id=owner_id, page_size=top, max_pages=max_pages,
            )
        else:
            rows = async_ops_mod.list_async_operations(
                backend, state=state_int, message_name=message_name,
                owner_id=owner_id, top=top,
            )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=rows, meta={"count": len(rows)})


@async_group.command("get")
@click.argument("async_operation_id")
@pass_ctx
def async_get(ctx: CLIContext, async_operation_id):
    """Get one asyncoperation row."""
    try:
        row = async_ops_mod.get_async_operation(ctx.backend(), async_operation_id)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=row)


@async_group.command("cancel")
@click.argument("async_operation_id")
@click.confirmation_option(prompt="Cancel this async operation?")
@pass_ctx
def async_cancel(ctx: CLIContext, async_operation_id):
    """Cancel a pending or suspended asyncoperation."""
    try:
        async_ops_mod.cancel_async_operation(ctx.backend(), async_operation_id)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data={"cancelled": True, "id": async_operation_id})
