"""Connection management commands."""
from __future__ import annotations
import os
import click
from crm.core import connection as conn_mod
from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile, D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import _handle_d365_error


@click.group("connection")
def connection_group():
    """Manage server connection profiles and authentication."""


@connection_group.command("connect")
@click.option("--url", required=True, help="Server URL, e.g. https://crm.contoso.local/contoso")
@click.option("--username", required=True)
@click.option("--domain", default="", help="AD domain (optional for on-prem with UPN).")
@click.option("--password", "password_opt", help="Password (else read from D365_PASSWORD).")
@click.option("--profile-name", default="default", help="Save under this profile name.")
@click.option("--api-version", default="v9.2")
@click.option("--no-verify-ssl", is_flag=True, help="Skip SSL certificate verification.")
@pass_ctx
def connection_connect(ctx: CLIContext, url, username, domain, password_opt,
                       profile_name, api_version, no_verify_ssl):
    """Save a connection profile and test the credentials with WhoAmI."""
    profile = ConnectionProfile(
        name=profile_name,
        url=url,
        domain=domain,
        username=username,
        api_version=api_version,
        verify_ssl=not no_verify_ssl,
        auth_scheme=ctx.auth_scheme or "ntlm",
    )
    session_mod.save_profile(profile)
    ctx.profile_name = profile_name
    ctx.password = password_opt or os.environ.get(conn_mod.ENV_PASSWORD, "")
    ctx.invalidate_backend()
    try:
        info = conn_mod.test_connection(ctx.backend())
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return

    state = session_mod.load_session(ctx.session_name)
    state["active_profile"] = profile_name
    session_mod.save_session(state, ctx.session_name)
    ctx.invalidate_backend()
    ctx.emit(True, data=info, meta={"profile": profile_name})


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
    """Issue WhoAmI() against the server."""
    try:
        info = conn_mod.whoami(ctx.backend())
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@connection_group.command("test")
@pass_ctx
def connection_test(ctx: CLIContext):
    """Reachability check: WhoAmI + report API base."""
    try:
        info = conn_mod.test_connection(ctx.backend())
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@connection_group.command("profiles")
@pass_ctx
def connection_profiles(ctx: CLIContext):
    """List saved profiles."""
    names = session_mod.list_profiles()
    if ctx.json_mode:
        ctx.emit(True, data=names)
        return
    ctx.skin.section("Profiles")
    if not names:
        ctx.skin.hint("(none)")
    for n in names:
        ctx.skin.info(n)


@connection_group.command("disconnect")
@pass_ctx
def connection_disconnect(ctx: CLIContext):
    """Clear the active profile from the session."""
    state = session_mod.load_session(ctx.session_name)
    state["active_profile"] = None
    session_mod.save_session(state, ctx.session_name)
    # Also clear in-memory state — sticky-options means these would otherwise
    # persist across REPL lines and defeat the disconnect.
    ctx.profile_name = None
    ctx.password = None
    ctx.invalidate_backend()
    ctx.emit(True, data={"disconnected": True})
