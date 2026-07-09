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
from crm.core import apply as apply_mod


@click.command("apply")
@click.option("-f", "--file", "spec_file",
              type=click.Path(exists=True, dir_okay=False, readable=True),
              help="Path to the YAML or JSON desired-state spec. Exactly one of "
                   "-f/--file or --from-plan is required.")
@click.option("--from-plan", "from_plan",
              type=click.Path(exists=True, dir_okay=False, readable=True),
              help="Execute a saved plan (from `--dry-run apply -o`) only if it is "
                   "still exactly true. Mutually exclusive with -f; its intent "
                   "(prune / allow-data-loss / stage-only) is replayed, not "
                   "re-specified. With --dry-run, re-verifies without executing.")
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
@click.option("-o", "--plan-out", "plan_out",
              type=click.Path(dir_okay=False, writable=True),
              help="Serialize the --dry-run drift report to this path as a plan "
                   "artifact (JSON). Valid only with the global --dry-run flag. The "
                   "plan is always written, including when the dry-run exits 1.")
@_destructive_option
@pass_ctx
def apply_cmd(ctx: CLIContext, spec_file, from_plan, include_referenced_optionsets,
              prune, allow_data_loss, plan_out, yes):
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

    # Source selection: exactly one of -f/--file or --from-plan. --from-plan
    # replays a saved plan; -f applies a spec.
    if bool(spec_file) == bool(from_plan):
        raise click.UsageError(
            "apply requires exactly one of -f/--file or --from-plan.")
    if from_plan:
        # Plan intent is fixed at plan time and replayed — re-specifying it here
        # would reintroduce "what runs ≠ what was approved" (ADR 0022). -o
        # serializes a fresh drift report from -f, so it has no meaning either.
        conflicting = [name for name, on in (
            ("--prune", prune), ("--allow-data-loss", allow_data_loss),
            ("--stage-only", ctx.stage_only), ("-o/--plan-out", bool(plan_out)))
            if on]
        if conflicting:
            raise click.UsageError(
                f"{', '.join(conflicting)} cannot be combined with --from-plan — "
                "plan intent is fixed at plan time and replayed (ADR 0022).")
        _apply_from_plan(ctx, from_plan, include_referenced_optionsets, yes)
        return

    if allow_data_loss and not prune:
        raise click.UsageError("--allow-data-loss only applies with --prune.")

    # A plan serializes a drift report, so it only means anything under --dry-run
    # (which suppresses writes while live reads still fire). Reject the combination
    # up front as a usage error (exit 2), before any backend work.
    if plan_out and not ctx.dry_run:
        raise click.UsageError("-o/--plan-out requires the global --dry-run flag.")

    # utf-8-sig tolerates a leading UTF-8 BOM (Windows editors add one) on the
    # spec file, matching crm's file-boundary read policy (#683).
    try:
        with open(spec_file, encoding="utf-8-sig") as fh:
            spec = yaml.safe_load(fh)
    except OSError as exc:
        ctx.emit(False, error=f"Could not read spec file {spec_file}: {exc}")
        return
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

    # Gate destructive pruning behind a confirmation. Under --dry-run the shared
    # helper returns immediately so the command reaches its read-only preview.
    if prune:
        scope = (" (including data-bearing entities/attributes — destroys row data)"
                 if allow_data_loss else
                 "; data-bearing entities/attributes are skipped unless "
                 "--allow-data-loss is also passed")
        _confirm_destructive(
            ctx, "org components", "not declared in the spec", yes,
            message=("--prune permanently DELETES components in the target solution "
                     "that the spec no longer declares" + scope
                     + ". This cannot be undone. Continue?"))

    base_dir = os.path.dirname(os.path.abspath(spec_file))
    with d365_errors(ctx):
        backend = ctx.backend()
        res = apply_mod.apply_spec(
            backend, spec, stage_only=ctx.stage_only,
            include_referenced_optionsets=include_referenced_optionsets,
            base_dir=base_dir,
            prune=prune, allow_data_loss=allow_data_loss)
        # Serialize the drift report as a plan artifact when -o was passed (only
        # reachable under --dry-run, checked above). Written before emit so a
        # replace-blocked dry-run (res.ok False → emit exits 1) still lands the
        # plan — it doubles as the drift-report artifact regardless of exit code.
        if plan_out:
            from crm.core import connection as conn_mod
            from crm.core import plan as plan_mod
            org_id = conn_mod.whoami(backend).get("OrganizationId")
            plan_doc = plan_mod.build_plan(
                spec=spec, report=res, backend=backend, organization_id=org_id,
                solution=sol_block["unique_name"], base_dir=base_dir,
                prune=prune, allow_data_loss=allow_data_loss,
                stage_only=ctx.stage_only)
            plan_mod.write_plan(plan_out, plan_doc)

    meta: dict[str, object] = {"staged": res["staged"]}
    if plan_out:
        meta["plan_out"] = plan_out
    data = _apply_data(res)
    ctx.emit(res["ok"], data=data, error=_apply_error_summary(res), meta=meta)
    if res["ok"]:
        _journal(ctx, spec_file, data)


