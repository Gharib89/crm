"""Scaffold commands — generate metadata from shorthand (e.g. `scaffold table`)."""
# pyright: basic
from __future__ import annotations

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _active_profile,
    _handle_d365_error,
    _journal,
    _require_solution,
    _resolve_solution,
    _solution_option,
)
from crm.core import apply as apply_mod
from crm.core import references as references_mod
from crm.core import scaffold as scaffold_mod
from crm.utils.d365_backend import D365Error


@click.group("scaffold")
def scaffold_group():
    """Generate D365 metadata from shorthand specs."""


@scaffold_group.command("table")
@click.argument("display")
@click.option(
    "--column", "columns",
    multiple=True,
    required=True,
    metavar="DISPLAY:KIND[:opts]",
    help=(
        "Column shorthand: DISPLAY:KIND[:key=value,...]. "
        "KIND is one of string, memo, integer, bigint, decimal, double, money, "
        "boolean, datetime, picklist, multiselect, lookup, image, file. "
        "string/memo default max_length to 100/2000; override with max_length=N. "
        "lookup requires target_entity=<logical_name>. "
        "picklist/multiselect require optionset_name=<name>. "
        "Optional: required=None|Recommended|ApplicationRequired, description=<text> "
        "(opts are comma-separated, so description cannot contain a comma). "
        "Repeatable — pass one --column per column."
    ),
)
@click.option("--schema-name", default=None,
              help="Entity schema name override (PascalCase with prefix, e.g. new_Project). "
                   "Derived from DISPLAY and publisher prefix when omitted.")
@click.option("--display-collection", default=None,
              help="Plural UI label for the entity set. Derived by apply when omitted.")
@click.option(
    "--ownership",
    type=click.Choice(["UserOwned", "OrganizationOwned"]),
    default="UserOwned",
    show_default=True,
    help="Entity ownership model.",
)
@_solution_option
@pass_ctx
def table(
    ctx: CLIContext,
    display: str,
    columns: tuple[str, ...],
    schema_name: str | None,
    display_collection: str | None,
    ownership: str,
    solution: str | None,
    require_solution: bool,
) -> None:
    """Create an entity (table) with N columns in a single publish.

    Builds a one-entity in-memory apply spec from the given display name and
    column shorthands, then runs it through `crm.core.apply.apply_spec`. Each
    resource is created with if_exists=skip in dependency order, so re-running
    the same command is a no-op. One PublishAllXml fires at the end (or is
    suppressed by --stage-only).

    Global --dry-run: reports the entity + all columns as planned without
    making any create calls (only the entity existence GET fires to probe the
    current state).

    Global --stage-only: creates everything but skips the final publish; the
    result envelope carries meta.staged=true.

    Emits {ok, data:{applied, skipped, planned, failed}, meta:{staged}}.
    """
    # --- 1. Resolve publisher prefix (precondition check before any backend call) ---
    profile = _active_profile(ctx)
    prefix = profile.publisher_prefix if profile else None
    if not prefix:
        raise click.UsageError(
            "scaffold table needs a publisher prefix to derive column schema names; "
            "set publisher_prefix on the active profile (e.g. via crm profile edit)."
        )

    # --- 2. Resolve solution ---
    solution, warning = _resolve_solution(ctx, solution, require=_require_solution(require_solution))

    # --- 3. Build the spec (pure, no backend) ---
    try:
        spec = scaffold_mod.build_table_spec(
            display_name=display,
            columns=list(columns),
            prefix=prefix,
            schema_name=schema_name,
            display_collection=display_collection,
            ownership=ownership,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return

    # --- 4. Apply via apply_spec ---
    backend = ctx.backend()
    try:
        res = apply_mod.apply_spec(
            backend, spec, solution=solution, stage_only=ctx.stage_only
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return

    # --- 5. Emit result ---
    data = {k: res[k] for k in ("applied", "skipped", "planned", "failed")}
    warnings = [warning] if warning else []
    # Under dry-run the columns' references (lookup target entities, picklist
    # option sets) are reported even when the (new) table itself is only planned,
    # so a dangling reference is a pre-flight finding, not a write-time fault (#281).
    if ctx.dry_run:
        references = references_mod.resolve_spec_references(backend, spec)
        if references:
            data["references"] = references
            warnings.extend(references_mod.reference_warnings(references))
    ctx.emit(
        res["ok"],
        data=data,
        meta={"staged": res["staged"]},
        warnings=warnings or None,
    )

    # --- 6. Journal on success ---
    if res["ok"]:
        _journal(
            ctx,
            "scaffold table",
            spec["entities"][0]["schema_name"],
            data,
            solution=solution,
        )
