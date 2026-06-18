"""Security role commands."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import security as security_mod
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _destructive_option,
    d365_errors,
    _confirm_destructive,
    _admin_header_options,
    _admin_kwargs,
    _journal,
)


@click.group("security")
def security_group():
    """List and assign security roles, and share records (POA)."""


def _split_principal(value: str) -> tuple[str, str]:
    """Parse a ``<type>:<guid>`` principal argument into (type, id).

    The type is validated against the supported principals in the core layer;
    here we only enforce the ``type:id`` shape so a malformed value fails as a
    clean usage error (exit 2) before any backend call.
    """
    ptype, sep, pid = value.partition(":")
    if not sep or not ptype.strip() or not pid.strip():
        raise click.UsageError("principal must be of the form <user|team|org>:<guid>")
    return ptype.strip(), pid.strip()


@security_group.command("list-roles")
@click.option("--business-unit", "business_unit", metavar="GUID", default=None,
              help="Filter to roles belonging to this business unit GUID.")
@pass_ctx
def list_roles(ctx: CLIContext, business_unit):
    """List security roles (optionally scoped to a business unit)."""
    with d365_errors(ctx):
        items = security_mod.list_roles(ctx.backend(), business_unit=business_unit)
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
    with d365_errors(ctx):
        items = security_mod.list_user_roles(ctx.backend(), user_id)
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
    with d365_errors(ctx):
        items = security_mod.list_team_roles(ctx.backend(), team_id)
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["name", "roleid"]
    rows = [[it.get("name", ""), it.get("roleid", "")] for it in items]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"count": len(items)})


@security_group.command("user-privileges")
@click.argument("user_id")
@pass_ctx
def user_privileges(ctx: CLIContext, user_id):
    """Show a system user's effective privileges (USER_ID is a GUID).

    Resolves the user's full privilege set — from their own roles plus
    team-inherited — via RetrieveUserPrivileges. Team-inherited privileges
    are reported at Basic depth only.
    """
    with d365_errors(ctx):
        items = security_mod.list_user_privileges(ctx.backend(), user_id)
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["name", "depth", "privilegeid"]
    rows = [[it.get("PrivilegeName", ""), it.get("Depth", ""),
             it.get("PrivilegeId", "")] for it in items]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"count": len(items)})


@security_group.command("assign-role")
@click.argument("role_id")
@click.option("--to-user", "to_user", metavar="GUID", default=None,
              help="Assign the role to this system user GUID.")
@click.option("--to-team", "to_team", metavar="GUID", default=None,
              help="Assign the role to this team GUID.")
@_destructive_option
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
    _confirm_destructive(ctx, "role", role_id, yes, message=message)
    with d365_errors(ctx):
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
    ctx.emit(True, data=result)
    _journal(ctx, role_id, result)


@security_group.command("grant")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--to", "to", metavar="TYPE:GUID", required=True,
              help="Principal to share with, as <user|team|org>:<guid>.")
@click.option("--rights", required=True, metavar="RIGHTS",
              help="Comma-separated access rights: "
                   "Read,Write,Append,AppendTo,Create,Delete,Share,Assign.")
@_destructive_option
@pass_ctx
def grant(ctx: CLIContext, entity_set, record_id, to, rights, yes):
    """Share a record with a principal (POA GrantAccess).

    ENTITY_SET is the entity-set name (e.g. accounts); RECORD_ID is the record
    GUID. Sharing is reversible with `security revoke`.
    """
    principal_type, principal_id = _split_principal(to)
    message = (
        f"Share {entity_set}({record_id}) with {principal_type} {principal_id} "
        f"at rights [{rights}]?"
    )
    _confirm_destructive(ctx, "record", record_id, yes, message=message)
    with d365_errors(ctx):
        result = security_mod.grant_access(
            ctx.backend(), entity_set, record_id,
            principal_type=principal_type, principal_id=principal_id, rights=rights,
        )
    ctx.emit(True, data=result)
    _journal(ctx, record_id, result)


@security_group.command("revoke")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--from", "from_", metavar="TYPE:GUID", required=True,
              help="Principal to unshare, as <user|team|org>:<guid>.")
@_destructive_option
@pass_ctx
def revoke(ctx: CLIContext, entity_set, record_id, from_, yes):
    """Remove a principal's shared access to a record (POA RevokeAccess).

    Removes all of the principal's shared rights on the record (there is no
    per-right revoke). ENTITY_SET is the entity-set name; RECORD_ID is the GUID.
    """
    principal_type, principal_id = _split_principal(from_)
    message = (
        f"Revoke {principal_type} {principal_id}'s shared access to "
        f"{entity_set}({record_id})?"
    )
    _confirm_destructive(ctx, "record", record_id, yes, message=message)
    with d365_errors(ctx):
        result = security_mod.revoke_access(
            ctx.backend(), entity_set, record_id,
            principal_type=principal_type, principal_id=principal_id,
        )
    ctx.emit(True, data=result)
    _journal(ctx, record_id, result)


@security_group.command("list-access")
@click.argument("entity_set")
@click.argument("record_id")
@pass_ctx
def list_access(ctx: CLIContext, entity_set, record_id):
    """List the principals a record is shared with and their rights.

    ENTITY_SET is the entity-set name (e.g. accounts); RECORD_ID is the GUID.
    Reports each principal's type, id, and effective shared access mask
    (RetrieveSharedPrincipalsAndAccess).
    """
    with d365_errors(ctx):
        items = security_mod.list_access(ctx.backend(), entity_set, record_id)
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["principalType", "principalId", "accessMask"]
    rows = [[it.get("principalType", ""), it.get("principalId", ""),
             it.get("accessMask", "")] for it in items]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"count": len(items)})
