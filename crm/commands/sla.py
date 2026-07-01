"""SLA commands."""
# pyright: basic
from __future__ import annotations
from pathlib import Path
import click
from crm.core import sla as sla_mod
from crm.cli import CLIContext, pass_ctx
from crm.utils.d365_backend import D365Error, normalize_guid
from crm.commands._helpers import (
    d365_errors,
    _admin_header_options,
    _admin_kwargs,
    _emit_with_warning,
    _journal,
    _resolve_solution,
    _solution_option,
)


@click.group("sla")
def sla_group():
    """Create, configure, and activate D365 SLAs and their backing workflows."""


def _inline_or_file(inline: str | None, file: str | None, flag: str) -> str:
    """Resolve an XML/condition value from an inline string or a file path.

    Mirrors `query fetchxml` (`--xml` / `--xml-file`): exactly one source is
    required; the file is read as UTF-8."""
    if inline and file:
        raise click.UsageError(f"pass only one of {flag} / {flag}-file.")
    if inline:
        return inline
    if file:
        # Surface an unreadable / non-UTF-8 file as a clean CLI error (mirrors
        # _load_payload), not a raw traceback that would break the --json envelope.
        try:
            return Path(file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise click.UsageError(f"cannot read {flag}-file: {exc}") from exc
    raise click.UsageError(f"{flag} (or {flag}-file) is required.")


def _ui_required_error(result: dict) -> str:
    """Human message for the compile-error failure: which workflows failed and
    why the Web API path is blocked."""
    failed = [w for w in result.get("workflows", []) if w.get("status") == "failed"]
    lines = [
        f"SLA {result.get('name') or result.get('sla_id')!r} was NOT activated: "
        f"{len(failed)} backing workflow(s) failed to activate."
    ]
    for w in failed:
        steps = "; ".join(
            f"{e['step']}: {', '.join(e['errors'])}" for e in w.get("errors", [])
        ) or w.get("error", "")
        lines.append(f"  - {w.get('name') or w['workflow_id']}: {steps}")
    lines.append(
        "These are compile errors (e.g. InvalidEntity/InvalidRelationship) in the "
        "workflow definition; the Web API cannot activate a workflow that fails "
        "compilation, so activation must be done from the D365 UI: "
        "Settings > Service Level Agreements > open the SLA > Activate."
    )
    return "\n".join(lines)


@sla_group.command("create")
@click.option("--name", required=True, help="SLA name.")
@click.option("--entity", required=True,
              help="Target entity logical name (objecttypecode); also the "
                   "entity whose IsSLAEnabled flag is verified/set.")
@click.option("--applicable-from", "applicable_from", default=None,
              help="Date-anchor field the SLA calculates from, e.g. 'createdon' "
                   "(sla.applicablefrom).")
@click.option("--business-hours", "business_hours", metavar="GUID", default=None,
              help="Business-hours calendar id (sla.businesshoursid).")
@click.option("--description", default=None, help="SLA description.")
@_solution_option
@_admin_header_options
@pass_ctx
def sla_create(ctx: CLIContext, name, entity, applicable_from, business_hours,
               description, solution,
               as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Create an SLA for a target entity and ensure that entity is SLA-enabled.

    Defines the SLA record; attach KPI / SLA-item conditions with
    `sla add-kpi`, then activate with `sla activate`. The `sla` entity has no
    FetchXML condition of its own — per-KPI `--applicable-when` /
    `--success-criteria` live on `sla add-kpi`.
    """
    # Validate the optional business-hours GUID before building an authenticated
    # backend (house rule: validate untrusted input before ctx.backend()).
    with d365_errors(ctx):
        if business_hours is not None and normalize_guid(business_hours) is None:
            raise D365Error(f"Invalid GUID for --business-hours: {business_hours!r}")
    solution = _resolve_solution(ctx, solution)
    with d365_errors(ctx):
        info = sla_mod.create_sla(
            ctx.backend(), name=name, entity=entity,
            applicable_from=applicable_from, business_hours_id=business_hours,
            description=description, solution=solution,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection,
                            bypass_plugins),
        )
    _emit_with_warning(ctx, info, None, meta=ctx.staged_meta())
    _journal(ctx, name, info, solution=solution)


@sla_group.command("add-kpi")
@click.option("--sla", "sla_id", required=True, help="Parent SLA id.")
@click.option("--kpi", required=True,
              help="KPI field the item tracks (slaitem.relatedfield); also the "
                   "default item name.")
@click.option("--name", default=None, help="SLA-item name (defaults to --kpi).")
@click.option("--applicable-when", "applicable_when", default=None,
              help="FetchXML/condition for when the KPI applies "
                   "(slaitem.applicablewhenxml).")
@click.option("--applicable-when-file", "applicable_when_file", default=None,
              type=click.Path(exists=True, dir_okay=False),
              help="Read --applicable-when from a file.")
@click.option("--success-criteria", "success_criteria", default=None,
              help="FetchXML/condition defining KPI success "
                   "(slaitem.successconditionsxml).")
@click.option("--success-criteria-file", "success_criteria_file", default=None,
              type=click.Path(exists=True, dir_okay=False),
              help="Read --success-criteria from a file.")
@_solution_option
@_admin_header_options
@pass_ctx
def sla_add_kpi(ctx: CLIContext, sla_id, kpi, name, applicable_when,
                applicable_when_file, success_criteria, success_criteria_file,
                solution,
                as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Attach a KPI / SLA-item to an existing SLA before activation.

    --applicable-when and --success-criteria each accept an inline FetchXML/
    condition string or a `*-file` path.
    """
    applicable_when = _inline_or_file(
        applicable_when, applicable_when_file, "--applicable-when")
    success_criteria = _inline_or_file(
        success_criteria, success_criteria_file, "--success-criteria")
    # Validate the GUID before building an authenticated backend, matching
    # `sla activate` — an invalid id fails fast without a session round-trip.
    with d365_errors(ctx):
        sla_id = sla_mod.validate_sla_id(sla_id)
    solution = _resolve_solution(ctx, solution)
    with d365_errors(ctx):
        info = sla_mod.add_kpi(
            ctx.backend(), sla_id=sla_id, kpi=kpi, name=name,
            applicable_when=applicable_when, success_criteria=success_criteria,
            solution=solution,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection,
                            bypass_plugins),
        )
    _emit_with_warning(ctx, info, None, meta=ctx.staged_meta())
    _journal(ctx, kpi, info, solution=solution)


@sla_group.command("activate")
@click.argument("sla_id")
@_admin_header_options
@pass_ctx
def sla_activate(ctx: CLIContext, sla_id, as_user, as_user_object_id,
                 suppress_dup_detection, bypass_plugins):
    """Activate an SLA: its backing workflows first, then the SLA record.

    Backing workflows already active are skipped, so re-running is safe.
    If any backing workflow fails to activate (compile errors after a
    solution import), the SLA is left untouched and the per-workflow error
    report explains why UI activation is required.
    """
    with d365_errors(ctx):
        sla_id = sla_mod.validate_sla_id(sla_id)
    with d365_errors(ctx):
        result = sla_mod.activate_sla(
            ctx.backend(), sla_id,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection,
                            bypass_plugins),
        )
    if result.get("ui_activation_required"):
        ctx.emit(False, data=result, error=_ui_required_error(result))
        return
    ctx.emit(True, data=result)
    _journal(ctx, sla_id, result)
