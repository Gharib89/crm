"""Connection management commands."""

# pyright: basic
from __future__ import annotations

import click

from crm.cli import FAILURE_EXIT_CODE, CLIContext, pass_ctx
from crm.commands._helpers import d365_errors
from crm.core import connection as conn_mod
from crm.core import session as session_mod


@click.group("connection")
def connection_group():
    """Diagnose the active connection (WhoAmI, reachability, doctor).

    Profiles and stored secrets are managed under `crm profile`.
    """


@connection_group.command("status")
@pass_ctx
def connection_status(ctx: CLIContext):
    """Show the active session + profile (no network call)."""
    state = session_mod.load_session(ctx.session_name)
    profile_name = ctx.profile_name or state.get("active_profile")
    data = {
        "session": ctx.session_name,
        "active_profile": profile_name,
        "current_entity_set": state.get("current_entity_set"),
    }
    if profile_name:
        try:
            p = session_mod.load_profile(profile_name)
            data["profile"] = p.to_dict()
        except FileNotFoundError:
            data["profile_error"] = f"profile {profile_name!r} not found"
    ctx.emit(True, data=data)


@connection_group.command("whoami")
@pass_ctx
def connection_whoami(ctx: CLIContext):
    """Issue WhoAmI() against the server, with the serving profile/org/URL."""
    with d365_errors(ctx):
        info = conn_mod.whoami_identity(ctx.backend())
    ctx.emit(True, data=info)


@connection_group.command("test")
@pass_ctx
def connection_test(ctx: CLIContext):
    """Reachability check: WhoAmI + report API base."""
    with d365_errors(ctx):
        info = conn_mod.test_connection(ctx.backend(), negotiate=False)
    ctx.emit(True, data=info)


@click.command("doctor")
@pass_ctx
def doctor_command(ctx: CLIContext):
    """Diagnose the connection: DNS/TCP, TLS, api_version, auth, rate-limit.

    \f
    A live diagnostic: it issues raw GETs and never negotiates or mutates the
    profile. It is registered both under the `connection` group and as the
    top-level `crm doctor` alias (the same command object).

    Human mode renders the checklist explicitly because emit's human not-ok path
    prints only the error line, dropping the per-check data we want to show. The
    whole checklist (check lines and hints) goes to a single stream (stdout, like
    skin.success and skin.hint) so captured or piped output stays ordered;
    skin.error writes to stderr and would orphan each failed line from its hint. A
    failed check line here is data, not an error message, so it renders on stdout
    in the same visual format as skin.success. The exit code is what signals
    failure programmatically.
    """
    with d365_errors(ctx):
        backend = ctx.backend()
    result = conn_mod.connection_doctor(backend)
    if ctx.json_mode:
        ctx.emit(result["ok"], data={"checks": result["checks"]})
        return
    ctx.skin.section("Connection doctor")
    for c in result["checks"]:
        line = f"{c['check']}: {c['detail']}"
        if c["ok"]:
            ctx.skin.success(line)
        else:
            ctx.skin.failure(line)
        if isinstance(c["hint"], str) and c["hint"]:
            ctx.skin.hint(f"    {c['hint']}")
    if not result["ok"]:
        raise click.exceptions.Exit(FAILURE_EXIT_CODE)


connection_group.add_command(doctor_command)
