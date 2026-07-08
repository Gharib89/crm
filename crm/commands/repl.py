"""REPL command."""
# pyright: basic
from __future__ import annotations
import shlex
import time
from collections.abc import Callable
import click
from crm.core import session as session_mod
from crm.core import metadata_cache as mc_mod
from crm.core.metadata import list_attributes, list_entity_definitions
from crm.utils.d365_backend import D365Error
from prompt_toolkit.completion import Completer, Completion

# Slot table: (group, verb) -> (token_index, name_type)
# name_type "logical" = LogicalName (account); "set" = EntitySetName (accounts)
_ENTITY_SLOTS: dict[tuple[str, str], tuple[int, str]] = {
    ("entity",   "get"):        (2, "set"),
    ("entity",   "create"):     (2, "set"),
    ("entity",   "update"):     (2, "set"),
    ("entity",   "upsert"):     (2, "set"),
    ("entity",   "delete"):     (2, "set"),
    ("query",    "odata"):      (2, "set"),
    ("query",    "fetchxml"):   (2, "set"),
    ("query",    "saved"):      (2, "set"),
    ("query",    "user"):       (2, "set"),
    ("query",    "count"):      (2, "logical"),
    ("metadata", "entity"):     (2, "logical"),
    ("metadata", "attributes"): (2, "logical"),
}

# Options whose value is a column (attribute logical name), so a trailing
# ``--select <TAB>`` after a known entity completes that entity's attributes.
# Only ``--select`` qualifies: ``--expand`` takes navigation properties, not
# columns.
_ATTRIBUTE_OPTIONS: frozenset[str] = frozenset({"--select"})


def _strip_repl_prefix(argv: list[str]) -> list[str] | None:
    """Drop a single leading literal ``crm`` token from a REPL line's argv.

    Inside the REPL the accepted form is prefix-less (``connection whoami``), but
    every doc / ``--help`` / banner shows the shell form ``crm connection whoami``,
    so re-typing ``crm`` is the natural first reflex. Tolerate it: strip one exact
    ``crm`` token so both forms behave identically.

    Returns the argv to dispatch, or ``None`` for a bare ``crm`` line (argv empty
    after stripping) — the caller must treat that as a no-op rather than dispatch
    ``[]``, which would relaunch the REPL via ``invoke_without_command`` on a TTY.
    Only an exact first ``crm`` is stripped (``crmfoo`` is left alone), and at most
    one.
    """
    if argv and argv[0] == "crm":
        argv = argv[1:]
        if not argv:
            return None
    return argv


class MetadataCache:
    """Entity-name cache for the REPL session; reads from / writes to the
    persistent on-disk cache when constructed with ``use_cache=True``."""

    def __init__(self, *, use_cache: bool = False, refresh: bool = False) -> None:
        self._logical: list[str] | None = None
        self._set_names: list[str] | None = None
        # Attribute logical names fetched per entity, keyed by entity logical
        # name. Populated lazily on first `--select` Tab (one metadata GET per
        # entity) and memoized for the session; cleared with the def lists on a
        # profile switch / refresh so it never serves another org's columns.
        self._attributes: dict[str, list[str]] = {}
        # Profile the in-memory lists were loaded for; entity names are
        # org-specific, so a `profile use` mid-REPL must force a reload.
        self._loaded_profile: str | None = None
        self._use_cache = use_cache
        self._refresh = refresh

    def _ensure(self, backend) -> None:
        if self._logical is None or self._loaded_profile != backend.profile.name:
            self._load(backend)

    def _load(self, backend) -> None:
        if self._use_cache:
            lookup = mc_mod.load_definitions(
                backend.profile,
                fetch=lambda: list_entity_definitions(backend),
                refresh=self._refresh,
                now=time.time(),
            )
            # lookup.status ("hit"/"miss"/"refreshed") is intentionally unused
            # in the REPL — there is no display path for cache-status feedback.
            defs = lookup.definitions
            self._refresh = False
        else:
            defs = list_entity_definitions(backend)
        self._logical = [d["logical"] for d in defs]
        self._set_names = [d["set_name"] for d in defs]
        # A profile switch / refresh invalidates any memoized attribute lists —
        # they are org-specific just like the entity names above.
        self._attributes = {}
        self._loaded_profile = backend.profile.name

    def logical_names(self, backend) -> list[str]:
        self._ensure(backend)
        return self._logical  # type: ignore[return-value]

    def set_names(self, backend) -> list[str]:
        self._ensure(backend)
        return self._set_names  # type: ignore[return-value]

    def entities(self, backend) -> list[str]:
        """Backward-compat: returns logical names."""
        return self.logical_names(backend)

    def _resolve_logical(self, entity_token: str) -> str | None:
        """Map an on-line entity token (set *or* logical name) to its logical
        name using the loaded definition lists, or ``None`` if unknown."""
        if self._logical and entity_token in self._logical:
            return entity_token
        if self._set_names and entity_token in self._set_names:
            return self._logical[self._set_names.index(entity_token)]  # type: ignore[index]
        return None

    def attribute_names(self, backend, entity_token: str) -> list[str]:
        """Attribute logical names for ``entity_token`` (a set or logical name).

        Resolves the token to a logical name via the cached definition lists,
        then fetches the entity's attributes with one metadata GET, memoizing
        the result for the session. An unresolvable token is treated as a
        logical name directly (best effort) so a freshly-created entity not yet
        in the def lists still completes."""
        self._ensure(backend)
        logical = self._resolve_logical(entity_token) or entity_token
        cached = self._attributes.get(logical)
        if cached is None:
            cached = [
                row["LogicalName"]
                for row in list_attributes(backend, logical)
                if row.get("LogicalName")
            ]
            self._attributes[logical] = cached
        return cached


