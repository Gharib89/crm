"""crm — Click-based CLI + REPL for Microsoft Dynamics 365 CE — on-prem v9.x (NTLM) or Dataverse online (OAuth).

Entry point: `crm` (installed) or `python -m crm`.

Running with no subcommand drops into the REPL. Each command supports `--json` for
machine-readable output. `--dry-run` previews writes without issuing them; reads
(GET) still run for real.
"""
# pyright: basic

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING, Any

import click

from crm import __version__
from crm.core.logging_setup import setup_logging

if TYPE_CHECKING:
    from crm.utils.d365_backend import D365Backend
from crm.utils.repl_skin import ReplSkin
from crm.commands._helpers import (
    _apply_jq,
    _normalize_odata_envelope,
    _project_fields,
    _project_table_columns,
    _sanitize,
    _short_repr,
    _strip_odata_keys,
)
from crm.commands._tty import _stdin_is_tty

# Exit code for an operational failure (ADR 0001): a command that ran but did not
# achieve its effect — D365 server error, in-command validation, declined confirm.
FAILURE_EXIT_CODE = 1


def force_utf8_output(stream: Any) -> None:
    """Reconfigure a text stream to UTF-8 so box-drawing output never crashes.

    On a default Windows console (cp1252) the human table/banner renderers emit
    box-drawing characters that cp1252 cannot encode, raising UnicodeEncodeError
    (#146a). TextIOWrapper.reconfigure (3.7+) flips the encoding in place.
    errors='replace' takes effect only when reconfigure succeeds; if the stream
    lacks reconfigure or the call raises, the stream is left unchanged.
    """
    encoding = getattr(stream, "encoding", None)
    if isinstance(encoding, str) and encoding.lower() == "utf-8":
        return
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass  # stream not reconfigurable (already detached / non-seekable wrapper)


