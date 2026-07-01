"""Workflow commands."""
# pyright: basic
from __future__ import annotations
from pathlib import Path
import click
from crm.core import workflow as workflow_mod
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _destructive_option,
    _confirm_destructive,
    _admin_header_options,
    _admin_kwargs,
    _journal,
    d365_errors,
    _output_option,
    _solution_option,
    _resolve_solution,
)

_CATEGORY_NAMES = {
    "workflow": workflow_mod.CATEGORY_WORKFLOW,
    "dialog": workflow_mod.CATEGORY_DIALOG,
    "businessrule": workflow_mod.CATEGORY_BUSINESS_RULE,
    "action": workflow_mod.CATEGORY_ACTION,
    "bpf": workflow_mod.CATEGORY_BPF,
    "flow": workflow_mod.CATEGORY_MODERN_FLOW,
}

_CATEGORY_NAMES_LIST = ", ".join(_CATEGORY_NAMES)


class _WorkflowCategoryType(click.ParamType):
    name = "category"

    def convert(self, value, param, ctx):
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except (ValueError, TypeError):
            pass
        lower = value.lower()
        if lower in _CATEGORY_NAMES:
            return _CATEGORY_NAMES[lower]
        self.fail(
            f"{value!r} is not a valid category. "
            f"Use an integer or one of: {_CATEGORY_NAMES_LIST}.",
            param,
            ctx,
        )


_SCOPE_NAMES = {
    "user": 1,
    "businessunit": 2,
    "parentchildbusinessunits": 3,
    "organization": 4,
}

_SCOPE_NAMES_LIST = ", ".join(_SCOPE_NAMES)


class _WorkflowScopeType(click.ParamType):
    name = "scope"

    def convert(self, value, param, ctx):
        if isinstance(value, int):
            resolved = value
        else:
            lower = str(value).strip().lower()
            if lower in _SCOPE_NAMES:
                resolved = _SCOPE_NAMES[lower]
            else:
                try:
                    resolved = int(lower)
                except (ValueError, TypeError):
                    self.fail(
                        f"{value!r} is not a valid scope. Use an integer (1–4) "
                        f"or one of: {_SCOPE_NAMES_LIST}.",
                        param,
                        ctx,
                    )
        if resolved not in set(_SCOPE_NAMES.values()):
            self.fail(
                f"{value!r} is out of range — scope must be 1–4 "
                f"(or one of: {_SCOPE_NAMES_LIST}).",
                param,
                ctx,
            )
        return resolved


@click.group("workflow")
def workflow_group():
    """List, activate, and trigger D365 workflows."""


def _redirect_note_meta(info: dict) -> dict | None:
    """Meta carrying the activation-record redirect note, or None when the
    state change ran against the GUID that was passed. The note travels via
    meta (not gated on json_mode) so it renders in human mode too."""
    resolved_from = info.get("resolved_from_activation_id")
    if not resolved_from:
        return None
    return {"note": (
        f"Operated on parent definition {info['workflow_id']}; "
        f"activation-record GUID {resolved_from} was passed."
    )}


@workflow_group.command("list")
@click.option("--category", type=_WorkflowCategoryType(),
              help="Filter by category. Accepts an integer (0–5) or a friendly name: "
                   "workflow, dialog, businessrule, action, bpf, flow.")
@click.option("--entity", "primary_entity", help="Filter by primary entity logical name.")
@click.option("--activated/--all", "activated_only", default=False,
              help="Restrict to activated workflows. Default returns all states.")
@click.option("--on-demand", "on_demand_only", is_flag=True, default=False,
              help="Only on-demand workflows.")
@pass_ctx
def workflow_list(ctx: CLIContext, category, primary_entity, activated_only, on_demand_only):
    """List workflow definitions."""
    with d365_errors(ctx):
        items = workflow_mod.list_workflows(
            ctx.backend(),
            category=category,
            primary_entity=primary_entity,
            activated_only=activated_only,
            on_demand_only=on_demand_only,
        )
    ctx.emit(True, data=items, meta={"count": len(items)})


