"""Connection management commands."""
# pyright: basic
from __future__ import annotations
import os
import click
from crm.core import connection as conn_mod
from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile, D365Error
from crm.cli import CLIContext, FAILURE_EXIT_CODE, pass_ctx
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
@click.option("--api-version", default=None,
              help="Web API version. Omit to auto-negotiate (tries v9.2, "
                   "downgrades to v9.1 when the server is on-prem and 501s).")
@click.option("--no-verify-ssl", is_flag=True, help="Skip SSL certificate verification.")
@click.option("--default-solution", default=None,
              help="Default solution uniquename for mutating metadata commands.")
@click.option("--publisher-prefix", default=None,
              help="Default schema-name prefix for create commands, e.g. 'new'.")
@pass_ctx
def connection_connect(ctx: CLIContext, url, username, domain, password_opt,
                       profile_name, api_version, no_verify_ssl,
                       default_solution, publisher_prefix):
    """Save a connection profile and test the credentials with WhoAmI."""
    # An omitted --api-version is negotiated against the server (v9.2 → v9.1 on
    # on-prem); an explicit value is pinned and never auto-downgraded.
    negotiate = api_version is None
    profile = ConnectionProfile(
        name=profile_name,
        url=url,
        domain=domain,
        username=username,
        api_version=api_version or conn_mod.DEFAULT_API_VERSION,
        verify_ssl=not no_verify_ssl,
        auth_scheme=ctx.auth_scheme or "ntlm",
        default_solution=default_solution,
        publisher_prefix=publisher_prefix,
    )
    session_mod.save_profile(profile)
    ctx.profile_name = profile_name
    ctx.password = password_opt or os.environ.get(conn_mod.ENV_PASSWORD, "")
    ctx.invalidate_backend()
    try:
        info = conn_mod.test_connection(ctx.backend(), negotiate=negotiate)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return

    # Persist the negotiated version so every later command uses the working one.
    # (The trailing invalidate_backend() below picks up the re-saved profile.)
    if info["api_version"] != profile.api_version:
        profile.api_version = info["api_version"]
        session_mod.save_profile(profile)

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
    # An env-derived profile with no explicit D365_API_VERSION negotiates the
    # version for this run; a loaded profile is respected as saved. Load .env
    # first so a version pinned only in the .env file is seen (it would
    # otherwise be read later, inside ctx.backend(), and missed here).
    conn_mod.load_dotenv()
    negotiate = ctx.profile_name is None and not conn_mod.env_api_version()
    try:
        info = conn_mod.test_connection(ctx.backend(), negotiate=negotiate)
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
        # `data` stays the bare name list for back-compat with `--json` consumers
        # that iterate it; the per-profile default_solution / publisher_prefix (AC)
        # is surfaced under `meta` instead of changing the shape of `data`.
        profiles = []
        for n in names:
            try:
                p = session_mod.load_profile(n)
                profiles.append({
                    "name": n,
                    "default_solution": p.default_solution,
                    "publisher_prefix": p.publisher_prefix,
                })
            except FileNotFoundError:
                profiles.append({"name": n})
        ctx.emit(True, data=names, meta={"profiles": profiles})
        return
    ctx.skin.section("Profiles")
    if not names:
        ctx.skin.hint("(none)")
    for n in names:
        try:
            p = session_mod.load_profile(n)
            ctx.skin.status(
                n,
                f"solution={p.default_solution} prefix={p.publisher_prefix}",
            )
        except FileNotFoundError:
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


@click.command("doctor")
@pass_ctx
def doctor_command(ctx: CLIContext):
    """Diagnose the connection: DNS/TCP, TLS, api_version, auth, rate-limit."""
    # Live diagnostic — issues raw GETs and never negotiates or mutates the
    # profile. Registered both under the `connection` group and as the
    # top-level `crm doctor` alias (the same command object).
    try:
        backend = ctx.backend()
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    result = conn_mod.connection_doctor(backend)
    if ctx.json_mode:
        ctx.emit(result["ok"], data={"checks": result["checks"]})
        return
    # Human mode: render the checklist explicitly — emit's human not-ok path
    # prints only the error line, dropping the per-check data we want to show.
    ctx.skin.section("Connection doctor")
    for c in result["checks"]:
        line = f"{c['check']}: {c['detail']}"
        if c["ok"]:
            ctx.skin.success(line)
        else:
            ctx.skin.error(line)
        if isinstance(c["hint"], str) and c["hint"]:
            ctx.skin.hint(f"    {c['hint']}")
    if not result["ok"]:
        raise click.exceptions.Exit(FAILURE_EXIT_CODE)


connection_group.add_command(doctor_command)