class CLIContext:
    """Per-invocation state shared across subcommands."""

    def __init__(self):
        self.json_mode: bool = False
        self.dry_run: bool = False
        # Output-shaping selection behind the global `--fields` flag (#735): the
        # top-level keys to project the curated `data` payload down to, or None
        # for no shaping. Per-invocation (never sticky), set in the root callback.
        self.fields: list[str] | None = None
        # Compiled jq program behind the global `--jq` flag (#736), or None for no
        # shaping. A `jq.compile(...)` object built (and syntax-validated) in the
        # root callback; mutually exclusive with `fields`. Per-invocation, never
        # sticky. Typed `Any` because jq is imported lazily (kept out of cold start).
        self.jq_program: Any = None
        self.profile_name: str | None = None
        # Which of the five dual-position global options (#818) were supplied at
        # the ROOT position this invocation, as canonical kind strings ("json",
        # "fields", "jq", "dry_run", "profile"). Populated by the root callback via
        # ParameterSource; read by an injected leaf option's callback to reject the
        # same flag given in both positions. Reset per invocation (per REPL line).
        self._root_positions: set[str] = set()
        self.password: str | None = None
        self.auth_scheme: str | None = None
        self.stage_only: bool = False
        self.cache_metadata: bool = False
        self.refresh_metadata: bool = False
        self.retry_on_ambiguous: bool = False
        self.session_name: str = "default"
        # Set True by backend() whenever this command resolved a connection;
        # reset per invocation in the root callback. Lets emit() stamp the
        # serving profile/url only on envelopes that actually opened a backend
        # (#624) — never on a later REPL line whose backend is merely cached.
        self.connection_resolved: bool = False
        self._backend: D365Backend | None = None
        self._backend_key: tuple[str | None, str | None, bool, str | None, bool] | None = None
        self.skin: ReplSkin = ReplSkin("d365", version=__version__)

    def emit(self, ok: bool, data: Any = None, *, error: str | None = None,
             meta: dict | None = None, table: dict | None = None,
             warnings: list[str] | None = None) -> None:
        """Print either a JSON envelope or a human-friendly representation.

        `warnings` is the structured advisory channel (#64): each entry is
        appended to `meta.warnings` (never clobbering any already there) in JSON
        mode, or printed via skin.warning in human mode. A pre-existing
        `meta["warnings"]` that is not a list is coerced to a single-item list
        first, so a stray scalar can never split into characters or raise. A
        fresh dict is built so the caller's `meta` is not mutated.
        """
        if self.json_mode:
            envelope: dict[str, Any] = {"ok": ok}
            shape_warnings: list[str] = []
            if data is not None:
                # Curate `data` into the CLI-owned shape (ADR 0008): unwrap an
                # OData collection envelope to a bare array (paging → meta), then
                # strip `@odata.*` protocol keys. Applied once here so every list
                # verb and single record is consistent, not per-command. A dry-run
                # mutation preview (`data._dry_run`) is a verbatim echo of the
                # request that WOULD be sent — never curated, so request-shape keys
                # like `<nav>@odata.bind`/`@odata.type` survive and the preview
                # matches the wire payload. (Read results under --dry-run carry no
                # `_dry_run` marker and ARE curated.)
                if not (isinstance(data, dict) and "_dry_run" in data):
                    data, paging = _normalize_odata_envelope(data)
                    if paging:
                        meta = {**(meta or {}), **paging}
                    data = _strip_odata_keys(data)
                # Output shaping (#735/#736): reshape `data` at this seam — after
                # ADR 0008 curation, before serialization — so every command inherits
                # it. Gated on `ok` so an error envelope bypasses shaping; a dry-run
                # preview is shaped like any other data. `--fields` and `--jq` are
                # mutually exclusive (enforced at parse time), so at most one runs.
                if ok and self.fields is not None:
                    data, shape_warnings = _project_fields(data, self.fields)
                elif ok and self.jq_program is not None:
                    data, jq_error = _apply_jq(data, self.jq_program)
                    if jq_error is not None:
                        # Compiled but failed at eval time: the success payload it was
                        # piped through is unusable, so surface an error envelope via
                        # the canonical error path (which stamps meta.dry_run and keeps
                        # the ok:false shape consistent with every other error) rather
                        # than a hand-built one. The accumulated paging meta describes
                        # the now-discarded payload, so it is not carried onto the
                        # error. emit(False) exits in JSON mode; return guards against
                        # falling through if that ever changes.
                        self.emit(False, error=f"--jq: {jq_error}")
                        return
                envelope["data"] = _sanitize(data)
            if error:
                envelope["error"] = error
            # Canonical dry-run signal (#61): keyed off the invocation flag, not
            # data-sniffing, so list-shaped batch/poll previews are covered too.
            # Build a fresh dict so the caller's meta is not mutated.
            if self.dry_run:
                meta = {**(meta or {}), "dry_run": True}
            all_warnings = [*(warnings or []), *shape_warnings]
            if all_warnings:
                existing = (meta or {}).get("warnings") or []
                if not isinstance(existing, list):
                    existing = [existing]
                meta = {**(meta or {}), "warnings": [*existing, *all_warnings]}
            # Connection identity (#624): a success envelope from a command that
            # resolved a backend self-identifies the serving profile + Web API
            # base, so an agent can tell which org a result came from. Gated so
            # error envelopes keep their reserved {status,code,category,retryable}
            # meta, a backend resolved THIS command (connection_resolved) is
            # required so a later REPL line over a stale cached backend stays
            # clean, and _backend must still be live (profile add invalidates
            # before emit). Fresh dict, so the caller's meta is not mutated.
            if ok and self.connection_resolved and self._backend is not None:
                meta = {**(meta or {}),
                        "profile": self._backend.profile.name,
                        "url": self._backend.profile.api_base}
            if meta:
                envelope["meta"] = meta
            click.echo(json.dumps(envelope, indent=2, default=str))
            if not ok:
                raise click.exceptions.Exit(FAILURE_EXIT_CODE)
            return

        for w in warnings or []:
            self.skin.warning(w)

        if not ok:
            self.skin.error(error or "Operation failed.")
            raise click.exceptions.Exit(FAILURE_EXIT_CODE)

        # Output shaping (#735), human mode: `--fields` selects/orders the table
        # columns (or projects a record's key/value render). Only reached on
        # success, so error output is never shaped. Warnings print via the skin.
        if self.fields is not None:
            if table:
                table, shape_ws = _project_table_columns(table, self.fields)
            elif data is not None:
                data, shape_ws = _project_fields(data, self.fields)
            else:
                shape_ws = []
            for w in shape_ws:
                self.skin.warning(w)

        if table:
            headers = table.get("headers", [])
            rows = table.get("rows", [])
            self.skin.table(headers, rows)
            if meta:
                for k, v in meta.items():
                    self.skin.status(k, str(v))
            return

        if isinstance(data, dict) and data:
            for k, v in data.items():
                self.skin.status(k, _short_repr(v))
        elif isinstance(data, list):
            self.skin.info(f"{len(data)} item(s)")
            for item in data[:20]:
                click.echo(f"  - {_short_repr(item)}")
            if len(data) > 20:
                self.skin.hint(f"... {len(data) - 20} more items")
        elif data is not None:
            click.echo(str(data))
        if meta:
            for k, v in meta.items():
                self.skin.status(k, str(v))

    def hint(self, hint_id: str) -> None:
        """Show a one-time next-step hint (#657) after a command's normal output.

        Human/REPL only: suppressed under `--json` and when stdout is not a TTY, so
        the hint never pollutes output an agent or script is capturing. The env
        kill-switch and show-once bookkeeping live in `crm.core.hints`; the gate
        here is what keeps `take_hint` (and its seen-store) off the machine path
        entirely — under `--json`/no-TTY the store is never even read.
        """
        if self.json_mode:
            return
        from crm.commands import _tty
        if not _tty._stdout_is_tty():
            return
        from crm.core import hints as hints_mod
        text = hints_mod.take_hint(hint_id)
        if text is not None:
            self.skin.hint(text)

    def backend(self) -> "D365Backend":
        from crm.core import connection as conn_mod
        from crm.core import session as session_mod
        from crm.utils.d365_backend import D365Backend

        # Profile selection: --profile flag > session active_profile > wizard.
        # A flag value is authoritative; otherwise fall back to the saved
        # active_profile so `crm profile use` persists across later commands (#130).
        effective_profile = self.profile_name
        if effective_profile is None:
            state = session_mod.load_session(self.session_name)
            candidate = state.get("active_profile")
            # Ignore a stale pointer to a deleted profile (its file is gone).
            if candidate and session_mod.profile_path(candidate).is_file():
                effective_profile = candidate

        if effective_profile is None and _stdin_is_tty() and not self.json_mode:
            # First-run UX: no profile resolvable and we're on an interactive
            # terminal — drop into the setup wizard so a new user goes
            # zero-to-working. Under --json / no-TTY we skip this and let
            # resolve_credentials() raise the actionable "run `crm profile add`"
            # error instead (never hang an agent/CI invocation).
            import click as _click
            from crm.commands.profile import profile_add
            _click.echo("No profile configured yet. Let's set one up:")
            _click.get_current_context().invoke(profile_add)
            state = session_mod.load_session(self.session_name)
            effective_profile = state.get("active_profile")

        key = (effective_profile, self.password, self.dry_run, self.auth_scheme,
               self.retry_on_ambiguous)
        if self._backend is None or self._backend_key != key:
            allow_prompt = _stdin_is_tty() and not self.json_mode
            resolved = conn_mod.resolve_credentials(
                profile_name=effective_profile,
                password_override=self.password,
                allow_prompt=allow_prompt,
            )
            if self.auth_scheme is not None:
                resolved.profile.auth_scheme = self.auth_scheme
            self._backend = D365Backend(
                resolved.profile, resolved.password, dry_run=self.dry_run,
                retry_on_ambiguous=self.retry_on_ambiguous,
            )
            self._backend_key = key
        # Mark that this command opened a connection (covers a freshly built or
        # a cached backend) so emit() stamps meta.profile/url (#624).
        self.connection_resolved = True
        return self._backend

    def materialized_backend(self) -> "D365Backend | None":
        """Return the cached backend, if one already exists, without resolving it."""
        return self._backend

    def invalidate_backend(self) -> None:
        """Drop the cached D365Backend so the next backend() call rebuilds it.

        Called when the profile changes (`crm profile add`/`use`/`rm`) so
        the REPL stops reusing a backend wired up to a stale profile.
        Also triggers automatically if `profile_name`/`password`/`dry_run` change
        between calls (e.g., root opts re-supplied per REPL line).
        """
        self._backend = None
        self._backend_key = None

    def staged_meta(self) -> dict[str, Any] | None:
        """Meta dict flagging a staged (unpublished) metadata write, or None.

        Replaces the `{"staged": True} if ctx.stage_only else None` ternary
        hand-copied across the metadata-mutating verbs. Deliberately NOT folded
        into `emit` — that would leak `staged` into read-command output during a
        `--stage-only` session, breaking the byte-identical envelope contract.
        """
        return {"staged": True} if self.stage_only else None