@workflow_group.command("migration-assess")
@click.option("--entity", "primary_entity", help="Filter by primary entity logical name.")
@pass_ctx
def workflow_migration_assess(ctx: CLIContext, primary_entity):
    """Assess classic workflows for migration to cloud (Power Automate) flows.

    Inventories category-0 workflow definitions and flags blockers from the MS
    capability table: real-time (synchronous) mode, wait conditions, and custom
    workflow activities. Read-only. Blockers are "needs redesign" signals, not
    verdicts of impossibility. On an on-prem profile the report still runs and
    carries an advisory note (cloud flows live only on Dataverse online).
    """
    with d365_errors(ctx):
        backend = ctx.backend()
        items = workflow_mod.assess_workflow_migrations(
            backend, primary_entity=primary_entity)
    meta: dict[str, object] = {"count": len(items)}
    if backend.profile.auth_scheme != "oauth":
        meta["note"] = (
            "Cloud flows don't exist on on-prem; the migration target must be a "
            "Dataverse online environment. This report assesses readiness only."
        )
    ctx.emit(True, data=items, meta=meta)


def _activation_enrich(ctx: CLIContext, workflow_id):
    """`enrich(exc)` for activate/deactivate: derive the activation-record hint
    only for the code the state PATCH raises. Gating on the code keeps
    `ctx.backend()` (cached after a successful PATCH) off the path where the
    original error came from building the backend."""
    def _enrich(exc):
        hint = (workflow_mod.activation_record_hint(ctx.backend(), workflow_id, exc)
                if exc.code == workflow_mod.ACTIVATION_PATCH_ERROR_CODE else None)
        return hint, None
    return _enrich


