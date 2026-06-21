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
@click.option("--name-contains", "name_contains", metavar="TEXT", default=None,
              help="Filter to roles whose name contains this text (server-side).")
@pass_ctx
def list_roles(ctx: CLIContext, business_unit, name_contains):
    """List security roles (optionally filtered by business unit or name)."""
    with d365_errors(ctx):
        items = security_mod.list_roles(
            ctx.backend(), business_unit=business_unit, name_contains=name_contains,
        )
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["name", "roleid", "businessunitid"]
    rows = [[it.get("name", ""), it.get("roleid", ""),
             it.get("_businessunitid_value", "")] for it in items]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"count": len(items)})



def _csv(value: str | None) -> list[str]:
    """Split a comma-separated option value into a clean list (empty → [])."""
    return [v.strip() for v in value.split(",") if v.strip()] if value else []


@security_group.command("create-role")
@click.argument("name")
@click.option("--business-unit", "business_unit", metavar="GUID", default=None,
              help="Business unit GUID for the role (defaults to the caller's).")
@click.option("--if-exists", "if_exists", type=click.Choice(["error", "skip"]),
              default="error", show_default=True,
              help="On a same-name role in the same business unit: error, or "
                   "skip (reuse the existing role).")
@_destructive_option
@pass_ctx
def create_role(ctx: CLIContext, name, business_unit, if_exists, yes):
    """Create a security role (NAME is the role's display name).

    The role starts with no privileges; grant them with
    `security set-role-privileges`. Use --dry-run to preview without writing.
    """
    if not ctx.dry_run:
        _confirm_destructive(ctx, "role", name, yes,
                             message=f"Create security role {name!r}?")
    with d365_errors(ctx):
        result = security_mod.create_role(
            ctx.backend(), name, business_unit=business_unit, if_exists=if_exists,
        )
    ctx.emit(True, data=result)
    _journal(ctx, name, result)


@security_group.command("set-role-privileges")
@click.argument("role")
@click.option("--access", default=None, metavar="LIST",
              help="Comma-separated access types: "
                   "read,write,create,delete,append,appendto,assign,share. "
                   "Requires --entities or --all-entities.")
@click.option("--entities", default=None, metavar="LIST",
              help="Comma-separated entity logical names to scope --access to.")
@click.option("--all-entities", "all_entities", is_flag=True,
              help="Apply --access across every entity (org-wide).")
@click.option("--privilege", "privilege", default=None, metavar="LIST",
              help="Explicit privilege names (comma-separated), e.g. "
                   "prvCreateEntity. The escape hatch for non-entity privileges.")
@click.option("--depth", required=True, metavar="LEVEL",
              help="Privilege depth: basic|local|deep|global (aliases "
                   "user|businessunit|parentchild|organization). Clamped per "
                   "privilege to the levels it supports.")
@click.option("--add", "mode", flag_value="add", default=True,
              help="Merge privileges into the role (default, non-destructive).")
@click.option("--replace", "mode", flag_value="replace",
              help="Replace the role's privileges with exactly the resolved set.")
@_destructive_option
@pass_ctx
def set_role_privileges(ctx: CLIContext, role, access, entities, all_entities,
                        privilege, depth, mode, yes):
    """Add or replace a security role's privileges (ROLE is a role id or name).

    Resolve privileges from access×entity selectors and/or explicit privilege
    names, clamp the requested --depth per privilege, then grant them. --replace
    wipes any privilege not in the resolved set. Use --dry-run to preview.
    """
    replace = mode == "replace"
    if not ctx.dry_run:
        verb = "Replace" if replace else "Add"
        scope = "ALL entities" if all_entities else (entities or "named privileges")
        message = (
            f"{verb} privileges on role {role} (access=[{access or '-'}], "
            f"scope={scope}) at depth {depth}? "
            + ("--replace wipes privileges not in the resolved set." if replace else "")
        )
        _confirm_destructive(ctx, "role", role, yes, message=message)
    with d365_errors(ctx):
        result = security_mod.set_role_privileges(
            ctx.backend(), role,
            access=_csv(access), entities=_csv(entities), all_entities=all_entities,
            privilege_names=_csv(privilege), depth=depth, replace=replace,
        )
    warnings = result.pop("warnings", None) or None
    ctx.emit(True, data=result, warnings=warnings)
    _journal(ctx, role, result)


@security_group.command("list-user-roles")
@click.argument("user_id")
@pass_ctx
def list_user_roles(ctx: CLIContext, user_id):
    """List security roles directly assigned to a system user (USER_ID is a GUID).

    Shows only roles assigned directly to the user, not roles inherited
    through team membership.  Use ``security user-privileges`` to see the
    full effective privilege set (direct + team-inherited).
    """
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