pass_ctx = click.make_pass_decorator(CLIContext, ensure=True)


def _emit_error_envelope(message: str, *, meta: dict[str, Any] | None = None) -> None:
    """Print the standard {ok: false, error: ...} JSON error envelope. Usage errors
    omit `meta`; non-usage ClickException failures pass an empty `meta` object."""
    env: dict[str, Any] = {"ok": False, "error": message}
    if meta is not None:
        env["meta"] = meta
    click.echo(json.dumps(env, indent=2, default=str))


def _suppress_bare_repl(json_mode: bool) -> bool:
    """Whether bare `crm` (no subcommand) must fail fast instead of dropping into
    the interactive REPL. True when the caller is clearly non-interactive: --json,
    an explicit CRM_NO_REPL opt-out, or a non-TTY stdin (piped/redirected, as
    agents and CI invoke it). A proactive isatty probe — intentionally stronger
    than waiting for the REPL's EOF handler so a bare invocation never hangs."""
    if json_mode:
        return True
    if os.environ.get("CRM_NO_REPL", "").lower() in ("1", "true", "yes", "on"):
        return True
    return not _stdin_is_tty()


def _json_mode_active(args: list[str] | None) -> bool:
    """Decide whether to emit JSON by scanning argv — the authoritative per-invocation
    signal. `--json` is valid both before AND after the subcommand (#818), so it can
    appear anywhere in argv for a real --json invocation; argv is the reliable source.
    The parsed CLIContext.json_mode is deliberately NOT consulted: the root callback
    may not have run yet when a usage error fires, and in the REPL it carries a stale
    value from a prior --json line, which would mis-skin a subsequent human-mode error.

    The whole argv is scanned (not just the leading root-option run) because `--json`
    may now trail the subcommand. A value consumed by a preceding value-taking ROOT
    option is skipped, so a '--json' sitting in such a slot is treated as a value, not
    the flag. **Accepted false positive (#818, ADR 0025):** a leaf option that takes a
    value is not in `value_opts`, so a literal '--json' passed as *its* value
    (e.g. `entity get accounts --select --json`) is mistaken for the flag and, in an
    invocation that also has a usage error, renders that error as a JSON envelope.
    Purely cosmetic (the error text is unchanged), it only affects error skinning, and
    is pinned by a test — accepted over reimplementing Click's per-command tokenizer
    here just to shape usage-error output."""
    if not args:
        return False
    # ROOT options that consume the following token as their value; '--json' sitting
    # in such a slot is a value, not the flag. (Leaf value-options are not enumerated
    # here — see the accepted false positive above.)
    value_opts = {
        "--profile", "--password", "--log-level", "--log-format",
        "--auth-scheme", "--session",
    }
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--json":
            return True
        if tok in value_opts:
            i += 2  # skip the option and its value
            continue
        i += 1
    return False


# ── Dual-position global options (#818, ADR 0025) ────────────────────────────
# Five root global options are ALSO valid after the subcommand — position is pure
# syntax, semantics identical. They are injected into every leaf command's params
# at the root resolution seam (`_LazyJsonAwareGroup.get_command`), NOT by rewriting
# argv (which cannot tell a flag token from a flag-valued option value). Each
# injected option has `expose_value=False` and a callback that funnels the value
# through the SAME merge semantics as the root callback, so `crm entity list --json`
# is byte-identical to `crm --json entity list`. See ADR 0025.

# Canonical kind → the option's user-facing token, in declaration/injection order.
_DUALPOS_TOKENS: dict[str, str] = {
    "json": "--json",
    "fields": "--fields",
    "jq": "--jq",
    "profile": "--profile",
    "dry_run": "--dry-run",
}
# Help-text marker appended to every injected option so `--help` / `crm describe`
# show it is a global flag that also works before the command (decision 6).
_DUALPOS_HELP_SUFFIX = "  [global; also valid before the command]"


def _parse_fields_value(value: str) -> list[str]:
    """Parse a ``--fields`` value into its ordered key list, or raise a usage error.

    Shared by the root callback and the injected leaf option so both positions
    validate identically. An empty / whitespace-only value has nothing to project
    and is a usage error (exit 2)."""
    parsed = [f.strip() for f in value.split(",") if f.strip()]
    if not parsed:
        raise click.BadParameter("no field names given", param_hint="--fields")
    return parsed


