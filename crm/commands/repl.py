"""REPL command."""
from __future__ import annotations
import shlex
import click
from crm.core import session as session_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx


@click.command("repl")
@pass_ctx
def repl(ctx: CLIContext):
    """Interactive REPL (default when no subcommand is provided)."""
    from crm.cli import cli
    ctx.skin.print_banner()
    ctx.skin.info(f"Session: {ctx.session_name}  |  Type 'help' for commands, 'quit' to exit.")
    pt_session = ctx.skin.create_prompt_session()
    state = session_mod.load_session(ctx.session_name)

    while True:
        try:
            profile_label = state.get("active_profile") or "<no profile>"
            line = ctx.skin.get_input(
                pt_session, project_name=profile_label,
                modified=bool(state.get("last_query")),
            )
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        cmd = line.strip()
        if cmd in ("quit", "exit", ":q"):
            break
        if cmd in ("help", "?"):
            _repl_help(ctx)
            continue
        if cmd == "clear":
            click.clear()
            continue

        session_mod.append_history(state, cmd)
        try:
            argv = shlex.split(cmd)
        except ValueError as exc:
            ctx.skin.error(f"Parse error: {exc}")
            continue
        try:
            cli.main(args=argv, obj=ctx, standalone_mode=False, prog_name="crm")
        except SystemExit:
            pass
        except click.ClickException as exc:
            ctx.skin.error(exc.format_message())
        except D365Error as exc:
            ctx.skin.error(str(exc))
        except Exception as exc:  # noqa: BLE001 — REPL must keep running
            ctx.skin.error(f"{type(exc).__name__}: {exc}")
        state = session_mod.load_session(ctx.session_name)
        session_mod.save_session(state, ctx.session_name)

    session_mod.save_session(state, ctx.session_name)
    ctx.skin.print_goodbye()


def _repl_help(ctx: CLIContext):
    ctx.skin.help({
        "connection connect": "Save profile and verify with WhoAmI",
        "connection status": "Show active session/profile",
        "connection whoami": "Issue WhoAmI() against the server",
        "entity get <set> <id>": "GET a record",
        "entity create <set> --data '{...}'": "POST a new record",
        "entity update <set> <id> --data '{...}'": "PATCH a record",
        "entity delete <set> <id>": "DELETE a record",
        "query odata <set> [--filter ...] [--top N]": "OData query",
        "query fetchxml <set> --xml '<fetch>...</fetch>'": "FetchXML query",
        "metadata entities": "List entity definitions",
        "metadata attributes <entity>": "List attributes",
        "metadata add-attribute <entity> --kind <k>": "Add a column to an entity",
        "metadata create-entity / delete-entity": "Custom entity lifecycle",
        "metadata create-one-to-many / create-many-to-many": "Relationships",
        "metadata list-optionsets / create-optionset / update-optionset / delete-optionset": "Global option sets",
        "solution list / info / export / import": "Solution lifecycle",
        "data export <set> -o file.csv": "Bulk export",
        "action function/invoke <name>": "Call OData function/action",
        "session info / clear / history": "Local session state",
        "help / quit": "REPL controls",
    })
