"""crm — Click-based CLI + REPL for Dynamics 365 CE on-prem 9.x.

Entry point: `crm` (installed) or `python -m crm`.

Running with no subcommand drops into the REPL. Each command supports `--json` for
machine-readable output. `--dry-run` previews the HTTP request without issuing it.
"""
# pyright: basic

from __future__ import annotations

import json
import os
from typing import Any

import click

from crm import __version__
from crm.core import (
    connection as conn_mod,
)
from crm.core.logging_setup import setup_logging
from crm.utils.d365_backend import D365Backend
from crm.utils.repl_skin import ReplSkin
from crm.commands._helpers import _sanitize, _short_repr


class CLIContext:
    """Per-invocation state shared across subcommands."""

    def __init__(self):
        self.json_mode: bool = False
        self.dry_run: bool = False
        self.profile_name: str | None = None
        self.password: str | None = None
        self.auth_scheme: str | None = None
        self.session_name: str = "default"
        self._backend: D365Backend | None = None
        self._backend_key: tuple[str | None, str | None, bool, str | None] | None = None
        self.skin: ReplSkin = ReplSkin("d365", version=__version__)

    def emit(self, ok: bool, data: Any = None, *, error: str | None = None,
             meta: dict | None = None, table: dict | None = None) -> None:
        """Print either a JSON envelope or a human-friendly representation."""
        if self.json_mode:
            envelope: dict[str, Any] = {"ok": ok}
            if data is not None:
                envelope["data"] = _sanitize(data)
            if error:
                envelope["error"] = error
            if meta:
                envelope["meta"] = meta
            click.echo(json.dumps(envelope, indent=2, default=str))
            return

        if not ok:
            self.skin.error(error or "Operation failed.")
            return

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

    def backend(self) -> D365Backend:
        key = (self.profile_name, self.password, self.dry_run, self.auth_scheme)
        if self._backend is None or self._backend_key != key:
            resolved = conn_mod.resolve_credentials(
                profile_name=self.profile_name,
                password_override=self.password,
            )
            if self.auth_scheme is not None:
                resolved.profile.auth_scheme = self.auth_scheme
            self._backend = D365Backend(
                resolved.profile, resolved.password, dry_run=self.dry_run
            )
            self._backend_key = key
        return self._backend

    def invalidate_backend(self) -> None:
        """Drop the cached D365Backend so the next backend() call rebuilds it.

        Called when the profile changes (`connection connect`/`disconnect`) so
        the REPL stops reusing a backend wired up to a stale profile.
        Also triggers automatically if `profile_name`/`password`/`dry_run` change
        between calls (e.g., root opts re-supplied per REPL line).
        """
        self._backend = None
        self._backend_key = None


pass_ctx = click.make_pass_decorator(CLIContext, ensure=True)


# ── Root group ──────────────────────────────────────────────────────────


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--json", "json_mode", is_flag=True, help="Emit machine-readable JSON output.")
@click.option("--dry-run", is_flag=True, help="Preview HTTP request without issuing it.")
@click.option("--profile", "profile_name", help="Connection profile name (from ~/.crm/profiles).")
@click.option("--password", help="Override password (otherwise read from D365_PASSWORD).")
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
              type=click.Choice(["ntlm", "kerberos", "negotiate"]),
              default=None,
              help="HTTP auth scheme (env: CRM_AUTH_SCHEME). Default: ntlm.")
@click.option("--session", "session_name", default="default", help="Session name.")
@click.version_option(__version__, prog_name="crm")
@click.pass_context
def cli(ctx: click.Context, json_mode: bool, dry_run: bool,
        profile_name: str | None, password: str | None,
        log_level: str | None, verbose: bool, log_format: str | None,
        auth_scheme: str | None, session_name: str):
    """Stateful CLI for Dynamics 365 CE on-prem 9.x (Web API)."""
    effective_level = log_level or os.environ.get("CRM_LOG_LEVEL") or "warning"
    if verbose:
        effective_level = "debug"
    effective_fmt = log_format or os.environ.get("CRM_LOG_FORMAT") or "text"
    setup_logging(level=effective_level, fmt=effective_fmt)  # type: ignore[arg-type]

    cli_ctx = ctx.ensure_object(CLIContext)
    cli_ctx.json_mode = json_mode
    cli_ctx.dry_run = dry_run
    # Sticky options: in the REPL the same CLIContext is reused across lines, so only
    # overwrite when the user actually supplied the flag — otherwise prior values
    # (e.g., set by `connection connect`) would be wiped on the next bare command.
    if profile_name is not None:
        cli_ctx.profile_name = profile_name
    if password is not None:
        cli_ctx.password = password
    cli_ctx.auth_scheme = auth_scheme or os.environ.get("CRM_AUTH_SCHEME")
    cli_ctx.session_name = session_name

    if ctx.invoked_subcommand is None:
        from crm.commands.repl import repl
        ctx.invoke(repl)


# ── Wire up command modules ─────────────────────────────────────────────

from crm.commands.connection import connection_group  # noqa: E402
from crm.commands.entity import entity_group  # noqa: E402
from crm.commands.query import query_group  # noqa: E402
from crm.commands.metadata import metadata_group  # noqa: E402
from crm.commands.solution import solution_group  # noqa: E402
from crm.commands.data import data_group  # noqa: E402
from crm.commands.action import action_group  # noqa: E402
from crm.commands.async_ops import async_group  # noqa: E402
from crm.commands.workflow import workflow_group  # noqa: E402
from crm.commands.skill import skill_group  # noqa: E402
from crm.commands.session import session_group  # noqa: E402
from crm.commands.batch import batch_cmd, service_document_cmd  # noqa: E402
from crm.commands.repl import repl  # noqa: E402
from crm.commands.init import init_cmd  # noqa: E402

cli.add_command(connection_group)
cli.add_command(entity_group)
cli.add_command(query_group)
cli.add_command(metadata_group)
cli.add_command(solution_group)
cli.add_command(data_group)
cli.add_command(action_group)
cli.add_command(async_group)
cli.add_command(workflow_group)
cli.add_command(skill_group)
cli.add_command(session_group)
cli.add_command(batch_cmd)
cli.add_command(service_document_cmd)
cli.add_command(repl)
cli.add_command(init_cmd)

if __name__ == "__main__":
    cli()
