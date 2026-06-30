"""Duplicate-detection rule commands — `crm dup`."""
# pyright: basic
from __future__ import annotations

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _emit_with_warning,
    _journal,
    _load_payload,
    _resolve_solution,
    _solution_option,
    d365_errors,
)
from crm.core import dup as dup_mod


@click.group("dup")
def dup_group() -> None:
    """Manage duplicate-detection rules (duplicaterule / duplicaterulecondition)."""


@dup_group.command("list")
@click.option("--entity", default=None,
              help="Filter to rules whose base entity is this logical name.")
@pass_ctx
def dup_list(ctx: CLIContext, entity: str | None) -> None:
    """List duplicate-detection rules."""
    with d365_errors(ctx):
        rules = dup_mod.list_rules(ctx.backend(), entity=entity)
    rows = [
        [r.get("name", ""), r.get("duplicateruleid") or "",
         r.get("baseentityname") or "", r.get("matchingentityname") or "",
         str(r.get("statuscode"))]
        for r in rules
    ]
    ctx.emit(True, data=rules, table={
        "headers": ["name", "duplicateruleid", "base", "matching", "statuscode"],
        "rows": rows,
    })


@dup_group.command("get")
@click.argument("rule")
@pass_ctx
def dup_get(ctx: CLIContext, rule: str) -> None:
    """Show one rule (by name or id) and the conditions it carries."""
    with d365_errors(ctx):
        info = dup_mod.get_rule(ctx.backend(), rule)
    ctx.emit(True, data=info)


@dup_group.command("create")
@click.argument("entity")
@click.option("--name", required=True, help="Rule display name.")
@click.option("--matching-entity", default=None,
              help="Matching entity logical name (defaults to ENTITY).")
@click.option("--description", default=None, help="Rule description.")
@_solution_option
@pass_ctx
def dup_create(ctx: CLIContext, entity, name, matching_entity, description,
               solution) -> None:
    """Create an (unpublished) duplicate-detection rule on ENTITY.

    The rule is created unpublished — add conditions with `dup add-condition`,
    then activate it with `dup publish`.
    """
    solution, warning = _resolve_solution(ctx, solution)
    with d365_errors(ctx):
        info = dup_mod.create_rule(
            ctx.backend(), name=name, entity=entity,
            matching_entity=matching_entity, description=description,
            solution=solution,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, name, info, solution=solution)


@dup_group.command("add-condition")
@click.argument("rule")
@click.option("--attr", "attribute", required=True,
              help="Base column logical name to compare.")
@click.option("--operator", required=True, type=click.Choice(list(dup_mod.OPERATORS)),
              help="Match operator for the condition.")
@click.option("--matching-attr", "matching_attribute", default=None,
              help="Matching column logical name (defaults to --attr).")
@click.option("--operator-param", type=int, default=None,
              help="Character count N for same-first / same-last operators.")
@click.option("--ignore-blank-values", is_flag=True,
              help="Treat blank values as non-duplicates (do not match on null).")
@_solution_option
@pass_ctx
def dup_add_condition(ctx: CLIContext, rule, attribute, operator,
                      matching_attribute, operator_param, ignore_blank_values,
                      solution) -> None:
    """Add a match condition to RULE (name or id).

    --operator one of: exact, same-first, same-last, same-date, same-datetime,
    exact-picklist-label, exact-picklist-value. The same-first / same-last
    operators require --operator-param N (the character count); the others
    reject it.
    """
    solution, warning = _resolve_solution(ctx, solution)
    with d365_errors(ctx):
        info = dup_mod.add_condition(
            ctx.backend(), rule=rule, attribute=attribute, operator=operator,
            matching_attribute=matching_attribute, operator_param=operator_param,
            ignore_blank_values=ignore_blank_values, solution=solution,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, f"{rule}:{attribute}", info, solution=solution)


@dup_group.command("publish")
@click.argument("rule")
@click.option("--wait", is_flag=True,
              help="Block until the async publish job completes.")
@click.option("--timeout", type=int, default=None,
              help="Seconds to wait for the job (with --wait).")
@pass_ctx
def dup_publish(ctx: CLIContext, rule, wait, timeout) -> None:
    """Publish RULE (name or id) — submits the async PublishDuplicateRule job.

    The rule must carry at least one condition. Without --wait the command
    returns once the job is submitted; with --wait it polls to completion.
    """
    with d365_errors(ctx):
        info = dup_mod.publish_rule(ctx.backend(), rule, wait=wait, timeout=timeout)
    ctx.emit(True, data=info)
    _journal(ctx, rule, info)


@dup_group.command("unpublish")
@click.argument("rule")
@pass_ctx
def dup_unpublish(ctx: CLIContext, rule) -> None:
    """Unpublish RULE (name or id) — UnpublishDuplicateRule (synchronous)."""
    with d365_errors(ctx):
        info = dup_mod.unpublish_rule(ctx.backend(), rule)
    ctx.emit(True, data=info)
    _journal(ctx, rule, info)


@dup_group.command("check")
@click.argument("entity")
@click.option("--data-file", default=None,
              help="Path to a JSON file with the candidate record's column values.")
@click.option("--data", "data_json", default=None,
              help="Inline JSON object of the candidate record's column values.")
@click.option("--matching-entity", default=None,
              help="Matching entity logical name (defaults to ENTITY).")
@click.option("--top", type=int, default=50, show_default=True,
              help="Maximum number of matches to return.")
@pass_ctx
def dup_check(ctx: CLIContext, entity, data_file, data_json, matching_entity, top) -> None:
    """Test a candidate ENTITY record against the published rules (RetrieveDuplicates).

    Supply the candidate record's column values via --data-file or --data. Only
    **published** rules on a duplicate-detection-enabled entity match; with no
    published rule the result is always empty.
    """
    record = _load_payload(data_json, data_file)
    with d365_errors(ctx):
        info = dup_mod.check(
            ctx.backend(), entity=entity, record=record,
            matching_entity=matching_entity, top=top,
        )
    ctx.emit(True, data=info)
