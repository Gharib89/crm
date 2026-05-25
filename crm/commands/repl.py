"""REPL command."""
from __future__ import annotations
import shlex
import click
from crm.core import session as session_mod
from crm.core.metadata import list_entity_names
from crm.utils.d365_backend import D365Error
from prompt_toolkit.completion import Completer, Completion

# Slot-aware completion table: (group, verb) -> argument index (0-based)
_ENTITY_SLOTS: dict[tuple[str, str], int] = {
    ("entity",   "get"):        2,
    ("entity",   "create"):     2,
    ("entity",   "update"):     2,
    ("entity",   "upsert"):     2,
    ("entity",   "delete"):     2,
    ("query",    "odata"):      2,
    ("query",    "fetchxml"):   2,
    ("query",    "count"):      2,
    ("query",    "saved"):      2,
    ("query",    "user"):       2,
    ("metadata", "entity"):     2,
    ("metadata", "attributes"): 2,
}


class MetadataCache:
    """In-memory cache of entity logical names for the REPL session."""

    def __init__(self) -> None:
        self._entities: list[str] | None = None

    def entities(self, backend) -> list[str]:
        if self._entities is None:
            self._entities = list_entity_names(backend)
        return self._entities


def complete_entity_token(line: str, names: list[str]) -> list[str] | None:
    """Return entity-name completions or None if not on an entity-name slot."""
    parts = line.split()
    if line.endswith(" "):
        token_index = len(parts)
        prefix = ""
    else:
        if not parts:
            return None
        token_index = len(parts) - 1
        prefix = parts[-1]

    if len(parts) < 2:
        return None
    group, verb = parts[0], parts[1]
    expected_idx = _ENTITY_SLOTS.get((group, verb))
    if expected_idx is None or expected_idx != token_index:
        return None
    return [n for n in names if n.startswith(prefix)]


class _EntityCompleter(Completer):
    """prompt_toolkit completer for entity-name slots."""

    def __init__(self, backend_getter, cache: MetadataCache):
        self._get_backend = backend_getter
        self._cache = cache

    def get_completions(self, document, complete_event):
        line = document.text_before_cursor
        try:
            names = self._cache.entities(self._get_backend())
        except Exception:  # completion must never raise
            return
        matches = complete_entity_token(line, names)
        if matches is None:
            return
        if line.endswith(" "):
            prefix_len = 0
        else:
            prefix_len = len(line.split()[-1]) if line.split() else 0
        for name in matches:
            yield Completion(name, start_position=-prefix_len)


@click.command("repl")
@click.pass_context
def repl(click_ctx: click.Context):
    """Interactive REPL (default when no subcommand is provided)."""
    from crm.cli import CLIContext, cli
    ctx = click_ctx.ensure_object(CLIContext)
    ctx.skin.print_banner()
    ctx.skin.info(f"Session: {ctx.session_name}  |  Type 'help' for commands, 'quit' to exit.")
    cache = MetadataCache()
    completer = _EntityCompleter(ctx.backend, cache)
    pt_session = ctx.skin.create_prompt_session(completer=completer)
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


def _repl_help(ctx):
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
        "query count <entity>": "RetrieveTotalRecordCount via cached server-side count",
        "metadata entities": "List entity definitions",
        "metadata attributes <entity>": "List attributes",
        "metadata add-attribute <entity> --kind <k>": "Add a column to an entity",
        "metadata create-entity / delete-entity": "Custom entity lifecycle",
        "metadata create-one-to-many / create-many-to-many": "Relationships",
        "metadata list-optionsets / create-optionset / update-optionset / delete-optionset": "Global option sets",
        "metadata list-actions": "List OData actions (POST verbs)",
        "metadata list-functions": "List OData functions (GET verbs)",
        "solution list / info / export / import": "Solution lifecycle",
        "data export <set> -o file.csv": "Bulk export",
        "action function/invoke <name>": "Call OData function/action",
        "init [--template]": "Bootstrap a workspace (.env.example or interactive profile)",
        "session info / clear / history": "Local session state",
        "help / quit": "REPL controls",
    })
