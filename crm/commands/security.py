"""Security role commands."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import security as security_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _confirm_destructive,
    _admin_header_options,
    _admin_kwargs,
    _handle_d365_error,
)


@click.group("security")
def security_group():
    """List and assign security roles."""


@security_group.command("list-roles")
@click.option("--business-unit", "business_unit", metavar="GUID", default=None,
              help="Filter to roles belonging to this business unit GUID.")
@pass_ctx
def list_roles(ctx: CLIContext, business_unit):
    """List security roles (optionally scoped to a business unit)."""
    try:
        items = security_mod.list_roles(ctx.backend(), business_unit=business_unit)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["name", "roleid", "businessunitid"]
    rows = [[it.get("name", ""), it.get("roleid", ""),
             it.get("_businessunitid_value", "")] for it in items]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"count": len(items)})


@security_group.command("list-user-roles")
@click.argument("user_id")
@pass_ctx
def list_user_roles(ctx: CLIContext, user_id):
    """List security roles assigned to a system user (USER_ID is a GUID)."""
    try:
        items = security_mod.list_user_roles(ctx.backend(), user_id)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["name", "roleid"]
    rows = [[it.get("name", ""), it.get("roleid", "")] for it in items]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"count": len(items)})


@security_group.command("list-team-roles")
@click.argument("team_id")
@pass_ctx
def list_team_roles(ctx: CLIContext, team_id):
    """List security roles assigned to a team (TEAM_ID is a GUID)."""
    try:
        items = security_mod.list_team_roles(ctx.backend(), team_id)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["name", "roleid"]
    rows = [[it.get("name", ""), it.get("roleid", "")] for it in items]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"count": len(items)})


@security_group.command("assign-role")
@click.argument("role_id")
@click.option("--to-user", "to_user", metavar="GUID", default=None,
              help="Assign the role to this system user GUID.")
@click.option("--to-team", "to_team", metavar="GUID", default=None,
              help="Assign the role to this team GUID.")
@click.option("--yes", is_flag=True, help="Skip interactive confirmation.")
@_admin_header_options
@pass_ctx
def assign_role(ctx: CLIContext, role_id, to_user, to_team, yes,
                as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Assign a security role to a user or team.

    Exactly one of --to-user or --to-team must be provided.
    Role assignment is cumulative and not cleanly reversible.
    """
    if (to_user is None) == (to_team is None):
        raise click.UsageError("provide exactly one of --to-user / --to-team")
    principal = "user" if to_user else "team"
    principal_id = to_user or to_team
    message = (
        f"Grant security role {role_id} to {principal} {principal_id}? "
        "Role assignment is cumulative and not cleanly reversible."
    )
    if not _confirm_destructive("role", role_id, yes, message=message):
        ctx.emit(False, error="aborted by user")
        return
    try:
        if to_user:
            result = security_mod.assign_role_to_user(
                ctx.backend(), to_user, role_id,
                **_admin_kwargs(as_user, as_user_object_id,
                                suppress_dup_detection, bypass_plugins),
            )
        else:
            result = security_mod.assign_role_to_team(
                ctx.backend(), to_team, role_id,
                **_admin_kwargs(as_user, as_user_object_id,
                                suppress_dup_detection, bypass_plugins),
            )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result)
