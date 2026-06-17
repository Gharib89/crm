"""OData function and action commands."""
# pyright: basic
from __future__ import annotations
import json
import click
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import d365_errors, _journal, _load_payload, _odata_literal


@click.group("action")
def action_group():
    """Invoke OData functions and actions (unbound or bound)."""


@action_group.command("function")
@click.argument("name")
@click.option("--params", "params_json", help='JSON dict of function parameters.')
@click.option(
    "--bind-set",
    help="Entity set to bind the function to (e.g. 'systemusers'). "
    "Alone → collection-bound; with --bind-id → record-bound.",
)
@click.option(
    "--bind-id",
    help="Record id to bind the function to. Requires --bind-set.",
)
@click.option(
    "--cast",
    default="Microsoft.Dynamics.CRM",
    show_default=True,
    help="Namespace for the function when bound. Override only for custom namespaces.",
)
@pass_ctx
def action_function(ctx: CLIContext, name, params_json, bind_set, bind_id, cast):
    """Call an OData function — unbound by default, bound when --bind-set is given.

    Functions issue a GET. Params are encoded inline per OData v4. --bind-set
    alone binds to a collection (set/Ns.Fn(...)); adding --bind-id binds to a
    single record (set(id)/Ns.Fn(...)). --bind-id requires --bind-set.
    """
    if bind_id and not bind_set:
        ctx.emit(False, error="--bind-id requires --bind-set.")
        return
    backend = ctx.backend() if not ctx.dry_run else None
    params = json.loads(params_json) if params_json else None
    encoded = (
        ",".join(f"{k}={_odata_literal(v)}" for k, v in params.items()) if params else ""
    )
    call = f"{name}({encoded})"
    if bind_set and bind_id:
        path = f"{bind_set}({bind_id})/{cast}.{call}"
    elif bind_set:
        path = f"{bind_set}/{cast}.{call}"
    else:
        path = call
    with d365_errors(ctx):
        result = (backend or ctx.backend()).get(path)
    ctx.emit(True, data=result or {})


@action_group.command("invoke")
@click.argument("name")
@click.option("--body", "body_json", help="JSON body for the action.")
@click.option("--body-file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--bind-set",
    help="Entity set name to bind the action to (e.g. 'workflows'). Requires --bind-id.",
)
@click.option(
    "--bind-id",
    help="Record id to bind the action to. Requires --bind-set.",
)
@click.option(
    "--cast",
    default="Microsoft.Dynamics.CRM",
    show_default=True,
    help="Namespace for the action when bound. Override only for custom namespaces.",
)
@pass_ctx
def action_invoke(ctx: CLIContext, name, body_json, body_file, bind_set, bind_id, cast):
    """POST an OData action — unbound by default, bound when --bind-set/--bind-id given."""
    if bool(bind_set) ^ bool(bind_id):
        ctx.emit(False, error="--bind-set and --bind-id must be used together.")
        return
    payload = _load_payload(body_json, body_file) if (body_json or body_file) else {}
    if bind_set and bind_id:
        path = f"{bind_set}({bind_id})/{cast}.{name}"
    else:
        path = name
    with d365_errors(ctx):
        result = ctx.backend().post(path, json_body=payload)
    data = result or {}
    ctx.emit(True, data=data)
    _journal(ctx, name, data)
