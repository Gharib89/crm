"""Declarative desired-state apply command (`crm apply -f spec.yaml`).

Reads a YAML or JSON spec and orchestrates the metadata cores in dependency
order via crm.core.apply. Honors the global --dry-run (planned-create preview)
and --stage-only (create without publishing) flags.
"""
# pyright: basic
from __future__ import annotations

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import d365_errors, _journal
from crm.core import apply as apply_mod


@click.command("apply")
@click.option("-f", "--file", "spec_file", required=True,
              type=click.Path(exists=True, dir_okay=False, readable=True),
              help="Path to the YAML or JSON desired-state spec.")
@click.option("--solution", default=None,
              help="Override the spec's target solution (unique name).")
@click.option("--include-referenced-optionsets/--no-include-referenced-optionsets",
              "include_referenced_optionsets", default=True, show_default=True,
              help="Add a picklist's referenced global option set to the target "
                   "solution (covers pre-existing globals the create step skips).")
@pass_ctx
def apply_cmd(ctx: CLIContext, spec_file, solution, include_referenced_optionsets):
    """Apply a declarative desired-state spec.

    The spec declares a publisher, solution, and entities (with attributes,
    option sets, relationships, and views). Each resource is created with
    if_exists=skip in dependency order and PublishAllXml runs once at the end,
    so re-applying an unchanged spec is a no-op. Emits
    {ok, data:{applied, skipped, planned, failed}, meta:{staged}}.
    """
    import yaml

    with open(spec_file, encoding="utf-8") as fh:
        try:
            spec = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            ctx.emit(False, error=f"Could not parse spec file: {exc}")
            return
    if not isinstance(spec, dict):
        ctx.emit(False, error="Spec must be a mapping "
                 "(publisher / solution / entities / optionsets).")
        return

    with d365_errors(ctx):
        res = apply_mod.apply_spec(
            ctx.backend(), spec, solution=solution, stage_only=ctx.stage_only,
            include_referenced_optionsets=include_referenced_optionsets)

    data = {k: res[k] for k in (
        "applied", "updated", "skipped", "replace_blocked", "pruned", "planned", "failed")}
    # On ok=False the human path prints only `error` (not the data buckets), so
    # summarize the replace-blocked components there — otherwise a human running
    # `crm apply` would see "Operation failed" with no reason. JSON carries the
    # full buckets regardless.
    error = None
    if res["replace_blocked"]:
        error = "refused (no write) — " + "; ".join(
            f"{e['kind']} {e['name']}: {e.get('reason', 'destructive divergence')}"
            for e in res["replace_blocked"])
    ctx.emit(res["ok"], data=data, error=error, meta={"staged": res["staged"]})
    if res["ok"]:
        _journal(ctx, spec_file, data)
