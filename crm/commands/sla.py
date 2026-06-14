"""SLA commands."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import sla as sla_mod
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    d365_errors,
    _admin_header_options,
    _admin_kwargs,
    _journal,
)


@click.group("sla")
def sla_group():
    """Activate D365 SLAs and their backing workflows."""


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
