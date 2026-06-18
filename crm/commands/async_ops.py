"""Async-operations commands."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import async_ops as async_ops_mod
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _destructive_option,
    d365_errors,
    _confirm_destructive,
    _journal,
    _resolve_async_state,
)


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
@click.option("--order-by", default="createdon desc", help="OData $orderby expression (default: 'createdon desc').")
@click.option("--filter", default=None, help="Raw OData $filter expression to AND-join.")
@click.option("--all", "fetch_all", is_flag=True, default=False,
              help="Follow @odata.nextLink until exhausted (caps at --max-pages).")
@click.option("--max-pages", type=int, default=20,
              help="Safety cap on pagination depth when --all is set (default 20).")
@pass_ctx
def async_list(ctx: CLIContext, state, message_name, owner_id, top, order_by, filter, fetch_all, max_pages):
    """List asyncoperation rows."""
    with d365_errors(ctx):
        state_int = _resolve_async_state(state)
        backend = ctx.backend()
        if fetch_all:
            rows = async_ops_mod.list_all_async_operations(
                backend, state=state_int, message_name=message_name,
                owner_id=owner_id, page_size=top, max_pages=max_pages,
                order_by=order_by, filter=filter,
            )
        else:
            rows = async_ops_mod.list_async_operations(
                backend, state=state_int, message_name=message_name,
                owner_id=owner_id, top=top, order_by=order_by, filter=filter,
            )
    ctx.emit(True, data=rows, meta={"count": len(rows)})


@async_group.command("get")
@click.argument("async_operation_id")
@pass_ctx
def async_get(ctx: CLIContext, async_operation_id):
    """Get one asyncoperation row."""
    with d365_errors(ctx):
        row = async_ops_mod.get_async_operation(ctx.backend(), async_operation_id)
    ctx.emit(True, data=row)


@async_group.command("cancel")
@click.argument("async_operation_id")
@_destructive_option
@pass_ctx
def async_cancel(ctx: CLIContext, async_operation_id, yes):
    """Cancel a pending or suspended asyncoperation."""
    _confirm_destructive(ctx, "async job", async_operation_id, yes)
    with d365_errors(ctx):
        async_ops_mod.cancel_async_operation(ctx.backend(), async_operation_id)
    data = {"cancelled": True, "id": async_operation_id}
    ctx.emit(True, data=data)
    _journal(ctx, async_operation_id, data)
