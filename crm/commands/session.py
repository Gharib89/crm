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


@session_group.command("audit")
@click.option("--tail", type=click.IntRange(min=1), default=None,
              help="Only the last N entries (N >= 1).")
@click.option("--session", "session_override", default=None,
              help="Read another session's journal (default: current --session).")
@pass_ctx
def session_audit(ctx: CLIContext, tail, session_override):
    """Show this session's audit journal of mutations."""
    from crm.core import audit
    name = session_override or ctx.session_name
    rows = audit.read(name, tail=tail)
    if ctx.json_mode:
        ctx.emit(True, data=rows, meta={"session": name, "count": len(rows)})
        return
    if not rows:
        ctx.skin.info("No audit entries.")
        return
    for r in rows:
        flags = []
        if r.get("dry_run"):
            flags.append("dry-run")
        if r.get("staged"):
            flags.append("staged")
        suffix = f" [{', '.join(flags)}]" if flags else ""
        click.echo(f"  {r.get('ts', '')}  {r.get('command', '')}  "
                   f"{r.get('target') or ''}  {r.get('result_id') or ''}{suffix}")