def complete_entity_token(
    line: str,
    logical_names: list[str],
    set_names: list[str],
) -> list[str] | None:
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
    slot = _ENTITY_SLOTS.get((group, verb))
    if slot is None:
        return None
    expected_idx, name_type = slot
    if expected_idx != token_index:
        return None
    names = set_names if name_type == "set" else logical_names
    return [n for n in names if n.startswith(prefix)]


def _entity_token_on_line(completed: list[str]) -> str | None:
    """Return the entity token (set or logical name) already present at its
    slot for the ``group verb`` on the line, or ``None`` if the line isn't a
    recognized entity-slot command or the slot isn't filled with a name yet.

    Reuses ``_ENTITY_SLOTS`` so attribute completion keys off exactly the same
    entity positions the entity-name completion already understands."""
    if len(completed) < 2:
        return None
    slot = _ENTITY_SLOTS.get((completed[0], completed[1]))
    if slot is None:
        return None
    idx, _name_type = slot
    if idx < len(completed):
        tok = completed[idx]
        if tok and not tok.startswith("-"):
            return tok
    return None


def _tokens_and_prefix(line: str) -> tuple[list[str], str]:
    """Split a REPL line into (completed tokens, in-progress prefix).

    A trailing space means the cursor sits on a fresh empty token; otherwise
    the last whitespace-separated token is the one being typed.
    """
    parts = line.split()
    if line.endswith(" ") or not parts:
        return parts, ""
    return parts[:-1], parts[-1]


def _resolve_command_chain(tokens: list[str]) -> click.Command | None:
    """Resolve ``tokens`` as a chain of subcommand names from the root ``cli``
    group. Returns the deepest resolved Group/Command only if every token was
    consumed as a subcommand name; ``None`` if a token is flag-shaped, doesn't
    resolve, or there's nothing left to descend into (imports the specific
    command module for each resolved token, same cost as running it)."""
    from crm.cli import cli
    current: click.Command = cli
    for tok in tokens:
        if tok.startswith("-") or not isinstance(current, click.Group):
            return None
        ctx = click.Context(current)
        nxt = current.get_command(ctx, tok)
        if nxt is None:
            return None
        current = nxt
    return current


def _option_strings(cmd: click.Command) -> list[str]:
    """All option flags (primary + ``--no-*`` secondary) declared on ``cmd``."""
    opts: list[str] = []
    for param in cmd.params:
        if isinstance(param, click.Option):
            opts.extend(param.opts)
            opts.extend(param.secondary_opts)
    return sorted(opts)


def _choice_values(cmd: click.Command, opt_token: str) -> list[str] | None:
    """Choice values for the option named ``opt_token`` on ``cmd``, or ``None``
    if ``opt_token`` isn't a recognized Choice-typed option of ``cmd``."""
    for param in cmd.params:
        if isinstance(param, click.Option) and opt_token in (*param.opts, *param.secondary_opts):
            if isinstance(param.type, click.Choice):
                return list(param.type.choices)
            return None
    return None


