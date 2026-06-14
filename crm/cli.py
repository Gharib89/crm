"""crm — Click-based CLI + REPL for Dynamics 365 CE on-prem 9.x.

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
from crm.commands._helpers import _sanitize, _short_repr
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
        self.profile_name: str | None = None
        self.password: str | None = None
        self.auth_scheme: str | None = None
        self.stage_only: bool = False
        self.cache_metadata: bool = False
        self.refresh_metadata: bool = False
        self.retry_on_ambiguous: bool = False
        self.session_name: str = "default"
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
            if data is not None:
                envelope["data"] = _sanitize(data)
            if error:
                envelope["error"] = error
            # Canonical dry-run signal (#61): keyed off the invocation flag, not
            # data-sniffing, so list-shaped batch/poll previews are covered too.
            # Build a fresh dict so the caller's meta is not mutated.
            if self.dry_run:
                meta = {**(meta or {}), "dry_run": True}
            if warnings:
                existing = (meta or {}).get("warnings") or []
                if not isinstance(existing, list):
                    existing = [existing]
                meta = {**(meta or {}), "warnings": [*existing, *warnings]}
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


def _emit_usage_envelope(message: str) -> None:
    """Print the standard {ok: false, error: ...} JSON envelope for a usage error."""
    click.echo(json.dumps({"ok": False, "error": message}, indent=2, default=str))


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
    signal. The root --json must precede the subcommand and is always present in argv
    for a real --json invocation, so argv is the reliable source. The parsed
    CLIContext.json_mode is deliberately NOT consulted: the root callback may not have
    run yet when a usage error fires, and in the REPL it carries a stale value from a
    prior --json line, which would mis-skin a subsequent human-mode error.

    Only the leading run of root-level option tokens (everything before the first
    subcommand token) is considered, and a value consumed by a preceding
    value-taking root option is skipped — so a literal '--json' passed as an option
    value (e.g. `entity get accounts --select --json`) is not mistaken for the flag."""
    if not args:
        return False
    # Root options that consume the following token as their value; '--json' sitting
    # in such a slot is a value, not the root flag.
    value_opts = {
        "--profile", "--password", "--log-level", "--log-format",
        "--auth-scheme", "--session",
    }
    i = 0
    while i < len(args):
        tok = args[i]
        if not tok.startswith("-"):
            # First subcommand token reached; the root --json must appear before it.
            return False
        if tok == "--json":
            return True
        if tok in value_opts:
            i += 2  # skip the option and its value
            continue
        i += 1
    return False


class _JsonAwareGroup(click.Group):
    """Root group that, under --json, renders Click usage errors as the standard
    JSON envelope on stdout while preserving the exit code (2, per ADR 0001)."""

    def main(self, args=None, **kwargs):  # type: ignore[override]
        argv = list(args) if args is not None else sys.argv[1:]
        json_mode = _json_mode_active(argv)
        if not json_mode:
            return super().main(args=args, **kwargs)

        # Under --json, intercept Click usage errors so they render as the standard
        # envelope on stdout. Run non-standalone so the UsageError reaches us instead
        # of Click printing raw text to stderr; otherwise replicate standalone exit
        # semantics so the operational-failure (Exit) / Abort paths are unchanged.
        # In non-standalone mode super().main() returns the command's value on
        # success, or the Exit code (an int) when emit() raised Exit.
        standalone = kwargs.pop("standalone_mode", True)
        try:
            rv = super().main(args=args, standalone_mode=False, **kwargs)
        except click.UsageError as exc:
            _emit_usage_envelope(exc.format_message())
            if standalone:
                sys.exit(exc.exit_code)
            raise click.exceptions.Exit(exc.exit_code)
        except click.exceptions.Abort:
            if standalone:
                click.echo("Aborted!", file=sys.stderr)
                sys.exit(1)
            raise
        if standalone:
            sys.exit(rv if isinstance(rv, int) else 0)
        return rv


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
        "batch": "crm.commands.batch:batch_cmd",
        "completion": "crm.commands.completion:completion_group",
        "connection": "crm.commands.connection:connection_group",
        "data": "crm.commands.data:data_group",
        "describe": "crm.commands.describe:describe_cmd",
        "doctor": "crm.commands.connection:doctor_command",
        "entity": "crm.commands.entity:entity_group",
        "form": "crm.commands.form:form_group",
        "metadata": "crm.commands.metadata:metadata_group",
        "plugin": "crm.commands.plugin:plugin_group",
        "profile": "crm.commands.profile:profile_group",
        "query": "crm.commands.query:query_group",
        "repl": "crm.commands.repl:repl",
        "ribbon": "crm.commands.ribbon:ribbon_group",
        "service-document": "crm.commands.batch:service_document_cmd",
        "scaffold": "crm.commands.scaffold:scaffold_group",
        "security": "crm.commands.security:security_group",
        "self-update": "crm.commands.self_update:self_update_cmd",
        "session": "crm.commands.session:session_group",
        "skill": "crm.commands.skill:skill_group",
        "sla": "crm.commands.sla:sla_group",
        "solution": "crm.commands.solution:solution_group",
        "translation": "crm.commands.translation:translation_group",
        "view": "crm.commands.view:view_group",
        "webresource": "crm.commands.webresource:webresource_group",
        "workflow": "crm.commands.workflow:workflow_group",
    }

    def list_commands(self, ctx):
        return sorted({*self._lazy_commands, *super().list_commands(ctx)})

    def get_command(self, ctx, cmd_name):
        eager = super().get_command(ctx, cmd_name)
        if eager is not None:
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
        return command


# ── Root group ──────────────────────────────────────────────────────────


@click.group(cls=_LazyJsonAwareGroup, name="crm", invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--json", "json_mode", is_flag=True, help="Emit machine-readable JSON output.")
@click.option("--dry-run", is_flag=True,
              help="Preview writes without issuing them; reads run normally.")
@click.option("--profile", "profile_name", help="Connection profile name (from ~/.crm/profiles).")
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
def cli(ctx: click.Context, json_mode: bool, dry_run: bool,
        profile_name: str | None, password: str | None,
        log_level: str | None, verbose: bool, log_format: str | None,
        auth_scheme: str | None, stage_only: bool, retry_on_ambiguous: bool,
        cache_metadata: bool, refresh_metadata: bool,
        session_name: str):
    """Stateful CLI for Dynamics 365 CE on-prem 9.x (Web API)."""
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
    cli_ctx.json_mode = json_mode
    cli_ctx.dry_run = dry_run
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
    # silently clobber the session name set at REPL-launch time (#128).
    # Imported from click.core (not top-level click) because pyright's bundled click
    # stubs only export ParameterSource there; `click.ParameterSource` / `from click
    # import ParameterSource` fail strict type-checking even though both work at runtime.
    from click.core import ParameterSource
    if ctx.get_parameter_source("session_name") == ParameterSource.COMMANDLINE:
        cli_ctx.session_name = session_name

    # Kick off the background update check (frozen-install upgrade notice). Cheap
    # guards run inline so machine/CI/--json paths never import the update module
    # (and its requests dependency) — keeping CLI startup lean. The authoritative
    # guard set lives in crm.core.update.is_check_enabled.
    _maybe_update_check(json_mode)

    if ctx.invoked_subcommand is None:
        if _suppress_bare_repl(json_mode):
            msg = "no subcommand given; run crm --help to list commands"
            if json_mode:
                _emit_usage_envelope(msg)
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
