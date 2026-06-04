"""`crm init` — env template generator + interactive profile wizard."""
# pyright: basic
from __future__ import annotations

from pathlib import Path

import click

from crm.cli import CLIContext, pass_ctx
from crm.core import connection as conn_mod
from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error

_ENV_TEMPLATE = """\
# Dynamics 365 connection settings — copy to .env and fill in values.
# Pick ONE auth block.

# ── On-prem (NTLM, default) ──
CRM_URL=https://your-crm.corp/
CRM_USERNAME=DOMAIN\\user
CRM_PASSWORD=
CRM_DOMAIN=CORP
CRM_AUTH=ntlm          # env-profile auth selector: ntlm | oauth
# kerberos / negotiate are CLI-only — pass --auth-scheme or set CRM_AUTH_SCHEME
# (a different knob from CRM_AUTH, which the env loader reads).

# ── Online / Dataverse cloud (OAuth client-credentials) ──
# CRM_URL=https://your-org.crm.dynamics.com
# CRM_AUTH=oauth
# CRM_TENANT_ID=
# CRM_CLIENT_ID=
# CRM_CLIENT_SECRET=

# Logging
CRM_LOG_LEVEL=warning  # debug | info | warning | error
CRM_LOG_FORMAT=text    # text | json-line
"""


@click.command("init")
@click.option("--template", is_flag=True,
              help="Write .env.example to the current directory and exit.")
@pass_ctx
def init_cmd(ctx: CLIContext, template: bool):
    """Bootstrap a crm workspace.

    With --template: writes .env.example to the current directory.
    Without args: interactive wizard to create a connection profile.
    """
    if template:
        _write_template(ctx)
        return
    _run_wizard(ctx)


def _write_template(ctx: CLIContext) -> None:
    dest = Path.cwd() / ".env.example"
    if dest.exists():
        ctx.emit(False, error=f"{dest} already exists; refusing to overwrite.")
    dest.write_text(_ENV_TEMPLATE, encoding="utf-8")
    ctx.emit(True, data={"written": str(dest)})


def _run_wizard(ctx: CLIContext) -> None:
    url = click.prompt("Server URL (e.g. https://crm.corp/org or https://org.crm.dynamics.com)")
    auth_scheme = click.prompt(
        "Auth scheme",
        type=click.Choice(["ntlm", "kerberos", "negotiate", "oauth"]),
        default="ntlm",
    )

    if auth_scheme == "oauth":
        tenant_id = click.prompt("Azure AD tenant id")
        client_id = click.prompt("Application (client) id")
        secret_set = bool(click.prompt(
            "Client secret (not saved to the profile)",
            hide_input=True, default="", show_default=False,
        ))
        profile_name = _prompt_profile_name(ctx)
        profile = ConnectionProfile(
            name=profile_name,
            url=url,
            domain="",
            username="",
            auth_scheme="oauth",
            tenant_id=tenant_id,
            client_id=client_id,
        )
        session_mod.save_profile(profile)
        ctx.emit(True, data={
            "profile": profile_name,
            "saved": True,
            "secret_set": secret_set,
        })
        return

    username = click.prompt("Username")
    password = click.prompt("Password", hide_input=True, default="", show_default=False)
    domain = click.prompt("AD domain (blank for UPN)", default="", show_default=False)
    profile_name = _prompt_profile_name(ctx)
    profile = ConnectionProfile(
        name=profile_name,
        url=url,
        domain=domain,
        username=username,
        auth_scheme=auth_scheme,
    )
    session_mod.save_profile(profile)
    data = {
        "profile": profile_name,
        "saved": True,
        "password_set": bool(password),
    }
    # On-prem v9.x 501s on the v9.2 default — probe with the just-entered creds
    # and persist the negotiated version so the profile is usable as-is (#51).
    data.update(_negotiate_version(profile, password))
    ctx.emit(True, data=data)


def _negotiate_version(profile: ConnectionProfile, password: str) -> dict:
    """Best-effort: probe the server and persist the negotiated api_version.

    Returns a small status dict to fold into the wizard output. A probe failure
    (server unreachable, wrong creds) is non-fatal — the profile keeps the
    optimistic v9.2 default and the user can re-run after fixing credentials.
    """
    try:
        info = conn_mod.test_connection(D365Backend(profile, password), negotiate=True)
    except D365Error as exc:
        return {"verified": False, "verify_error": str(exc)}
    # test_connection downgrades profile.api_version in place on 501; the backend
    # shares this very object, so persist the (possibly changed) value as-is.
    profile.api_version = info["api_version"]
    session_mod.save_profile(profile)
    return {"verified": True, "api_version": profile.api_version}


def _prompt_profile_name(ctx: CLIContext) -> str:
    profile_name = click.prompt("Profile name", default="default")
    if profile_name in session_mod.list_profiles() and not click.confirm(
        f"Profile {profile_name!r} already exists. Overwrite?", default=False,
    ):
        ctx.emit(False, error="aborted by user")
    return profile_name