def _apply_data(res: dict) -> dict:
    """The per-bucket data payload the apply envelope carries (both -f and --from-plan)."""
    return {k: res[k] for k in (
        "applied", "updated", "skipped", "replace_blocked", "pruned", "planned", "failed")}


def _apply_error_summary(res: dict) -> "str | None":
    """Summarize the failing components for the human `error` line, or None.

    On ok=False the human path prints only `error` (not the data buckets), so this
    names the refused/failed components — otherwise a human would see "Operation
    failed" with no reason. JSON carries the full buckets regardless.
    """
    parts: list[str] = []
    if res["replace_blocked"]:
        parts.append("refused (no write) — " + "; ".join(
            f"{e['kind']} {e['name']}: {e.get('reason', 'destructive divergence')}"
            for e in res["replace_blocked"]))
    if res["failed"]:
        parts.append("failed — " + "; ".join(
            f"{e['kind']} {e['name']}: {e.get('error', 'unknown error')}"
            for e in res["failed"]))
    return " | ".join(parts) or None


def _apply_from_plan(ctx: CLIContext, plan_path: str,
                     include_referenced_optionsets: bool, yes: bool) -> None:
    """Execute (or, under --dry-run, re-verify) a saved plan — ADR 0022 slice 2.

    Pre-flight refuses an un-executable plan (bad format / wrong org / unclean /
    payload drift), replays the plan's destructive gate for a real prune run, then
    recomputes the drift report and executes only if the plan is still exactly
    true. `--dry-run --from-plan` stops after the compare (the CI pre-check).
    """
    from crm.core import connection as conn_mod
    from crm.core import plan as plan_mod

    base_dir = os.path.dirname(os.path.abspath(plan_path))
    with d365_errors(ctx):
        plan_doc = plan_mod.load_plan(plan_path)
        backend = ctx.backend()
        org_id = conn_mod.whoami(backend).get("OrganizationId")
        warnings = plan_mod.preflight_plan(
            plan_doc, backend, organization_id=org_id, base_dir=base_dir)
        # Replay the destructive gate only for a real prune execution — a dry-run
        # verify writes nothing, so it needs no confirmation.
        if plan_mod.plan_intent(plan_doc)["prune"] and not ctx.dry_run:
            _confirm_destructive(
                ctx, "org components", "not declared in the plan", yes,
                message=("This plan's intent permanently DELETES components in the "
                         "target solution that the spec no longer declares. This "
                         "cannot be undone. Continue?"))
        outcome = plan_mod.run_plan(
            backend, plan_doc, base_dir=base_dir, verify_only=ctx.dry_run,
            include_referenced_optionsets=include_referenced_optionsets)
    _emit_plan_outcome(ctx, plan_path, outcome, warnings)


def _emit_plan_outcome(ctx: CLIContext, plan_path: str, outcome: dict,
                       warnings: "list[str]") -> None:
    """Map a `run_plan` outcome onto the {ok, data, meta} envelope."""
    meta: dict[str, object] = {"from_plan": plan_path}
    if warnings:
        meta["warnings"] = warnings
    status = outcome["status"]
    if status == "valid":
        ctx.emit(True, data={"plan_valid": True}, meta=meta)
        return
    if status == "stale":
        divergences = outcome["divergences"]
        summary = "; ".join(
            f"{d['kind']} {d['name']}: plan said {d['plan']}, live now computes {d['live']}"
            for d in divergences)
        ctx.emit(False, data={"plan_valid": False, "divergences": divergences},
                 error=f"stale plan — {len(divergences)} component(s) diverged: {summary}",
                 meta=meta)
        return
    # Executed for real: emit the apply result, same shape as the -f path.
    res = outcome["result"]
    meta["staged"] = res["staged"]
    data = _apply_data(res)
    ctx.emit(res["ok"], data=data, error=_apply_error_summary(res), meta=meta)
    if res["ok"]:
        _journal(ctx, plan_path, data)