@workflow_group.command("activate")
@click.argument("workflow_id")
@_admin_header_options
@pass_ctx
def workflow_activate(ctx: CLIContext, workflow_id, as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Activate a workflow (statecode=1, statuscode=2).

    An activation-record GUID (type=2) is resolved to its parent definition
    automatically and the state change is applied to the parent.
    """
    with d365_errors(ctx, enrich=_activation_enrich(ctx, workflow_id)):
        info = workflow_mod.set_workflow_state(
            ctx.backend(), workflow_id, activate=True,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    ctx.emit(True, data=info, meta=_redirect_note_meta(info))
    _journal(ctx, workflow_id, info)


@workflow_group.command("deactivate")
@click.argument("workflow_id")
@_destructive_option
@_admin_header_options
@pass_ctx
def workflow_deactivate(ctx: CLIContext, workflow_id, yes, as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Deactivate a workflow (statecode=0, statuscode=1).

    An activation-record GUID (type=2) is resolved to its parent definition
    automatically and the state change is applied to the parent.
    """
    # Deactivate is a state change, not a delete — name the actual effect rather
    # than the shared helper's default "permanently delete" wording.
    _confirm_destructive(
        ctx, "workflow", workflow_id, yes,
        message=f"This will deactivate workflow {workflow_id!r} (statecode=0). Continue?")
    with d365_errors(ctx, enrich=_activation_enrich(ctx, workflow_id)):
        info = workflow_mod.set_workflow_state(
            ctx.backend(), workflow_id, activate=False,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    ctx.emit(True, data=info, meta=_redirect_note_meta(info))
    _journal(ctx, workflow_id, info)


@workflow_group.command("delete")
@click.argument("workflow_id")
@_destructive_option
@_admin_header_options
@pass_ctx
def workflow_delete(ctx: CLIContext, workflow_id, yes,
                    as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Delete a workflow definition, deactivating it first if active.

    An activation-record GUID (type=2) is resolved to its parent definition;
    deleting the definition removes the activation record server-side. Not
    atomic: if the deactivate lands but the delete fails, the definition
    remains a draft (no rollback).
    """
    admin = _admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins)
    with d365_errors(ctx):
        target = workflow_mod.resolve_delete_target(
            ctx.backend(), workflow_id,
            caller_id=admin["caller_id"],
            caller_object_id=admin["caller_object_id"],
        )
    # The prompt must name the resolved target: with an activation-record GUID
    # the verb deletes a *different record* (the parent definition). desc is
    # plain — _confirm_destructive's default prompt applies !r itself, and the
    # custom message below adds !r explicitly so both paths render identically.
    desc = f"{target['name'] or '<unnamed>'} ({target['workflow_id']})"
    message = None
    if target["resolved_from_activation_id"]:
        message = (
            f"This will permanently delete workflow definition {desc!r} — you "
            f"passed its activation record {workflow_id}, which the server "
            "removes with the definition. Continue?"
        )
    _confirm_destructive(ctx, "workflow definition", desc, yes, message=message)
    with d365_errors(ctx):
        info = workflow_mod.delete_workflow(
            ctx.backend(), workflow_id, resolved=target, **admin)
    ctx.emit(True, data=info, meta=_redirect_note_meta(info))
    _journal(ctx, workflow_id, info)


@workflow_group.command("update")
@click.argument("workflow_id")
@click.option("--name", default=None, help="New display name.")
@click.option("--scope", type=_WorkflowScopeType(), default=None,
              help="Execution scope. Integer (1–4) or a name: "
                   f"{_SCOPE_NAMES_LIST}.")
@click.option("--on-demand/--no-on-demand", "on_demand", default=None,
              help="Toggle whether the workflow can be run on demand.")
@click.option("--on-create/--no-on-create", "trigger_on_create", default=None,
              help="Toggle the trigger-on-create flag.")
@click.option("--on-delete/--no-on-delete", "trigger_on_delete", default=None,
              help="Toggle the trigger-on-delete flag.")
@click.option("--on-update-attributes", "trigger_on_update_attributes", default=None,
              help="Comma-separated attribute logical names that trigger the "
                   "workflow on update. Pass an empty string to clear it "
                   "(disables the on-update trigger).")
@click.option("--xaml-file", type=click.Path(exists=True, dir_okay=False), default=None,
              help="Replace the workflow's step logic with the XAML in this file "
                   "(whole-definition replace). On-premises only — refuses on "
                   "Dataverse. Mutually exclusive with the metadata flags above.")
@click.option("--strict", is_flag=True, default=False,
              help="With --xaml-file: promote any reference-validation warning "
                   "to a hard failure before writing.")
@click.option("--rollback/--no-rollback", default=True,
              help="With --xaml-file: on reactivation failure, restore the prior "
                   "XAML (default). --no-rollback leaves the rejected XAML in "
                   "place for inspection.")
@_admin_header_options
@pass_ctx
def workflow_update(ctx: CLIContext, workflow_id, name, scope, on_demand,
                    trigger_on_create, trigger_on_delete, trigger_on_update_attributes,
                    xaml_file, strict, rollback,
                    as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Edit a workflow definition's metadata, or replace its step XAML.

    Metadata path (default) — edit name, scope, triggers, on-demand. Works on
    both targets (not provenance-gated). A published (activated) definition is
    edited via an automatic deactivate -> edit -> reactivate cycle; a draft is
    edited in place. Only the fields you pass change.

    XAML logic path (--xaml-file) — replace the whole step definition. This is
    **on-premises only**; on Dataverse it refuses up front with the provenance
    wall. The blob is reference-validated against the entity's live attribute
    set (warnings land on meta.warnings; --strict promotes them to a failure),
    then driven deactivate-if-active -> PATCH xaml -> reactivate. A failed
    reactivation rolls back to the prior XAML by default (--no-rollback keeps
    the rejected one). An activation-record GUID (type=2) resolves to its parent
    definition first in either path.
    """
    metadata_flags = (name, scope, on_demand, trigger_on_create,
                      trigger_on_delete, trigger_on_update_attributes)
    xaml = None
    if xaml_file is not None:
        if any(v is not None for v in metadata_flags):
            raise click.UsageError(
                "--xaml-file replaces the workflow's step logic wholesale and "
                "cannot be combined with the metadata flags; run them separately.")
        try:
            xaml = Path(xaml_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise click.UsageError(f"cannot read --xaml-file: {exc}") from exc
        except UnicodeDecodeError as exc:
            raise click.UsageError(f"--xaml-file is not valid UTF-8: {exc}") from exc
    else:
        # --strict / --no-rollback only steer the XAML path; reject them on the
        # metadata path rather than silently ignoring them. (`rollback` defaults
        # True, so `not rollback` means --no-rollback was passed explicitly.)
        if strict or not rollback:
            raise click.UsageError(
                "--strict and --rollback/--no-rollback only apply with --xaml-file.")
        if all(v is None for v in metadata_flags):
            raise click.UsageError(
                "Pass at least one field to update: --name, --scope, "
                "--on-demand/--no-on-demand, --on-create/--no-on-create, "
                "--on-delete/--no-on-delete, --on-update-attributes, or --xaml-file.")
    with d365_errors(ctx):
        info = workflow_mod.update_workflow(
            ctx.backend(), workflow_id,
            name=name, scope=scope, on_demand=on_demand,
            trigger_on_create=trigger_on_create,
            trigger_on_delete=trigger_on_delete,
            trigger_on_update_attributes=trigger_on_update_attributes,
            xaml=xaml, strict=strict, rollback=rollback,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    # Route the XAML path's reference-validation warnings through emit's
    # structured `warnings=` channel (#64) — it appends to meta.warnings in JSON
    # mode and prints cleanly in human mode. Pop them out of `data` so they are
    # not also echoed there. The metadata path has no warnings (pop → None).
    warnings = info.pop("warnings", None)
    ctx.emit(True, data=info, meta=_redirect_note_meta(info), warnings=warnings or None)
    _journal(ctx, workflow_id, info)


@workflow_group.command("run")
@click.argument("workflow_id")
@click.option("--target", "target_record_id", required=True,
              help="GUID of the record to run the workflow against.")
@_admin_header_options
@pass_ctx
def workflow_run(ctx: CLIContext, workflow_id, target_record_id,
                 as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Trigger an on-demand workflow against a target record via ExecuteWorkflow."""
    with d365_errors(ctx):
        info = workflow_mod.execute_workflow(
            ctx.backend(), workflow_id, target_record_id,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    ctx.emit(True, data=info)
    _journal(ctx, workflow_id, info)


@workflow_group.command("clone")
@click.argument("workflow_id")
@click.option("--to-entity", "target_entity", required=True,
              help="Logical name of the entity to clone the workflow onto.")
@click.option("--name", default=None, help="Name for the clone. Default: '<source> (Clone)'.")
@click.option("--activate/--no-activate", default=True,
              help="Activate the clone after creating it (compiles the xaml). Default: activate.")
@_solution_option
@_admin_header_options
@pass_ctx
def workflow_clone(ctx: CLIContext, workflow_id, target_entity, name, activate, solution,
                   as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Clone a workflow definition onto another entity (xaml-retargeted)."""
    solution = _resolve_solution(ctx, solution)
    with d365_errors(ctx):
        info = workflow_mod.clone_workflow_to_entity(
            ctx.backend(), workflow_id, target_entity,
            name=name, activate=activate, solution=solution,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    ctx.emit(True, data=info)
    _journal(ctx, workflow_id, info)


@workflow_group.command("export")
@click.argument("workflow_id")
@_output_option(help="Write the workflow definition to this JSON file. Default: stdout only.")
@click.option("--out", "out_path", hidden=True, type=click.Path(dir_okay=False))
@pass_ctx
def workflow_export(ctx: CLIContext, workflow_id, output, out_path):
    """Export a workflow definition (incl. xaml) to a JSON file."""
    with d365_errors(ctx):
        info = workflow_mod.export_workflow(ctx.backend(), workflow_id, out_path=output or out_path)
    ctx.emit(True, data=info)


@workflow_group.command("import")
@click.option("--file", "file_path", required=True,
              type=click.Path(exists=True, file_okay=True, dir_okay=False),
              help="Exported workflow JSON file to upsert.")
@click.option("--activate/--no-activate", default=False,
              help="Activate after import. Default: leave as draft.")
@_admin_header_options
@pass_ctx
def workflow_import(ctx: CLIContext, file_path, activate,
                    as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Import (upsert) a workflow definition from an exported JSON file."""
    with d365_errors(ctx):
        info = workflow_mod.import_workflow(
            ctx.backend(), file_path=file_path, activate=activate,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    ctx.emit(True, data=info)
    _journal(ctx, info.get("workflow_id", ""), info)
