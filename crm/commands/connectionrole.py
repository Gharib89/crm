"""Record-to-record connection role commands — `crm connectionrole`."""
# pyright: basic
from __future__ import annotations

import click

from crm.core import connectionrole as cr_mod
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    d365_errors, _journal, _solution_option, _resolve_solution, _emit_with_warning,
)


@click.group("connectionrole")
def connectionrole_group():
    """Define record-to-record connection roles (create / scope / match)."""


@connectionrole_group.command("create")
@click.option("--name", required=True, help="Connection role name.")
@click.option("--category", type=click.Choice(sorted(cr_mod.CATEGORIES)),
              default=None, help="Connection role category.")
@click.option("--description", default=None, help="Role description.")
@_solution_option
@pass_ctx
def connectionrole_create(ctx: CLIContext, name, category, description,
                          solution) -> None:
    """Create a connection role named NAME."""
    solution = _resolve_solution(ctx, solution)
    with d365_errors(ctx):
        info = cr_mod.create_role(
            ctx.backend(), name=name, category=category,
            description=description, solution=solution,
        )
    _emit_with_warning(ctx, info, None, meta=ctx.staged_meta())
    _journal(ctx, name, info, solution=solution)


@connectionrole_group.command("scope")
@click.argument("role")
@click.option("--entity", required=True,
              help="Entity logical name the role applies to.")
@_solution_option
@pass_ctx
def connectionrole_scope(ctx: CLIContext, role, entity,
                         solution) -> None:
    """Restrict ROLE (name or id) to records of ENTITY.

    Call repeatedly to scope a role to several entity types.
    """
    solution = _resolve_solution(ctx, solution)
    with d365_errors(ctx):
        info = cr_mod.scope(
            ctx.backend(), role=role, entity=entity, solution=solution,
        )
    _emit_with_warning(ctx, info, None, meta=ctx.staged_meta())
    _journal(ctx, f"{role}:{entity}", info, solution=solution)


@connectionrole_group.command("match")
@click.argument("role_a")
@click.argument("role_b")
@pass_ctx
def connectionrole_match(ctx: CLIContext, role_a, role_b) -> None:
    """Pair ROLE_A and ROLE_B (each a name or id) as reciprocal roles."""
    with d365_errors(ctx):
        info = cr_mod.match(ctx.backend(), role_a=role_a, role_b=role_b)
    ctx.emit(True, data=info)
    _journal(ctx, f"{role_a}+{role_b}", info)