def complete_repl_line(
    line: str,
    logical_names: list[str],
    set_names: list[str],
    profile_names: list[str],
    attribute_getter: Callable[[str], list[str]] | None = None,
) -> list[str] | None:
    """Top-level REPL completion: command/group names, flags (incl. ``--no-*``
    forms), Choice flag values, profile names after ``--profile``, attribute
    logical names after ``--select`` for a known entity, and (unchanged) entity
    names at their existing slots. ``None`` means nothing applies at the cursor
    position.

    ``attribute_getter`` maps an on-line entity token (set or logical name) to
    that entity's attribute logical names; it is called only for a
    ``--select`` value once an entity is resolvable on the line. Left ``None``
    (or returning ``[]``) makes attribute completion a graceful no-op.

    ``--profile`` value-completion is deliberately position-blind (fires
    whenever the previous token is literally ``--profile``, regardless of
    what precedes it) — the REPL never validates the full Click option graph,
    unlike OS-shell completion which is scoped to wherever the option is
    actually declared.
    """
    completed, prefix = _tokens_and_prefix(line)
    prev = completed[-1] if completed else None

    if prev == "--profile":
        return [p for p in profile_names if p.startswith(prefix)]

    if prev is not None and prev.startswith("-"):
        # A column-valued option (``--select``) completes the on-line entity's
        # attribute logical names. Resolve the entity from its slot; a missing
        # entity or getter is a no-op (``--select`` carries no Choice values to
        # fall back to anyway).
        if prev in _ATTRIBUTE_OPTIONS and attribute_getter is not None:
            entity = _entity_token_on_line(completed)
            if entity is None:
                return None
            return [a for a in attribute_getter(entity) if a.startswith(prefix)]
        cmd = _resolve_command_chain(completed[:-1])
        if cmd is None:
            return None
        choices = _choice_values(cmd, prev)
        return [c for c in choices if c.startswith(prefix)] if choices is not None else None

    if prefix.startswith("-"):
        cmd = _resolve_command_chain(completed)
        if cmd is None:
            return None
        return [o for o in _option_strings(cmd) if o.startswith(prefix)]

    entity_matches = complete_entity_token(line, logical_names, set_names)
    if entity_matches is not None:
        return entity_matches

    cmd = _resolve_command_chain(completed)
    if cmd is not None and isinstance(cmd, click.Group):
        ctx = click.Context(cmd)
        return [n for n in cmd.list_commands(ctx) if n.startswith(prefix)]
    return None


class _ReplCompleter(Completer):
    """prompt_toolkit completer for the REPL: command/group names, flags (incl.
    ``--no-*`` forms), Choice flag values, profile names after ``--profile``,
    and entity names at their existing slots (``complete_entity_token``)."""

    def __init__(self, materialized_backend_getter, cache: MetadataCache):
        self._get_backend = materialized_backend_getter
        self._cache = cache

    def get_completions(self, document, complete_event):
        line = document.text_before_cursor
        try:
            profiles = session_mod.list_profiles()
        except Exception:  # completion must never raise
            profiles = []
        try:
            backend = self._get_backend()
            logical = self._cache.logical_names(backend)
            sets = self._cache.set_names(backend)
        except Exception:  # completion must never raise
            # Kept independent of the profile/command/flag paths above, none
            # of which need a backend at all.
            logical, sets = [], []

        def attribute_getter(entity_token: str) -> list[str]:
            # Deferred: only a `--select <TAB>` on a resolvable entity reaches
            # this, so the (possibly network) attribute fetch never runs for
            # command/flag/profile completion. Any failure is a silent no-op.
            try:
                return self._cache.attribute_names(self._get_backend(), entity_token)
            except Exception:  # completion must never raise
                return []

        try:
            # A lazy-import failure inside _resolve_command_chain surfaces as a
            # click.ClickException; completion must never raise.
            matches = complete_repl_line(line, logical, sets, profiles, attribute_getter)
        except Exception:
            return
        if matches is None:
            return
        _, prefix = _tokens_and_prefix(line)
        for name in matches:
            yield Completion(name, start_position=-len(prefix))


@click.command("repl")
@click.pass_context
def repl(click_ctx: click.Context):
    """Interactive REPL (default when no subcommand is provided)."""
    from crm.cli import CLIContext, cli
    ctx = click_ctx.ensure_object(CLIContext)
    ctx.skin.print_banner()
    ctx.skin.info(f"Session: {ctx.session_name}  |  Type 'help' for commands, 'quit' to exit.")
    cache = MetadataCache(use_cache=ctx.cache_metadata or ctx.refresh_metadata, refresh=ctx.refresh_metadata)
    completer = _ReplCompleter(ctx.materialized_backend, cache)
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
        stripped = _strip_repl_prefix(argv)
        if stripped is None:
            continue
        argv = stripped
        try:
            cli.main(args=argv, obj=ctx, standalone_mode=False, prog_name="crm")
        except SystemExit:
            pass
        except click.exceptions.Exit:
            # emit() (operational failure) or the root group (usage error, json mode)
            # already printed the envelope; nothing more to show — keep going.
            pass
        except click.ClickException as exc:
            # Usage errors (UsageError/BadParameter) under json_mode are rendered as
            # the envelope by _JsonAwareGroup and surface as click.exceptions.Exit
            # above, so they never reach here. This is the generic fallback for any
            # other ClickException.
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
        "profile add": "Create a profile, verify with WhoAmI, and activate it",
        "profile use": "Switch the active profile",
        "profile list": "List saved profiles",
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
        "session info / clear / history": "Local session state",
        "help / quit": "REPL controls",
    })