def _compile_jq_value(value: str) -> Any:
    """Compile a ``--jq`` program, or raise a usage error (exit 2).

    Shared by root and leaf so an invalid program fails fast — before any profile
    resolution or network call — in either position. The `jq` module is imported
    lazily so the common no-jq path never pays for it."""
    import jq  # lazy: never imported unless --jq is used

    try:
        return jq.compile(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--jq") from exc


def _reject_fields_jq_conflict(cli_ctx: "CLIContext") -> None:
    """Cross-position guard: ``--fields`` and ``--jq`` are two spellings of the same
    shaping seam, so having both set — in any combination of positions — is a usage
    error (exit 2), matching the root-position check in the root callback."""
    if cli_ctx.fields is not None and cli_ctx.jq_program is not None:
        raise click.UsageError("--fields and --jq are mutually exclusive")


def _dualpos_callback(kind: str, token: str):
    """Build the parse callback for one injected dual-position option.

    Fires for every parse, but does work only when the option was actually supplied
    at the leaf (``ParameterSource.COMMANDLINE``). It then rejects the same option
    given in BOTH positions (decision 2) and otherwise applies the value through the
    exact merge semantics the root callback uses (decision 4): `--profile` sticky,
    the rest per-invocation, `--jq` implying `--json` and compiling now, `--fields`
    /`--jq` mutually exclusive cross-position."""
    def _cb(ctx: click.Context, param: click.Parameter, value: Any) -> Any:
        from click.core import ParameterSource

        if ctx.get_parameter_source(param.name) != ParameterSource.COMMANDLINE:
            return value  # not supplied after the subcommand → nothing to merge
        cli_ctx = ctx.find_root().ensure_object(CLIContext)
        if kind in cli_ctx._root_positions:
            raise click.UsageError(
                f"{token} was provided both before and after the subcommand; "
                f"pick one."
            )
        if kind == "json":
            cli_ctx.json_mode = True
        elif kind == "dry_run":
            cli_ctx.dry_run = True
        elif kind == "profile":
            cli_ctx.profile_name = value
        elif kind == "fields":
            cli_ctx.fields = _parse_fields_value(value)
            _reject_fields_jq_conflict(cli_ctx)
        elif kind == "jq":
            cli_ctx.jq_program = _compile_jq_value(value)
            cli_ctx.json_mode = True
            _reject_fields_jq_conflict(cli_ctx)
        return value

    return _cb


def _make_dualpos_options() -> list[click.Option]:
    """Fresh ``click.Option`` instances for the five dual-position global options.

    New instances every call: a Click Option is bound into the command it is added
    to, so the same object must not be shared across every leaf. Each uses an
    underscore-prefixed internal name that cannot collide with a real leaf param,
    and `expose_value=False` so leaf command signatures are untouched."""
    return [
        click.Option(
            ["--json", "_dualpos_json"], is_flag=True, default=False,
            expose_value=False, callback=_dualpos_callback("json", "--json"),
            help="Emit machine-readable JSON output." + _DUALPOS_HELP_SUFFIX,
        ),
        click.Option(
            ["--fields", "_dualpos_fields"], default=None, metavar="KEY[,KEY...]",
            expose_value=False, callback=_dualpos_callback("fields", "--fields"),
            help="Project the data payload down to these comma-separated top-level "
                 "keys." + _DUALPOS_HELP_SUFFIX,
        ),
        click.Option(
            ["--jq", "_dualpos_jq"], default=None, metavar="PROGRAM",
            expose_value=False, callback=_dualpos_callback("jq", "--jq"),
            help="Run a jq program over the curated data payload; the result "
                 "replaces data. Implies --json." + _DUALPOS_HELP_SUFFIX,
        ),
        click.Option(
            ["--profile", "_dualpos_profile"], default=None,
            shell_complete=_complete_profile_names, expose_value=False,
            callback=_dualpos_callback("profile", "--profile"),
            help="Connection profile name." + _DUALPOS_HELP_SUFFIX,
        ),
        click.Option(
            ["--dry-run", "_dualpos_dry_run"], is_flag=True, default=False,
            expose_value=False, callback=_dualpos_callback("dry_run", "--dry-run"),
            help="Preview writes without issuing them; reads run normally."
                 + _DUALPOS_HELP_SUFFIX,
        ),
    ]


def _leaf_declares_token(cmd: click.Command, token: str) -> bool:
    """Whether a command already declares an option under `token` (primary or `--no-`
    secondary form)."""
    for p in cmd.params:
        if isinstance(p, click.Option) and (token in p.opts or token in p.secondary_opts):
            return True
    return False


def _inject_dualpos_options(cmd: click.Command) -> None:
    """Append the dual-position options to one leaf command, skipping any token the
    leaf already declares.

    That skip does double duty: it keeps injection **idempotent** (a second pass over
    an already-injected leaf finds `--json` etc. present and skips all five), and it
    preserves a leaf's OWN same-named option — `profile set-password --profile`,
    `profile delete-password --profile` keep their required local meaning."""
    for opt in _make_dualpos_options():
        if not _leaf_declares_token(cmd, opt.opts[0]):
            cmd.params.append(opt)


def _inject_dualpos_tree(cmd: click.Command) -> None:
    """Walk a resolved top-level command and inject the dual-position options into
    every leaf beneath it.

    Subgroups below the root are eager ``click.Group``s, so `.commands` is populated
    and the walk reaches every leaf; only the root group itself is lazy, and it is
    never passed here (injection is driven from the root's own `get_command`)."""
    if isinstance(cmd, click.Group):
        for sub in cmd.commands.values():
            _inject_dualpos_tree(sub)
    else:
        _inject_dualpos_options(cmd)


class _JsonAwareGroup(click.Group):
    """Root group that intercepts Click usage errors for consistent rendering: a
    global root option placed *after* the subcommand (which Click rejects as
    NoSuchOption) becomes a position hint, and under --json every usage error
    renders as the standard JSON envelope on stdout. Exit code is preserved (2,
    per ADR 0001)."""

    def _global_option_names(self) -> set[str]:
        """Every option token the root group declares — primary and secondary
        (`--no-*`) forms. Derived from the live params so the set never drifts from
        the actual root options as global flags are added or renamed."""
        names: set[str] = set()
        for param in self.params:
            if isinstance(param, click.Option):
                names.update(param.opts)
                names.update(param.secondary_opts)
        return names

    def _global_option_hint(self, exc: "click.NoSuchOption") -> "str | None":
        """If the rejected option is a root-level global option, return a message
        telling the user to place it before the subcommand; otherwise None (so a
        genuinely unknown option keeps Click's error, incl. its "Did you mean")."""
        name = exc.option_name
        if name not in self._global_option_names():
            return None
        # exc.ctx.command_path is e.g. "crm profile list"; rebuild it with the
        # global option hoisted to just after the root program name.
        parts = (exc.ctx.command_path.split() if exc.ctx else ["crm"]) or ["crm"]
        corrected = " ".join([parts[0], name, *parts[1:]])
        return (f"{name!r} is a global option; place it before the command:\n"
                f"  {corrected} ...")

    def main(self, args=None, **kwargs):  # type: ignore[override]
        argv = list(args) if args is not None else sys.argv[1:]
        json_mode = _json_mode_active(argv)
        # Whole-argv JSON signal for the root callback's passive guards (#818). A
        # dual-position --json/--jq may trail the subcommand, so the root-position
        # `json_mode` param the callback receives can't see it; stash the argv-wide
        # answer here (argv only lives at this seam) for the callback to read. `--jq`
        # implies --json, so either token means the invocation emits JSON. Recomputed
        # per invocation — including each REPL line, which re-enters main().
        self._json_for_guards = json_mode or "--jq" in argv
        # Run non-standalone so Click parse/usage errors reach us instead of being
        # printed-and-exited by Click. We re-render: a misplaced global flag gets a
        # position hint; under --json a usage error becomes the envelope; otherwise
        # Click's own rendering is preserved. We then replicate standalone exit
        # semantics (super() returns the command value on success, or the Exit code
        # as an int when emit() raised Exit). EPIPE and EOFError/KeyboardInterrupt
        # are handled inside Click even non-standalone, so they need no replication.
        standalone = kwargs.pop("standalone_mode", True)
        try:
            rv = super().main(args=args, standalone_mode=False, **kwargs)
        except click.NoSuchOption as exc:
            hint = self._global_option_hint(exc)
            if hint is None:
                return self._render_usage_error(exc, json_mode, standalone)
            if json_mode:
                _emit_error_envelope(hint)
            else:
                click.echo(f"Error: {hint}", err=True)
            if standalone:
                sys.exit(exc.exit_code)
            raise click.exceptions.Exit(exc.exit_code)
        except click.ClickException as exc:
            return self._render_usage_error(exc, json_mode, standalone)
        except click.exceptions.Abort:
            if standalone:
                click.echo("Aborted!", file=sys.stderr)
                sys.exit(1)
            raise
        if standalone:
            sys.exit(rv if isinstance(rv, int) else 0)
        return rv

    def _render_usage_error(self, exc: "click.ClickException", json_mode: bool,
                            standalone: bool):
        """Render a non-global-flag ClickException exactly as before this hint was
        added, except that under --json any ClickException becomes a stdout JSON
        envelope. A direct human invocation keeps Click's standalone rendering,
        and the non-standalone human path still propagates to the REPL's
        skin.error handler."""
        if json_mode:
            # Usage errors omit meta and keep Click's exit 2; non-usage
            # ClickExceptions carry an empty meta and Click's default exit 1.
            meta = None if isinstance(exc, click.UsageError) else {}
            _emit_error_envelope(exc.format_message(), meta=meta)
            if standalone:
                sys.exit(exc.exit_code)
            raise click.exceptions.Exit(exc.exit_code)
        if not standalone or json_mode:
            raise exc
        exc.show()
        sys.exit(exc.exit_code)


class _LazyJsonAwareGroup(_JsonAwareGroup):
    """Root group that imports a subcommand's module only when that subcommand is
    invoked, so `crm --version` and direct command invocations avoid importing all
    command modules (and their requests/NTLM/prompt_toolkit deps). `crm --help`
    still imports every module to render short help — an accepted trade-off."""

    # Click command name -> "module:attribute". This map is the sole command
    # registry — a new top-level command must be added here to be exposed.
    _lazy_commands = {
        "action": "crm.commands.action:action_group",
        "app": "crm.commands.app:app_group",
        "apply": "crm.commands.apply:apply_cmd",
        "async": "crm.commands.async_ops:async_group",
        "audit": "crm.commands.audit:audit_group",
        "batch": "crm.commands.batch:batch_cmd",
        "chart": "crm.commands.chart:chart_group",
        "completion": "crm.commands.completion:completion_group",
        "connection": "crm.commands.connection:connection_group",
        "connectionrole": "crm.commands.connectionrole:connectionrole_group",
        "dashboard": "crm.commands.dashboard:dashboard_group",
        "data": "crm.commands.data:data_group",
        "describe": "crm.commands.describe:describe_cmd",
        "doctor": "crm.commands.connection:doctor_command",
        "dup": "crm.commands.dup:dup_group",
        "entity": "crm.commands.entity:entity_group",
        "examples": "crm.commands.examples:examples_cmd",
        "fieldsec": "crm.commands.fieldsec:fieldsec_group",
        "form": "crm.commands.form:form_group",
        "metadata": "crm.commands.metadata:metadata_group",
        "org": "crm.commands.org:org_group",
        "plugin": "crm.commands.plugin:plugin_group",
        "profile": "crm.commands.profile:profile_group",
        "query": "crm.commands.query:query_group",
        "repl": "crm.commands.repl:repl",
        "report": "crm.commands.report:report_group",
        "ribbon": "crm.commands.ribbon:ribbon_group",
        "service-document": "crm.commands.batch:service_document_cmd",
        "scaffold": "crm.commands.scaffold:scaffold_group",
        "security": "crm.commands.security:security_group",
        "self-update": "crm.commands.self_update:self_update_cmd",
        "session": "crm.commands.session:session_group",
        "skill": "crm.commands.skill:skill_group",
        "sitemap": "crm.commands.sitemap:sitemap_group",
        "sla": "crm.commands.sla:sla_group",
        "solution": "crm.commands.solution:solution_group",
        "theme": "crm.commands.theme:theme_group",
        "translation": "crm.commands.translation:translation_group",
        "view": "crm.commands.view:view_group",
        "webresource": "crm.commands.webresource:webresource_group",
        "workflow": "crm.commands.workflow:workflow_group",
    }

    def list_commands(self, ctx):
        return sorted({*self._lazy_commands, *super().list_commands(ctx)})

    def resolve_command(self, ctx, args):
        """Delegate to Click, but widen the "Did you mean ...?" candidate set.

        Click's `Group.resolve_command` raises `NoSuchCommand(possibilities=
        self.commands)`, and the exception derives its suggestion from that set.
        This root group is lazy — `self.commands` is empty (subcommands live in
        the lazy registry, not eagerly registered), so Click offers no suggestion
        for a near-miss group name while stock subgroups do for verbs. Re-raise
        with the full top-level command set (`list_commands`, the single source of
        truth) so `crm entit` → "Did you mean 'entity'?". The option-looking-token
        reparse side effect happens inside super() before the raise, so it stays
        intact; a no-close-match typo still yields the bare message (Click's
        `get_close_matches` cutoff is reused unchanged)."""
        try:
            return super().resolve_command(ctx, args)
        except click.exceptions.NoSuchCommand as exc:
            raise click.exceptions.NoSuchCommand(
                exc.command_name, possibilities=self.list_commands(ctx), ctx=ctx,
            ) from None

    def get_command(self, ctx, cmd_name):
        eager = super().get_command(ctx, cmd_name)
        if eager is not None:
            # Dual-position global options (#818): inject the five into every leaf
            # of the resolved subtree (idempotent — see _inject_dualpos_options) so
            # `crm entity list --json` parses the trailing flag. Done at resolution
            # rather than declaration because the root is lazy: modules load here.
            _inject_dualpos_tree(eager)
            return eager
        target = self._lazy_commands.get(cmd_name)
        if target is None:
            return None
        import importlib
        module_name, attr = target.split(":")
        # Surface lazy-load failures as a clean ClickException (rendered as
        # "Error: ..." with no traceback) rather than dumping a raw ImportError
        # to the user — especially confusing in a frozen build. A broken entry
        # here is a packaging/wiring bug; the sync test in test_lazy_imports.py
        # guards it at CI time, so this path should never fire in practice.
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise click.ClickException(
                f"failed to import {module_name!r} for command {cmd_name!r}: {exc}"
            ) from exc
        command = getattr(module, attr, None)
        if not isinstance(command, click.Command):
            raise click.ClickException(
                f"{target!r} did not resolve to a Click command for {cmd_name!r}"
            )
        _inject_dualpos_tree(command)
        return command


# ── Root group ──────────────────────────────────────────────────────────


def _complete_profile_names(ctx: click.Context, param: click.Parameter, incomplete: str) -> list[str]:
    """Dynamic ``--profile`` value completion: saved profile names.

    Local file read only, never a network call — shell completion spawns a
    fresh ``crm`` process per Tab, so cold-start/import cost is kept minimal
    (deferred import) and no backend/connection is ever touched.
    """
    from crm.core.session import list_profiles
    try:
        return [name for name in list_profiles() if name.startswith(incomplete)]
    except OSError:  # e.g. CRM_HOME unwritable/unreadable — best-effort, never crash
        return []


def _completion_profile(ctx: click.Context):
    """Resolve the profile whose metadata cache OS-shell completion should read:
    the ``--profile`` on the line if present, else the active profile of the
    session named on the line (``--session``), else the default session.

    Local reads only (profile file + session pointer) — shell completion spawns
    a fresh ``crm`` per Tab, so this never touches the network. Returns a loaded
    ``ConnectionProfile`` or ``None`` (no profile / unreadable / missing)."""
    from crm.core import session as session_mod

    root_params = ctx.find_root().params
    name = root_params.get("profile_name")
    if not name:
        try:
            # Honor an explicit --session on the line; the active profile is
            # per-session, so the default session would resolve the wrong org.
            session_name = root_params.get("session_name") or "default"
            state = session_mod.load_session(session_name)
            candidate = state.get("active_profile")
            if candidate and session_mod.profile_path(candidate).is_file():
                name = candidate
        except (OSError, ValueError):
            return None
    if not name:
        return None
    try:
        return session_mod.load_profile(name)
    except (OSError, ValueError, FileNotFoundError):
        return None


def _complete_entity_set_names(ctx: click.Context, param: click.Parameter, incomplete: str) -> list[str]:
    """Dynamic entity-set-name completion for OS-shell positional args.

    **Disk-cache only, never a network call** — shell completion runs a fresh
    ``crm`` process per Tab, so a per-keystroke round-trip is unacceptable and
    cold-start must stay cheap. Reads the on-disk metadata cache for the
    resolved profile (populated by earlier live runs / ``--cache-metadata``);
    a cache miss returns no completions silently. Best-effort — never raises."""
    import time

    from crm.core import metadata_cache
    try:
        profile = _completion_profile(ctx)
        if profile is None:
            return []
        definitions = metadata_cache.read_definitions(profile, now=time.time())
        if definitions is None:
            return []
        return [d["set_name"] for d in definitions
                if d["set_name"] and d["set_name"].startswith(incomplete)]
    except Exception:  # noqa: BLE001 — completion must never crash the shell subprocess
        return []


@click.group(cls=_LazyJsonAwareGroup, name="crm", invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--json", "json_mode", is_flag=True, help="Emit machine-readable JSON output.")
@click.option("--fields", "fields", default=None, metavar="KEY[,KEY...]",
              help="Project the data payload down to these comma-separated top-level "
                   "keys (JSON: per row/record; human: table columns). Applied after "
                   "curation; the ok/error/meta envelope is untouched.")
@click.option("--jq", "jq_program", default=None, metavar="PROGRAM",
              help="Run a jq program over the curated data payload; the result "
                   "replaces data in the envelope. Implies --json. Mutually exclusive "
                   "with --fields. An invalid program is a usage error (exit 2).")
@click.option("--dry-run", is_flag=True,
              help="Preview writes without issuing them; reads run normally.")
@click.option("--profile", "profile_name", shell_complete=_complete_profile_names,
              help="Connection profile name (from the profiles/ dir under CRM_HOME; default ~/.crm).")
@click.option("--password", help="Secret for this run (overrides the profile's stored secret).")
@click.option("--log-level",
              type=click.Choice(["debug", "info", "warning", "error"]),
              default=None,
              help="Log level (env: CRM_LOG_LEVEL). Default: warning.")
@click.option("--verbose", "verbose", is_flag=True,
              help="Alias for --log-level debug.")
@click.option("--log-format",
              type=click.Choice(["text", "json-line"]),
              default=None,
              help="Log output format (env: CRM_LOG_FORMAT). Default: text.")
@click.option("--auth-scheme",
              type=click.Choice(["ntlm", "kerberos", "negotiate", "oauth"]),
              default=None,
              help="Override the active profile's auth scheme for this run. "
                   "ntlm/kerberos/negotiate = on-prem; oauth = cloud.")
@click.option("--stage-only", "stage_only", is_flag=True,
              help="Stage metadata changes without publishing (env: CRM_STAGE_ONLY). "
                   "Forces every create/update command to --no-publish.")
@click.option("--retry-on-ambiguous", "retry_on_ambiguous", is_flag=True,
              help="Re-enable auto-retry of non-idempotent POST creates on "
                   "transport error / 429 / 503 (env: CRM_RETRY_ON_AMBIGUOUS). "
                   "Off by default: a lost POST response may have committed.")
@click.option("--cache-metadata", "cache_metadata", is_flag=True,
              help="Read entity definitions from the persistent on-disk cache "
                   "(env: CRM_CACHE_METADATA). Default off.")
@click.option("--refresh-metadata", "refresh_metadata", is_flag=True,
              help="Force-refresh the on-disk metadata cache on this call (one-shot; no env override).")
@click.option("--session", "session_name", default="default", help="Session name.")
@click.version_option(__version__, prog_name="crm")
@click.pass_context
def cli(ctx: click.Context, json_mode: bool, fields: str | None,
        jq_program: str | None, dry_run: bool,
        profile_name: str | None, password: str | None,
        log_level: str | None, verbose: bool, log_format: str | None,
        auth_scheme: str | None, stage_only: bool, retry_on_ambiguous: bool,
        cache_metadata: bool, refresh_metadata: bool,
        session_name: str):
    """Stateful CLI for Microsoft Dynamics 365 CE — on-prem v9.x (NTLM) or Dataverse online (OAuth), over the Web API."""
    force_utf8_output(sys.stdout)
    force_utf8_output(sys.stderr)
    _valid_levels = ("debug", "info", "warning", "error")
    _valid_fmts = ("text", "json-line")
    effective_level = log_level or os.environ.get("CRM_LOG_LEVEL") or "warning"
    if verbose:
        effective_level = "debug"
    if effective_level not in _valid_levels:
        raise click.BadParameter(
            f"{effective_level!r} is not a valid log level; choose from {_valid_levels}",
            param_hint="--log-level / CRM_LOG_LEVEL",
        )
    effective_fmt = log_format or os.environ.get("CRM_LOG_FORMAT") or "text"
    if effective_fmt not in _valid_fmts:
        raise click.BadParameter(
            f"{effective_fmt!r} is not a valid log format; choose from {_valid_fmts}",
            param_hint="--log-format / CRM_LOG_FORMAT",
        )
    setup_logging(level=effective_level, fmt=effective_fmt)  # type: ignore[arg-type]

    cli_ctx = ctx.ensure_object(CLIContext)
    # Record which of the five dual-position global options (#818) were supplied at
    # the ROOT position, so an injected leaf option can reject the same flag given
    # in both positions. Fresh set per invocation (per REPL line). ParameterSource
    # is imported from click.core (not top-level click) — pyright's bundled stubs
    # only export it there.
    from click.core import ParameterSource
    cli_ctx._root_positions = {
        kind
        for kind, pname in (
            ("json", "json_mode"), ("fields", "fields"), ("jq", "jq_program"),
            ("dry_run", "dry_run"), ("profile", "profile_name"),
        )
        if ctx.get_parameter_source(pname) == ParameterSource.COMMANDLINE
    }
    cli_ctx.json_mode = json_mode
    # Shaping is per-invocation, never sticky: each command (each REPL line)
    # re-decides whether to project, so a bare later line clears a prior --fields.
    # An empty/whitespace-only value is a usage error (nothing to project).
    cli_ctx.fields = None
    # --fields and --jq are two spellings of the same shaping seam; running both at
    # once is undefined, so it's a usage error (exit 2, ADR 0001) — checked before
    # parsing either so no backend/profile work happens.
    if fields is not None and jq_program is not None:
        raise click.UsageError("--fields and --jq are mutually exclusive")
    if fields is not None:
        cli_ctx.fields = _parse_fields_value(fields)
    # --jq (#736): compile the program NOW — before any profile resolution or
    # network call — so a syntactically invalid program fails fast with exit 2 and
    # provably issues no request (validate-before-backend). The jq module is imported
    # lazily, only when --jq is passed, to keep CLI cold-start cheap on the common
    # no-jq path (agents fire hundreds of one-shot invocations). --jq implies JSON
    # mode: a jq result has no meaningful human render.
    cli_ctx.jq_program = None
    if jq_program is not None:
        cli_ctx.jq_program = _compile_jq_value(jq_program)
        json_mode = True
        cli_ctx.json_mode = True
    cli_ctx.dry_run = dry_run
    # Per-invocation, NOT sticky: each command (each REPL line) re-decides whether
    # it opened a connection, so emit() never stamps identity onto a local verb
    # that follows a connecting one in the same REPL session (#624).
    cli_ctx.connection_resolved = False
    # Sticky options: in the REPL the same CLIContext is reused across lines, so only
    # overwrite when the user actually supplied the flag — otherwise prior values
    # (e.g., set by `crm profile add`) would be wiped on the next bare command.
    if profile_name is not None:
        cli_ctx.profile_name = profile_name
    if password is not None:
        cli_ctx.password = password
    cli_ctx.auth_scheme = auth_scheme
    # Sticky safety flag: once --stage-only (or CRM_STAGE_ONLY) is set, never clear it
    # back to False on a later bare REPL line that omits the token, which would silently
    # re-enable auto-publish and lose the safety guarantee.
    env_stage_only = os.environ.get("CRM_STAGE_ONLY", "").lower() in ("1", "true", "yes", "on")
    cli_ctx.stage_only = cli_ctx.stage_only or stage_only or env_stage_only
    cli_ctx.retry_on_ambiguous = retry_on_ambiguous
    env_cache = os.environ.get("CRM_CACHE_METADATA", "").lower() in ("1", "true", "yes", "on")
    cli_ctx.cache_metadata = cli_ctx.cache_metadata or cache_metadata or env_cache
    # Refresh is deliberately per-invocation (NOT sticky): a refresh is a one-shot action
    # and must not re-fire on every later REPL line. Compare cache_metadata above, which
    # is sticky so the REPL stays in cache mode once opted in.
    cli_ctx.refresh_metadata = refresh_metadata
    # Sticky session: the REPL re-invokes this callback for every typed line; a bare
    # line omits --session so Click passes the literal default "default", which would
    # silently clobber the session name set at REPL-launch time (#128). ParameterSource
    # (imported from click.core at the top of this callback) distinguishes an explicit
    # --session from Click's default fill.
    if ctx.get_parameter_source("session_name") == ParameterSource.COMMANDLINE:
        cli_ctx.session_name = session_name

    # Kick off the background update check (frozen-install upgrade notice). Cheap
    # guards run inline so machine/CI/--json paths never import the update module
    # (and its requests dependency) — keeping CLI startup lean. The authoritative
    # guard set lives in crm.core.update.is_check_enabled.
    #
    # A dual-position --json/--jq (#818) trails the subcommand, so the root-position
    # `json_mode` local does not yet reflect it (the leaf callback runs later). Use
    # the whole-argv guard signal computed in `_JsonAwareGroup.main`, so a trailing
    # --json/--jq gates the check exactly as a leading one — root and leaf placement
    # stay consistent. (The notice itself is gated on the final json_mode in the
    # result callback; this only governs whether the background probe starts.)
    json_for_guards = getattr(cli, "_json_for_guards", json_mode)
    _maybe_update_check(json_for_guards)

    if ctx.invoked_subcommand is None:
        if _suppress_bare_repl(json_mode):
            msg = "no subcommand given; run crm --help to list commands"
            if json_mode:
                _emit_error_envelope(msg)
            else:
                click.echo(f"Error: {msg}", err=True)
            raise click.exceptions.Exit(2)
        from crm.commands.repl import repl
        ctx.invoke(repl)


def _update_check_eligible(json_mode: bool) -> bool:
    """Cheap pre-check mirroring update.is_check_enabled, to gate the lazy import."""
    if json_mode:
        return False
    # A closed/detached stderr raises on isatty(); treat any failure as not-a-TTY
    # so the passive notice can never break an otherwise-unrelated command.
    try:
        if not sys.stderr.isatty():
            return False
    except Exception:
        return False
    return not (os.environ.get("CRM_NO_UPDATE_CHECK") or os.environ.get("CI"))


def _maybe_update_check(json_mode: bool) -> None:
    if not _update_check_eligible(json_mode):
        return
    import time
    from crm.core import update as update_mod
    update_mod.run_background_check(
        json_mode=json_mode, stderr_isatty=True, env=os.environ, now=time.time(),
    )


@cli.result_callback()
def _emit_update_notice(result: Any, **_kwargs: Any) -> None:
    """Print the one-line update notice (from cache) after a command completes."""
    ctx = click.get_current_context()
    # self-update owns its own update messaging; the running process still reports the
    # pre-update version, so the cached-version comparison would re-print the upgrade
    # notice right after a successful upgrade.
    if ctx.invoked_subcommand == "self-update":
        return
    json_mode = bool(getattr(ctx.obj, "json_mode", False))
    if not _update_check_eligible(json_mode):
        return
    from crm.core import update as update_mod
    update_mod.emit_pending_notice(
        json_mode=json_mode, stderr_isatty=True, env=os.environ,
    )


# Register PowerShell completion eagerly. Command modules are lazy-loaded, so a
# completion request never imports completion_registry on its own; Click's built-in
# bash/zsh/fish classes self-register at click import, but ours must be registered
# here (an always-imported module) before cli.main() runs — otherwise
# get_completion_class("powershell") returns None and completion silently emits
# nothing. completion_registry has no module-level crm.cli import, so this is safe.
from click.shell_completion import add_completion_class  # noqa: E402
from crm.commands.completion_registry import PowerShellComplete  # noqa: E402

add_completion_class(PowerShellComplete)


def main() -> None:
    """Console-script / ``python -m crm`` entry point.

    Pins ``prog_name="crm"`` so Click derives the completion env var
    (``_CRM_COMPLETE``) and usage/help text from ``crm`` — not the Windows binary
    basename ``crm.exe``, which would make Click look for ``_CRM_EXE_COMPLETE`` and
    break the generated completion script.
    """
    cli(prog_name="crm")


if __name__ == "__main__":
    main()
