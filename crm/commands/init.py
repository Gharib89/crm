"""`crm init` — env template generator + interactive profile wizard."""
# pyright: basic
from __future__ import annotations

from pathlib import Path

import click

from crm.cli import CLIContext, pass_ctx
from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile

_ENV_TEMPLATE = """\
# Dynamics 365 connection settings — copy to .env and fill in values.
CRM_URL=https://your-crm.corp/
CRM_USERNAME=DOMAIN\\user
CRM_PASSWORD=
CRM_DOMAIN=CORP
CRM_AUTH_SCHEME=ntlm   # ntlm | kerberos | negotiate

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
        raise SystemExit(1)
    dest.write_text(_ENV_TEMPLATE, encoding="utf-8")
    ctx.emit(True, data={"written": str(dest)})


def _run_wizard(ctx: CLIContext) -> None:
    url = click.prompt("Server URL (e.g. https://crm.corp/org)")
    username = click.prompt("Username")
    password = click.prompt("Password", hide_input=True, default="", show_default=False)
    domain = click.prompt("AD domain (blank for UPN)", default="", show_default=False)
    auth_scheme = click.prompt(
        "Auth scheme",
        type=click.Choice(["ntlm", "kerberos", "negotiate"]),
        default="ntlm",
    )
    profile_name = click.prompt("Profile name", default="default")

    existing = profile_name in session_mod.list_profiles()
    if existing and not click.confirm(
        f"Profile {profile_name!r} already exists. Overwrite?", default=False,
    ):
        ctx.emit(False, error="aborted by user")
        raise SystemExit(1)

    profile = ConnectionProfile(
        name=profile_name,
        url=url,
        domain=domain,
        username=username,
        auth_scheme=auth_scheme,
    )
    session_mod.save_profile(profile)
    ctx.emit(True, data={
        "profile": profile_name,
        "saved": True,
        "password_set": bool(password),
    })
