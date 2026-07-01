"""Field-level (column) security commands — `crm fieldsec`."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import fieldsec as fieldsec_mod
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    d365_errors, _journal, _solution_option, _resolve_solution, _emit_with_warning,
)


@click.group("fieldsec")
def fieldsec_group():
    """Manage field security profiles and column-level permissions."""


@fieldsec_group.command("list")
@pass_ctx
def fieldsec_list(ctx: CLIContext) -> None:
    """List all field security profiles."""
    with d365_errors(ctx):
        profiles = fieldsec_mod.list_profiles(ctx.backend())
    rows = [
        [p.get("name", ""), p.get("fieldsecurityprofileid") or "",
         p.get("description") or ""]
        for p in profiles
    ]
    ctx.emit(True, data=profiles, table={
        "headers": ["name", "fieldsecurityprofileid", "description"],
        "rows": rows,
    })


@fieldsec_group.command("get")
@click.argument("profile")
@pass_ctx
def fieldsec_get(ctx: CLIContext, profile: str) -> None:
    """Show one profile (by name or id) and the field permissions it grants."""
    with d365_errors(ctx):
        info = fieldsec_mod.get_profile(ctx.backend(), profile)
    ctx.emit(True, data=info)


@fieldsec_group.command("create-profile")
@click.argument("name")
@click.option("--description", default=None, help="Profile description.")
@_solution_option
@pass_ctx
def fieldsec_create_profile(ctx: CLIContext, name, description,
                            solution) -> None:
    """Create a field security profile named NAME."""
    solution = _resolve_solution(ctx, solution)
    with d365_errors(ctx):
        info = fieldsec_mod.create_profile(
            ctx.backend(), name=name, description=description, solution=solution,
        )
    _emit_with_warning(ctx, info, None, meta=ctx.staged_meta())
    _journal(ctx, name, info, solution=solution)


@fieldsec_group.command("add-permission")
@click.argument("profile")
@click.argument("entity")
@click.argument("attribute")
@click.option("--read", is_flag=True, help="Grant read access to the column.")
@click.option("--create", is_flag=True, help="Grant create access to the column.")
@click.option("--update", is_flag=True, help="Grant update access to the column.")
@_solution_option
@pass_ctx
def fieldsec_add_permission(ctx: CLIContext, profile, entity, attribute,
                            read, create, update,
                            solution) -> None:
    """Grant column permissions on ENTITY.ATTRIBUTE for PROFILE (name or id).

    Pass at least one of --read / --create / --update.
    """
    # A missing-flag combination is a CLI usage error (exit 2 / usage envelope),
    # matching how `assign` rejects its principal flags — caught before the backend
    # is built. The core function repeats the check for direct (non-CLI) callers.
    if not (read or create or update):
        raise click.UsageError("pass at least one of --read / --create / --update.")
    solution = _resolve_solution(ctx, solution)
    with d365_errors(ctx):
        info = fieldsec_mod.add_permission(
            ctx.backend(), profile=profile, entity=entity, attribute=attribute,
            read=read, create=create, update=update, solution=solution,
        )
    _emit_with_warning(ctx, info, None, meta=ctx.staged_meta())
    _journal(ctx, f"{entity}.{attribute}", info, solution=solution)


@fieldsec_group.command("assign")
@click.argument("profile")
@click.option("--user", "user_id", default=None, help="System user id to assign.")
@click.option("--team", "team_id", default=None, help="Team id to assign.")
@pass_ctx
def fieldsec_assign(ctx: CLIContext, profile, user_id, team_id) -> None:
    """Assign PROFILE (name or id) to a user or a team.

    Pass exactly one of --user / --team.
    """
    if bool(user_id) == bool(team_id):
        raise click.UsageError("pass exactly one of --user / --team.")
    with d365_errors(ctx):
        info = fieldsec_mod.assign(
            ctx.backend(), profile=profile, user_id=user_id, team_id=team_id,
        )
    ctx.emit(True, data=info)
    _journal(ctx, profile, info)
