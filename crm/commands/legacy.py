"""`crm legacy`: read-only commands for a CRM 2011 (crm2011) profile (ADR 0031)."""

# pyright: basic
from __future__ import annotations

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import d365_errors


@click.group("legacy")
def legacy_group():
    """Read a legacy CRM 2011 org over its read-only SOAP endpoint.

    Runs only on a crm2011 profile, which `crm profile add` saves when the org
    has no Web API. Tested on CRM 2011 UR15 (5.0.9690.3557), Linux.
    """


@legacy_group.command("whoami")
@pass_ctx
def legacy_whoami(ctx: CLIContext):
    """Issue WhoAmI: the caller's user, business unit and organization ids."""
    with d365_errors(ctx):
        client = ctx.legacy_client()
        results = client.execute("WhoAmI")
    data = {
        "user_id": results.get("UserId"),
        "business_unit_id": results.get("BusinessUnitId"),
        "organization_id": results.get("OrganizationId"),
    }
    meta = {"profile": client.profile.name, "url": client.endpoint} if ctx.json_mode else None
    ctx.emit(True, data=data, meta=meta)


@legacy_group.command("version")
@pass_ctx
def legacy_version(ctx: CLIContext):
    """Issue RetrieveVersion: the org's build string (5.x on CRM 2011)."""
    with d365_errors(ctx):
        client = ctx.legacy_client()
        results = client.execute("RetrieveVersion")
    data = {"version": results.get("Version")}
    meta = {"profile": client.profile.name, "url": client.endpoint} if ctx.json_mode else None
    ctx.emit(True, data=data, meta=meta)
