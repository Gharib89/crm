"""crm — Click-based CLI + REPL for Microsoft Dynamics 365 CE — on-prem v9.x (NTLM) or
Dataverse online (OAuth).

Entry point: `crm` (installed) or `python -m crm`.

Running with no subcommand drops into the REPL. Each command supports `--json` for
machine-readable output. `--dry-run` previews writes without issuing them; reads
(GET) still run for real.

PowerShell completion is registered at import time, here, because command modules
load lazily: a completion request never imports `completion_registry` on its own,
and while Click's bash/zsh/fish classes self-register when click is imported, ours
must be registered on an always-imported path before `cli.main()` runs, or
`get_completion_class("powershell")` returns None and completion silently emits
nothing. `completion_registry` imports `crm.cli` only inside a function, so the
import is not circular.
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
from crm.utils.d365_types import AUTH_SCHEMES

if TYPE_CHECKING:
    from crm.core.legacy import LegacyClient
    from crm.utils.d365_backend import ConnectionProfile, D365Backend
from crm.commands._helpers import (
    _apply_jq,
    _normalize_odata_envelope,
    _project_fields,
    _project_table_columns,
    _sanitize,
    _short_repr,
    _strip_odata_keys,
    reject_empty_values,
)
from crm.commands._tty import _stdin_is_tty
from crm.utils.repl_skin import ReplSkin

FAILURE_EXIT_CODE = 1
"""Exit code for an operational failure. See ADR 0001."""


def force_utf8_output(stream: Any) -> None:
    """Reconfigure a text stream to UTF-8 so box-drawing output never crashes.

    On a default Windows console (cp1252) the human table/banner renderers emit
    box-drawing characters that cp1252 cannot encode, raising UnicodeEncodeError
    (#146a). TextIOWrapper.reconfigure (3.7+) flips the encoding in place.
    errors='replace' takes effect only when reconfigure succeeds; if the stream
    lacks reconfigure or the call raises (an already detached or non-seekable
    wrapper), the stream is left unchanged.
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
        pass


class CLIContext:
    """Per-invocation state shared across subcommands.

    The REPL reuses one CLIContext across lines and re-runs the root callback for
    each, so every field is either per-invocation (reset on each line) or sticky
    (kept until a line supplies the flag again).

    Per-invocation:

    - `fields`: the top-level keys the global `--fields` flag (#735) projects the
      curated `data` payload down to, or None for no shaping. A bare later line
      clears a prior `--fields`.
    - `jq_program`: the `jq.compile(...)` object behind `--jq` (#736), or None. It
      is compiled in the root callback, before any profile resolution or network
      call, so an invalid program fails with exit 2 and issues no request. Typed
      `Any` because jq is imported lazily to keep it out of cold start (ADR 0029).
      Setting it forces `json_mode`, since a jq result has no human render.
    - `_root_positions`: which of the five dual-position global options (#818)
      were supplied at the ROOT position, as kind strings ("json", "fields",
      "jq", "dry_run", "profile"). An injected leaf option reads it to reject the
      same flag given in both positions (ADR 0025).
    - `connection_resolved`: set by `backend()` whenever this command resolved a
      connection, so `emit()` stamps the serving profile and url only on
      envelopes that opened a backend (#624), never on a later REPL line (a local
      verb, say) whose backend is merely cached.
    - `refresh_metadata`: a refresh is a one-shot action and must not re-fire on
      every later REPL line.

    Sticky:

    - `profile_name` and `password` are overwritten only when the line supplies
      them, so a value set by `crm profile add` survives the next bare command.
    - `stage_only` (or `CRM_STAGE_ONLY`) is never cleared back to False by a bare
      line: that would silently re-enable auto-publish and lose the safety
      guarantee.
    - `cache_metadata` stays on once opted in, so the REPL stays in cache mode.
    - `session_name` changes only on an explicit `--session`: a bare line gets
      Click's literal default "default", which would clobber the session name set
      at REPL launch (#128).
    """

    def __init__(self):
        self.json_mode: bool = False
        self.dry_run: bool = False
        self.fields: list[str] | None = None
        self.jq_program: Any = None
        self.profile_name: str | None = None
        self._root_positions: set[str] = set()
        self.password: str | None = None
        self.auth_scheme: str | None = None
        self.stage_only: bool = False
        self.cache_metadata: bool = False
        self.refresh_metadata: bool = False
        self.retry_on_ambiguous: bool = False
        self.session_name: str = "default"
        self.connection_resolved: bool = False
        self.legacy_profile_name: str | None = None
        self._backend: D365Backend | None = None
        self._backend_key: tuple[str | None, str | None, bool, str | None, bool] | None = None
        self.skin: ReplSkin = ReplSkin("d365", version=__version__)

    def emit(
        self,
        ok: bool,
        data: Any = None,
        *,
        error: str | None = None,
        meta: dict | None = None,
        table: dict | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        """Print either a JSON envelope or a human-friendly representation.

        `warnings` is the structured advisory channel (#64): each entry is
        appended to `meta.warnings` (never clobbering any already there) in JSON
        mode, or printed via skin.warning in human mode. A pre-existing
        `meta["warnings"]` that is not a list is coerced to a single-item list
        first, so a stray scalar can never split into characters or raise. A
        fresh dict is built so the caller's `meta` is not mutated.

        JSON `data` is curated here, once, so every command is consistent (ADR
        0008), then shaped by `--fields` or `--jq` (ADR 0023). A dry-run mutation
        preview (`data._dry_run`) is a verbatim echo of the request that would be
        sent and is never curated, so request-shape keys such as
        `<nav>@odata.bind` and `@odata.type` survive and the preview matches the
        wire payload; read results under `--dry-run` carry no marker and are
        curated.

        A `--jq` program that compiled but fails at eval time leaves the payload
        unusable, so it goes through the canonical error path (which stamps
        `meta.dry_run` and keeps the `ok: false` shape) and drops the paging meta
        that described the discarded payload. The `return` after it guards against
        falling through should `emit(False)` ever stop exiting in JSON mode.

        `meta.dry_run` (#61) is keyed off the invocation flag, not off the data,
        so list-shaped batch and poll previews carry it too.

        A success envelope from a command that resolved a backend names the
        serving profile and Web API base (#624), so an agent can tell which org a
        result came from. Error envelopes keep their reserved
        {status, code, category, retryable} meta; the backend must have been
        resolved by this command, so a later REPL line over a cached backend stays
        clean; and `_backend` must still be live, since `profile add` invalidates
        it before emitting.
        """
        if self.json_mode:
            envelope: dict[str, Any] = {"ok": ok}
            shape_warnings: list[str] = []
            if data is not None:
                if not (isinstance(data, dict) and "_dry_run" in data):
                    data, paging = _normalize_odata_envelope(data)
                    if paging:
                        meta = {**(meta or {}), **paging}
                    data = _strip_odata_keys(data)
                if ok and self.fields is not None:
                    data, shape_warnings = _project_fields(data, self.fields)
                elif ok and self.jq_program is not None:
                    data, jq_error = _apply_jq(data, self.jq_program)
                    if jq_error is not None:
                        self.emit(False, error=f"--jq: {jq_error}")
                        return
                envelope["data"] = _sanitize(data)
            if error:
                envelope["error"] = error
            if self.dry_run:
                meta = {**(meta or {}), "dry_run": True}
            all_warnings = [*(warnings or []), *shape_warnings]
            if all_warnings:
                existing = (meta or {}).get("warnings") or []
                if not isinstance(existing, list):
                    existing = [existing]
                meta = {**(meta or {}), "warnings": [*existing, *all_warnings]}
            if ok and self.connection_resolved and self._backend is not None:
                meta = {
                    **(meta or {}),
                    "profile": self._backend.profile.name,
                    "url": self._backend.profile.api_base,
                }
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

    def _effective_profile(self) -> str | None:
        """Select the profile: `--profile` flag, else the session's active profile,
        else the setup wizard.

        Falling back to the saved active profile is what makes `crm profile use`
        persist across later commands (#130); a pointer to a deleted profile is
        ignored. The wizard runs only on an interactive terminal outside `--json`,
        so a new user goes from zero to working; an agent or CI run instead gets
        the actionable "run `crm profile add`" error from `resolve_credentials()`
        and never hangs on a prompt.
        """
        from crm.core import session as session_mod

        effective_profile = self.profile_name
        if effective_profile is None:
            state = session_mod.load_session(self.session_name)
            candidate = state.get("active_profile")
            if candidate and session_mod.profile_path(candidate).is_file():
                effective_profile = candidate

        if effective_profile is None and _stdin_is_tty() and not self.json_mode:
            import click as _click

            from crm.commands.profile import profile_add

            _click.echo("No profile configured yet. Let's set one up:")
            _click.get_current_context().invoke(profile_add)
            state = session_mod.load_session(self.session_name)
            effective_profile = state.get("active_profile")
        return effective_profile

    def backend(self) -> D365Backend:
        """The connected backend, rebuilt when the profile or connection options change.

        Marks `connection_resolved` for a cached backend as well as a fresh one, so
        `emit()` stamps `meta.profile` and `meta.url` (#624).
        """
        from crm.core import connection as conn_mod
        from crm.utils.d365_backend import D365Backend

        effective_profile = self._effective_profile()
        key = (
            effective_profile,
            self.password,
            self.dry_run,
            self.auth_scheme,
            self.retry_on_ambiguous,
        )
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
                resolved.profile,
                resolved.password,
                dry_run=self.dry_run,
                retry_on_ambiguous=self.retry_on_ambiguous,
            )
            self._backend_key = key
        self.connection_resolved = True
        return self._backend

    def saved_profile(self) -> ConnectionProfile | None:
        """The selected saved profile, or None when no profile is selected or saved.

        Reads the profile file only: no secret lookup, prompt or connection.
        """
        from crm.core import session as session_mod

        name = self._effective_profile()
        if name is None or not session_mod.profile_path(name).is_file():
            return None
        return session_mod.load_profile(name)

    def legacy_client(self) -> LegacyClient:
        """Build the read-only SOAP client for a crm2011 profile (`crm legacy`).

        Selects the profile and resolves its secret the way backend() does, but
        refuses a non-crm2011 profile or a non-NTLM --auth-scheme before any
        secret lookup or prompt. Records the selected name in `legacy_profile_name`
        so a 401 hint can name the profile.
        """
        from crm.core import connection as conn_mod
        from crm.core.legacy import LegacyClient, require_crm2011

        if self.auth_scheme not in (None, "ntlm"):
            raise click.UsageError(
                f"--auth-scheme {self.auth_scheme} does not apply to a crm2011 profile, "
                "which supports only HTTP NTLM."
            )
        name = self._effective_profile()
        self.legacy_profile_name = name
        saved = self.saved_profile()
        if saved is not None:
            require_crm2011(saved)
        resolved = conn_mod.resolve_credentials(
            profile_name=name,
            password_override=self.password,
            allow_prompt=_stdin_is_tty() and not self.json_mode,
        )
        return LegacyClient(resolved.profile, resolved.password)

    def materialized_backend(self) -> D365Backend | None:
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
    omit `meta`; non-usage ClickException failures pass an empty `meta` object.
    """
    env: dict[str, Any] = {"ok": False, "error": message}
    if meta is not None:
        env["meta"] = meta
    click.echo(json.dumps(env, indent=2, default=str))


def _suppress_bare_repl(json_mode: bool) -> bool:
    """Whether bare `crm` (no subcommand) must fail fast instead of dropping into
    the interactive REPL. True when the caller is clearly non-interactive: --json,
    an explicit CRM_NO_REPL opt-out, or a non-TTY stdin (piped/redirected, as
    agents and CI invoke it). A proactive isatty probe — intentionally stronger
    than waiting for the REPL's EOF handler so a bare invocation never hangs.
    """
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
    the flag; `--fields` and `--jq` are among them, so a '--json' passed as their
    value does not flip skinning. **Accepted false positive (#818, ADR 0025):** a leaf
    option that takes a value is not in `value_opts`, so a literal '--json' passed as
    *its* value (e.g. `entity get accounts --select --json`) is mistaken for the flag
    and, in an invocation that also has a usage error, renders that error as a JSON
    envelope. Purely cosmetic (the error text is unchanged), it only affects error
    skinning, and is pinned by a test — accepted over reimplementing Click's
    per-command tokenizer here just to shape usage-error output.
    """
    if not args:
        return False
    value_opts = {
        "--profile",
        "--password",
        "--log-level",
        "--log-format",
        "--auth-scheme",
        "--session",
        "--fields",
        "--jq",
    }
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--json":
            return True
        if tok in value_opts:
            i += 2
            continue
        i += 1
    return False


_DUALPOS_TOKENS: dict[str, str] = {
    "json": "--json",
    "fields": "--fields",
    "jq": "--jq",
    "profile": "--profile",
    "dry_run": "--dry-run",
}
"""The dual-position global options (#818), valid before or after the subcommand.
See ADR 0025."""
_DUALPOS_HELP_SUFFIX = "  [global; also valid before the command]"


def _parse_fields_value(value: str) -> list[str]:
    """Parse a ``--fields`` value into its ordered key list, or raise a usage error.

    Shared by the root callback and the injected leaf option so both positions
    validate identically. An empty / whitespace-only value has nothing to project
    and is a usage error (exit 2).
    """
    parsed = [f.strip() for f in value.split(",") if f.strip()]
    if not parsed:
        raise click.BadParameter("no field names given", param_hint="--fields")
    return parsed


def _compile_jq_value(value: str) -> Any:
    """Compile a ``--jq`` program, or raise a usage error (exit 2).

    Shared by root and leaf so an invalid program fails fast — before any profile
    resolution or network call — in either position. The `jq` module is imported
    lazily so the common no-jq path never pays for it.
    """
    import jq

    try:
        return jq.compile(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--jq") from exc


def _reject_fields_jq_conflict(cli_ctx: CLIContext) -> None:
    """Cross-position guard: ``--fields`` and ``--jq`` are two spellings of the same
    shaping seam, so having both set — in any combination of positions — is a usage
    error (exit 2), matching the root-position check in the root callback. See ADR
    0023.
    """
    if cli_ctx.fields is not None and cli_ctx.jq_program is not None:
        raise click.UsageError("--fields and --jq are mutually exclusive")


def _dualpos_callback(kind: str, token: str):
    """Build the parse callback for one injected dual-position option.

    Fires for every parse, but does work only when the option was actually supplied
    at the leaf (``ParameterSource.COMMANDLINE``). It then rejects the same option
    given in BOTH positions (decision 2) and otherwise applies the value through the
    exact merge semantics the root callback uses (decision 4): `--profile` sticky,
    the rest per-invocation, `--jq` implying `--json` and compiling now, `--fields`
    /`--jq` mutually exclusive cross-position.
    """

    def _cb(ctx: click.Context, param: click.Parameter, value: Any) -> Any:
        from click.core import ParameterSource

        if ctx.get_parameter_source(param.name) != ParameterSource.COMMANDLINE:
            return value
        cli_ctx = ctx.find_root().ensure_object(CLIContext)
        if kind in cli_ctx._root_positions:
            raise click.UsageError(
                f"{token} was provided both before and after the subcommand; pick one."
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
    and `expose_value=False` so leaf command signatures are untouched.
    """
    return [
        click.Option(
            ["--json", "_dualpos_json"],
            is_flag=True,
            default=False,
            expose_value=False,
            callback=_dualpos_callback("json", "--json"),
            help="Emit machine-readable JSON output." + _DUALPOS_HELP_SUFFIX,
        ),
        click.Option(
            ["--fields", "_dualpos_fields"],
            default=None,
            metavar="KEY[,KEY...]",
            expose_value=False,
            callback=_dualpos_callback("fields", "--fields"),
            help="Project the data payload down to these comma-separated top-level "
            "keys." + _DUALPOS_HELP_SUFFIX,
        ),
        click.Option(
            ["--jq", "_dualpos_jq"],
            default=None,
            metavar="PROGRAM",
            expose_value=False,
            callback=_dualpos_callback("jq", "--jq"),
            help="Run a jq program over the curated data payload; the result "
            "replaces data. Implies --json." + _DUALPOS_HELP_SUFFIX,
        ),
        click.Option(
            ["--profile", "_dualpos_profile"],
            default=None,
            shell_complete=_complete_profile_names,
            expose_value=False,
            callback=_dualpos_callback("profile", "--profile"),
            help="Connection profile name." + _DUALPOS_HELP_SUFFIX,
        ),
        click.Option(
            ["--dry-run", "_dualpos_dry_run"],
            is_flag=True,
            default=False,
            expose_value=False,
            callback=_dualpos_callback("dry_run", "--dry-run"),
            help="Preview writes without issuing them; reads run normally." + _DUALPOS_HELP_SUFFIX,
        ),
    ]


def _leaf_declares_token(cmd: click.Command, token: str) -> bool:
    """Whether a command already declares an option under `token` (primary or `--no-`
    secondary form).
    """
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
    `profile delete-password --profile` keep their required local meaning.
    """
    for opt in _make_dualpos_options():
        if not _leaf_declares_token(cmd, opt.opts[0]):
            cmd.params.append(opt)


def _inject_dualpos_tree(cmd: click.Command) -> None:
    """Walk a resolved top-level command, inject the dual-position options into
    every leaf beneath it, and make an explicit empty value a usage error on every
    command's optional text options (`reject_empty_values`, #1417).

    Subgroups below the root are eager ``click.Group``s, so `.commands` is populated
    and the walk reaches every leaf; only the root group itself is lazy, and it is
    never passed here (injection is driven from the root's own `get_command`).
    """
    if isinstance(cmd, click.Group):
        for sub in cmd.commands.values():
            _inject_dualpos_tree(sub)
    else:
        _inject_dualpos_options(cmd)
    reject_empty_values(cmd)


class _JsonAwareGroup(click.Group):
    """Root group that intercepts Click usage errors for consistent rendering: a
    global root option placed *after* the subcommand (which Click rejects as
    NoSuchOption) becomes a position hint, and under --json every usage error
    renders as the standard JSON envelope on stdout. Exit code is preserved (2,
    per ADR 0001).
    """

    def _global_option_names(self) -> set[str]:
        """Every option token the root group declares — primary and secondary
        (`--no-*`) forms. Derived from the live params so the set never drifts from
        the actual root options as global flags are added or renamed.
        """
        names: set[str] = set()
        for param in self.params:
            if isinstance(param, click.Option):
                names.update(param.opts)
                names.update(param.secondary_opts)
        return names

    def _global_option_hint(self, exc: click.NoSuchOption) -> str | None:
        """If the rejected option is a root-level global option, return a message
        telling the user to place it before the subcommand; otherwise None (so a
        genuinely unknown option keeps Click's error, incl. its "Did you mean").
        """
        name = exc.option_name
        if name not in self._global_option_names():
            return None
        parts = (exc.ctx.command_path.split() if exc.ctx else ["crm"]) or ["crm"]
        corrected = " ".join([parts[0], name, *parts[1:]])
        return f"{name!r} is a global option; place it before the command:\n  {corrected} ..."

    def main(self, args=None, **kwargs):  # type: ignore[override]
        """Run Click non-standalone so its parse and usage errors reach us, re-render
        them, then replicate standalone exit semantics.

        A misplaced global flag gets a position hint; under `--json` a usage error
        becomes the envelope; otherwise Click's own rendering is kept. `super()`
        returns the command's value on success, or the Exit code as an int when
        `emit()` raised Exit. EPIPE and EOFError/KeyboardInterrupt are handled
        inside Click even non-standalone, so they need no replication.

        `_json_for_guards` is the whole-argv JSON signal for the root callback's
        passive guards (#818): a dual-position `--json` or `--jq` may trail the
        subcommand, where the callback's root-position `json_mode` cannot see it,
        and argv lives only at this seam. `--jq` implies `--json`. It is recomputed
        per invocation, including each REPL line, which re-enters `main()`.
        """
        argv = list(args) if args is not None else sys.argv[1:]
        json_mode = _json_mode_active(argv)
        self._json_for_guards = json_mode or "--jq" in argv
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
            raise click.exceptions.Exit(exc.exit_code) from exc
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

    def _render_usage_error(self, exc: click.ClickException, json_mode: bool, standalone: bool):
        """Render a non-global-flag ClickException exactly as before this hint was
        added, except that under --json any ClickException becomes a stdout JSON
        envelope. A direct human invocation keeps Click's standalone rendering,
        and the non-standalone human path still propagates to the REPL's
        skin.error handler. Under `--json` a usage error omits meta and keeps Click's
        exit 2, while any other ClickException carries an empty meta and Click's
        default exit 1 (ADR 0001).
        """
        if json_mode:
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
    still imports every module to render short help — an accepted trade-off.
    """

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
        "inventory": "crm.commands.inventory:inventory_group",
        "legacy": "crm.commands.legacy:legacy_group",
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
    """Click command name to "module:attribute": the sole top-level command
    registry, so a new top-level command is exposed only once added here."""

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
        `get_close_matches` cutoff is reused unchanged).
        """
        try:
            return super().resolve_command(ctx, args)
        except click.exceptions.NoSuchCommand as exc:
            raise click.exceptions.NoSuchCommand(
                exc.command_name,
                possibilities=self.list_commands(ctx),
                ctx=ctx,
            ) from None

    def get_command(self, ctx, cmd_name):
        """Resolve a top-level command, importing its module on first use, and
        inject the dual-position global options (#818) into every leaf beneath it,
        with the empty-value check (#1417) on every command.

        Injection happens here, at resolution, because the root is lazy and the
        modules load here. See ADR 0025.

        A failed import surfaces as a clean ClickException ("Error: ..." with no
        traceback) rather than a raw ImportError, which is especially confusing in
        a frozen build. A broken entry is a packaging or wiring bug that the sync
        test in `test_lazy_imports.py` catches in CI.
        """
        eager = super().get_command(ctx, cmd_name)
        if eager is not None:
            _inject_dualpos_tree(eager)
            return eager
        target = self._lazy_commands.get(cmd_name)
        if target is None:
            return None
        import importlib

        module_name, attr = target.split(":")
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


def _complete_profile_names(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[str]:
    """Dynamic ``--profile`` value completion: saved profile names.

    Local file read only, never a network call — shell completion spawns a
    fresh ``crm`` process per Tab, so cold-start/import cost is kept minimal
    (deferred import) and no backend/connection is ever touched. Best-effort: an
    unreadable CRM_HOME yields no completions rather than a crash.
    """
    from crm.core.session import list_profiles

    try:
        return [name for name in list_profiles() if name.startswith(incomplete)]
    except OSError:
        return []


def _completion_profile(ctx: click.Context):
    """Resolve the profile whose metadata cache OS-shell completion should read:
    the ``--profile`` on the line if present, else the active profile of the
    session named on the line (``--session``), else the default session. The
    active profile is per-session, so ignoring ``--session`` would resolve the
    wrong org.

    Local reads only (profile file + session pointer) — shell completion spawns
    a fresh ``crm`` per Tab, so this never touches the network. Returns a loaded
    ``ConnectionProfile`` or ``None`` (no profile / unreadable / missing).
    """
    from crm.core import session as session_mod

    root_params = ctx.find_root().params
    name = root_params.get("profile_name")
    if not name:
        try:
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


def _complete_entity_set_names(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[str]:
    """Dynamic entity-set-name completion for OS-shell positional args.

    **Disk-cache only, never a network call** — shell completion runs a fresh
    ``crm`` process per Tab, so a per-keystroke round-trip is unacceptable and
    cold-start must stay cheap. Reads the on-disk metadata cache for the
    resolved profile (populated by earlier live runs / ``--cache-metadata``);
    a cache miss returns no completions silently. Best-effort — never raises.
    """
    import time

    from crm.core import metadata_cache

    try:
        profile = _completion_profile(ctx)
        if profile is None:
            return []
        definitions = metadata_cache.read_definitions(profile, now=time.time())
        if definitions is None:
            return []
        return [
            d["set_name"]
            for d in definitions
            if d["set_name"] and d["set_name"].startswith(incomplete)
        ]
    except Exception:  # noqa: BLE001
        return []


@click.group(
    cls=_LazyJsonAwareGroup,
    name="crm",
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option("--json", "json_mode", is_flag=True, help="Emit machine-readable JSON output.")
@click.option(
    "--fields",
    "fields",
    default=None,
    metavar="KEY[,KEY...]",
    help="Project the data payload down to these comma-separated top-level "
    "keys (JSON: per row/record; human: table columns). Applied after "
    "curation; the ok/error/meta envelope is untouched.",
)
@click.option(
    "--jq",
    "jq_program",
    default=None,
    metavar="PROGRAM",
    help="Run a jq program over the curated data payload; the result "
    "replaces data in the envelope. Implies --json. Mutually exclusive "
    "with --fields. An invalid program is a usage error (exit 2).",
)
@click.option(
    "--dry-run", is_flag=True, help="Preview writes without issuing them; reads run normally."
)
@click.option(
    "--profile",
    "profile_name",
    shell_complete=_complete_profile_names,
    help="Connection profile name (from the profiles/ dir under CRM_HOME; default ~/.crm).",
)
@click.option("--password", help="Secret for this run (overrides the profile's stored secret).")
@click.option(
    "--log-level",
    type=click.Choice(["debug", "info", "warning", "error"]),
    default=None,
    help="Log level (env: CRM_LOG_LEVEL). Default: warning.",
)
@click.option("--verbose", "verbose", is_flag=True, help="Alias for --log-level debug.")
@click.option(
    "--log-format",
    type=click.Choice(["text", "json-line"]),
    default=None,
    help="Log output format (env: CRM_LOG_FORMAT). Default: text.",
)
@click.option(
    "--auth-scheme",
    type=click.Choice(AUTH_SCHEMES),
    default=None,
    help="Override the active profile's auth scheme for this run. "
    "ntlm/kerberos/negotiate/adfs = on-prem; oauth = cloud.",
)
@click.option(
    "--stage-only",
    "stage_only",
    is_flag=True,
    help="Stage metadata changes without publishing (env: CRM_STAGE_ONLY). "
    "Forces every create/update command to --no-publish.",
)
@click.option(
    "--retry-on-ambiguous",
    "retry_on_ambiguous",
    is_flag=True,
    help="Re-enable auto-retry of non-idempotent POST creates on "
    "transport error / 429 / 503 (env: CRM_RETRY_ON_AMBIGUOUS). "
    "Off by default: a lost POST response may have committed.",
)
@click.option(
    "--cache-metadata",
    "cache_metadata",
    is_flag=True,
    help="Read entity definitions from the persistent on-disk cache "
    "(env: CRM_CACHE_METADATA). Default off.",
)
@click.option(
    "--refresh-metadata",
    "refresh_metadata",
    is_flag=True,
    help="Force-refresh the on-disk metadata cache on this call (one-shot; no env override).",
)
@click.option("--session", "session_name", default="default", help="Session name.")
@click.version_option(__version__, prog_name="crm")
@click.pass_context
def cli(
    ctx: click.Context,
    json_mode: bool,
    fields: str | None,
    jq_program: str | None,
    dry_run: bool,
    profile_name: str | None,
    password: str | None,
    log_level: str | None,
    verbose: bool,
    log_format: str | None,
    auth_scheme: str | None,
    stage_only: bool,
    retry_on_ambiguous: bool,
    cache_metadata: bool,
    refresh_metadata: bool,
    session_name: str,
):
    """Stateful CLI for Microsoft Dynamics 365 CE — on-prem v9.x (NTLM) or Dataverse online
    (OAuth), over the Web API.
    """
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
    from click.core import ParameterSource

    cli_ctx._root_positions = {
        kind
        for kind, pname in (
            ("json", "json_mode"),
            ("fields", "fields"),
            ("jq", "jq_program"),
            ("dry_run", "dry_run"),
            ("profile", "profile_name"),
        )
        if ctx.get_parameter_source(pname) == ParameterSource.COMMANDLINE
    }
    cli_ctx.json_mode = json_mode
    cli_ctx.fields = None
    if fields is not None and jq_program is not None:
        raise click.UsageError("--fields and --jq are mutually exclusive")
    if fields is not None:
        cli_ctx.fields = _parse_fields_value(fields)
    cli_ctx.jq_program = None
    if jq_program is not None:
        cli_ctx.jq_program = _compile_jq_value(jq_program)
        json_mode = True
        cli_ctx.json_mode = True
    cli_ctx.dry_run = dry_run
    cli_ctx.connection_resolved = False
    cli_ctx.legacy_profile_name = None
    if profile_name is not None:
        cli_ctx.profile_name = profile_name
    if password is not None:
        cli_ctx.password = password
    cli_ctx.auth_scheme = auth_scheme
    env_stage_only = os.environ.get("CRM_STAGE_ONLY", "").lower() in ("1", "true", "yes", "on")
    cli_ctx.stage_only = cli_ctx.stage_only or stage_only or env_stage_only
    cli_ctx.retry_on_ambiguous = retry_on_ambiguous
    env_cache = os.environ.get("CRM_CACHE_METADATA", "").lower() in ("1", "true", "yes", "on")
    cli_ctx.cache_metadata = cli_ctx.cache_metadata or cache_metadata or env_cache
    cli_ctx.refresh_metadata = refresh_metadata
    if ctx.get_parameter_source("session_name") == ParameterSource.COMMANDLINE:
        cli_ctx.session_name = session_name

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


reject_empty_values(cli)


def _update_check_eligible(json_mode: bool) -> bool:
    """Cheap pre-check mirroring update.is_check_enabled, to gate the lazy import.

    A closed or detached stderr raises on isatty(); any failure counts as not a
    TTY, so the passive notice can never break an otherwise unrelated command.
    """
    if json_mode:
        return False
    try:
        if not sys.stderr.isatty():
            return False
    except Exception:
        return False
    return not (os.environ.get("CRM_NO_UPDATE_CHECK") or os.environ.get("CI"))


def _maybe_update_check(json_mode: bool) -> None:
    """Start the background update check behind the frozen-install upgrade notice.

    The cheap guards run inline so machine, CI and `--json` runs never import the
    update module and its requests dependency, keeping startup lean (ADR 0029); the
    authoritative guard set is `crm.core.update.is_check_enabled`. The root
    callback passes the whole-argv JSON signal from `_JsonAwareGroup.main` rather
    than its own `json_mode`, because a dual-position `--json` or `--jq` (#818)
    trailing the subcommand is parsed only later by the leaf, and root and leaf
    placement must gate the check alike. The notice itself is gated on the final
    `json_mode` in the result callback.
    """
    if not _update_check_eligible(json_mode):
        return
    import time

    from crm.core import update as update_mod

    update_mod.run_background_check(
        json_mode=json_mode,
        stderr_isatty=True,
        env=os.environ,
        now=time.time(),
    )


def _deferred_result_eligible(json_mode: bool) -> bool:
    """Gate for reporting a detached finisher's outcome: human TTY only.

    Deliberately not `_update_check_eligible`: opting out of update *checks*
    (`CRM_NO_UPDATE_CHECK`, `CI`) must not silence the news that an update failed.
    """
    if json_mode:
        return False
    try:
        return sys.stderr.isatty()
    except Exception:
        return False


def _deferred_update_recorded() -> bool:
    """Whether a detached finisher left an outcome to report (#937).

    The filename is repeated from `crm.core.update.result_path` rather than read
    from it: this runs after *every* command, and importing the update module for a
    file that is almost never there would put that cost on all of them.
    """
    from crm.core.state_home import state_home

    return (state_home() / "update-result.json").exists()


@cli.result_callback()
def _emit_update_notice(result: Any, **_kwargs: Any) -> None:
    """Print the one-line update notice (from cache) after a command completes.

    It first reports a deferred Windows swap: a detached finisher applies it after
    the `crm self-update` that staged it has exited, so only a later command can
    report the outcome. The two checks before that report are a cheap pre-gate on
    importing the update module, not the authority: `emit_update_result_notice`
    re-proves both for its own callers, so the arguments passed here are a
    formality.

    `self-update` is skipped for the upgrade notice because it owns its own update
    messaging, and the running process still reports the pre-update version, so the
    cached-version comparison would re-print the notice right after a successful
    upgrade.
    """
    ctx = click.get_current_context()
    json_mode = bool(getattr(ctx.obj, "json_mode", False))
    if _deferred_result_eligible(json_mode) and _deferred_update_recorded():
        from crm.core import update as update_mod

        update_mod.emit_update_result_notice(json_mode=False, stderr_isatty=True)
    if ctx.invoked_subcommand == "self-update":
        return
    if not _update_check_eligible(json_mode):
        return
    from crm.core import update as update_mod

    update_mod.emit_pending_notice(
        json_mode=json_mode,
        stderr_isatty=True,
        env=os.environ,
    )


from click.shell_completion import add_completion_class  # noqa: E402

from crm.commands.completion_registry import PowerShellComplete  # noqa: E402

add_completion_class(PowerShellComplete)


def main() -> None:
    """Console-script / ``python -m crm`` entry point.

    Pins ``prog_name="crm"`` so Click derives the completion env var
    (``_CRM_COMPLETE``) and usage/help text from ``crm`` — not the Windows binary
    basename ``crm.exe``, which would make Click look for ``_CRM_EXE_COMPLETE`` and
    break the generated completion script.

    ``python -m crm.cli`` runs this file as ``__main__`` while the command modules
    import it again as ``crm.cli``, so ``pass_ctx`` would look for that second
    module's ``CLIContext``, find none and drop every global flag (#1313). The
    ``__main__`` guard therefore re-enters through the importable module, so both
    sides share one class.
    """
    cli(prog_name="crm")


if __name__ == "__main__":
    from crm.cli import main as _main

    _main()
