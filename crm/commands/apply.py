"""Declarative desired-state apply command (`crm apply -f spec.yaml`).

Reads a YAML or JSON spec and orchestrates the metadata cores in dependency
order via crm.core.apply. Honors the global --dry-run (full drift report:
planned/updated/replace_blocked/pruned, no writes) and --stage-only (create
without publishing) flags.
"""
# pyright: basic
from __future__ import annotations

import os

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import d365_errors, _journal
from crm.commands._helpers.confirm import _confirm_destructive, _destructive_option
from crm.commands._tty import _stdin_is_tty
from crm.core import apply as apply_mod


@click.command("apply")
@click.option("-f", "--file", "spec_file", required=True,
              type=click.Path(exists=True, dir_okay=False, readable=True),
              help="Path to the YAML or JSON desired-state spec.")
@click.option("--include-referenced-optionsets/--no-include-referenced-optionsets",
              "include_referenced_optionsets", default=True, show_default=True,
              help="Add a picklist's referenced global option set to the target "
                   "solution (covers pre-existing globals the create step skips).")
@click.option("--prune", is_flag=True,
              help="Delete components in the target solution that the spec no longer "
                   "declares (schema-only kinds). Requires a target solution and a "
                   "confirmation; preview with --dry-run first.")
@click.option("--allow-data-loss", is_flag=True,
              help="With --prune, also delete data-bearing extras (entities, "
                   "attributes) — this destroys their row data.")
@_destructive_option
@pass_ctx
def apply_cmd(ctx: CLIContext, spec_file, include_referenced_optionsets,
              prune, allow_data_loss, yes):
    """Apply a declarative desired-state spec.

    The spec declares a publisher, solution, entities (with attributes, option
    sets, relationships, and views), web resources, security roles, and plug-ins
    (assembly + types + steps + images), driven in dependency order with
    PublishAllXml once at the end (web resources are published with everything
    else; security roles and plug-in registration are not publishable). A web
    resource's or plug-in assembly's `file` path is resolved relative to the spec
    file. apply is convergent: a component that already
    exists is reconciled against the spec — left untouched when it matches,
    updated in place when an allowed field drifts, or refused (no write) when the
    divergence would need a destructive drop-and-recreate (see ADR 0014). Emits
    {ok, data:{applied, updated, skipped, replace_blocked, pruned, planned,
    failed}, meta:{staged}}; a replace-blocked component makes ok=false (exit 1).

    With the global --dry-run flag the same reconcile runs read-only and the
    result is a full drift report — `planned` (would create), `updated` (would
    update), `replace_blocked`, and `pruned` (solution components absent from the
    spec, each `{kind, name, deleted: false}`) — assembled from live reads with no
    write issued (#550). --prune opts in to deleting those extras (#553): schema-
    only kinds under a confirmation, data-bearing kinds only with --allow-data-loss.
    """
    import yaml

    if allow_data_loss and not prune:
        raise click.UsageError("--allow-data-loss only applies with --prune.")

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

    # A customization write must target an explicit unmanaged solution: the spec
    # must declare a top-level `solution:` block with `unique_name` (#636). Reject
    # up front as a usage error (exit 2), before prompting or building a backend
    # and including under --dry-run. This also satisfies --prune, which is scoped
    # to that solution's components. (apply_spec re-checks for programmatic callers.)
    sol_block = spec.get("solution")
    if not isinstance(sol_block, dict) or not sol_block.get("unique_name"):
        raise click.UsageError(
            "apply requires a top-level 'solution:' block with 'unique_name' — "
            "customization writes must target an explicit unmanaged solution. Add "
            "a solution: block to the spec (or re-export with "
            "`metadata export-spec --solution <unique_name>`).")

    # Gate destructive pruning behind a confirmation (real runs only — --dry-run
    # is a read-only preview that deletes nothing). Under --json / a non-TTY there
    # is no interactive prompt, so an explicit --yes is required; on a TTY, prompt.
    if prune and not ctx.dry_run:
        if not yes and (ctx.json_mode or not _stdin_is_tty()):
            ctx.emit(False, error="--prune permanently deletes org components and "
                     "needs confirmation: pass --yes (no interactive prompt under "
                     "--json or a non-TTY).")
        scope = (" (including data-bearing entities/attributes — destroys row data)"
                 if allow_data_loss else
                 "; data-bearing entities/attributes are skipped unless "
                 "--allow-data-loss is also passed")
        _confirm_destructive(
            ctx, "org components", "not declared in the spec", yes,
            message=("--prune permanently DELETES components in the target solution "
                     "that the spec no longer declares" + scope
                     + ". This cannot be undone. Continue?"))

    with d365_errors(ctx):
        res = apply_mod.apply_spec(
            ctx.backend(), spec, stage_only=ctx.stage_only,
            include_referenced_optionsets=include_referenced_optionsets,
            base_dir=os.path.dirname(os.path.abspath(spec_file)),
            prune=prune, allow_data_loss=allow_data_loss)

    data = {k: res[k] for k in (
        "applied", "updated", "skipped", "replace_blocked", "pruned", "planned", "failed")}
    # On ok=False the human path prints only `error` (not the data buckets), so
    # summarize the failing components there — otherwise a human running
    # `crm apply` would see "Operation failed" with no reason. JSON carries the
    # full buckets regardless.
    parts: list[str] = []
    if res["replace_blocked"]:
        parts.append("refused (no write) — " + "; ".join(
            f"{e['kind']} {e['name']}: {e.get('reason', 'destructive divergence')}"
            for e in res["replace_blocked"]))
    if res["failed"]:
        parts.append("failed — " + "; ".join(
            f"{e['kind']} {e['name']}: {e.get('error', 'unknown error')}"
            for e in res["failed"]))
    error = " | ".join(parts) or None
    ctx.emit(res["ok"], data=data, error=error, meta={"staged": res["staged"]})
    if res["ok"]:
        _journal(ctx, spec_file, data)
