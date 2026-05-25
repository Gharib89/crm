"""Session state commands."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import session as session_mod
from crm.cli import CLIContext, pass_ctx


@click.group("session")
def session_group():
    """Local session state."""


@session_group.command("info")
@pass_ctx
def session_info(ctx: CLIContext):
    state = session_mod.load_session(ctx.session_name)
    ctx.emit(True, data=state)


@session_group.command("clear")
@pass_ctx
def session_clear(ctx: CLIContext):
    state = {
        "name": ctx.session_name,
        "active_profile": None,
        "current_entity_set": None,
        "last_query": None,
        "history": [],
    }
    session_mod.save_session(state, ctx.session_name)
    ctx.emit(True, data={"cleared": True})


@session_group.command("history")
@pass_ctx
def session_history(ctx: CLIContext):
    state = session_mod.load_session(ctx.session_name)
    history = state.get("history", [])
    if ctx.json_mode:
        ctx.emit(True, data=history)
        return
    for i, line in enumerate(history[-50:], 1):
        click.echo(f"  {i:>3}  {line}")
