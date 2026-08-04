"""Solution lifecycle commands."""

# pyright: basic
from __future__ import annotations

import json
from pathlib import Path

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _EXPORT_SETTING_KEYS,
    _active_profile,
    _confirm_destructive,
    _destructive_option,
    _journal,
    _no_retry_scope,
    _output_option,
    d365_errors,
    select_one,
)
from crm.commands._tty import _stdin_is_tty
from crm.core import async_ops as async_ops_mod
from crm.core import dependencies as dep_mod
from crm.core import export_spec as export_spec_mod
from crm.core import session as session_mod
from crm.core import solution as sol_mod
from crm.core import solution_validate as sv_mod
from crm.core import solutionpackager as sp_mod
from crm.utils.d365_backend import D365Error


@click.group("solution")
def solution_group():
    """Solution lifecycle (create-publisher / create / list / info / components / export /
    import).
    """


def _autowire_profile(ctx: CLIContext, field: str, value: str, result: dict) -> None:
    """Write `field=value` back to the active NAMED profile after a successful create.

    Command-layer only (the core create functions stay pure). No-op under --dry-run
    or when no named profile is active (env/dotenv connection). Records the outcome
    in `result` so it surfaces in the emitted envelope.
    """
    if ctx.dry_run or result.get("_dry_run"):
        return
    profile = _active_profile(ctx)
    if profile is None:
        result["profile_update"] = "skipped: no named profile"
        return
    setattr(profile, field, value)
    session_mod.save_profile(profile)
    result["profile_updated"] = {"profile": profile.name, field: value}


@solution_group.command("list")
@click.option("--managed/--unmanaged", default=None, help="Filter by managed flag.")
@pass_ctx
def solution_list(ctx: CLIContext, managed):
    with d365_errors(ctx):
        items = sol_mod.list_solutions(ctx.backend(), managed=managed)
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["uniquename", "friendlyname", "version", "ismanaged"]
    rows = [[it.get(h, "") for h in headers] for it in items]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta={"count": len(items)})


@solution_group.command("info")
@click.argument("unique_name")
@pass_ctx
def solution_info_cmd(ctx: CLIContext, unique_name):
    """Show details for one solution (version, managed state, publisher) by unique name."""
    with d365_errors(ctx):
        info = sol_mod.solution_info(ctx.backend(), unique_name)
    ctx.emit(True, data=info)


@solution_group.command("dependencies")
@click.argument("unique_name")
@pass_ctx
def solution_dependencies_cmd(ctx: CLIContext, unique_name):
    """Show blockers that would prevent UNINSTALLING a managed solution.

    Read-only. Calls RetrieveDependenciesForUninstall(SolutionUniqueName='<name>').
    """
    # An empty/blank name is a caller mistake (usage error, exit 2 — ADR 0001),
    # not an operational failure; validate before any network call.
    if not unique_name.strip():
        raise click.UsageError("solution unique name is required.")
    with d365_errors(ctx):
        info = dep_mod.retrieve_dependencies_for_uninstall(ctx.backend(), unique_name)
    meta = {"blockers": info["count"]}
    if ctx.json_mode:
        ctx.emit(True, data=info, meta=meta)
        return
    if info["blockers"]:
        headers = ["Dependent Type", "Dependent Id", "Required Type", "Dependency Type"]
        rows = [
            [b["dependent_type"], b["dependent_id"], b["required_type"], str(b["dependency_type"])]
            for b in info["blockers"]
        ]
        ctx.emit(True, table={"headers": headers, "rows": rows}, meta=meta)
    else:
        # Emit the scalar under `count` (not `blockers`) so the `blockers` key is
        # never an int here while it's a list in JSON mode (Copilot #135).
        ctx.emit(True, data={"solution": info["solution"], "count": 0}, meta=meta)


@solution_group.command("components")
@click.argument("unique_name")
@click.option(
    "--diff",
    "diff_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="Compare live components against this saved JSON snapshot; exits non-zero on drift.",
)
@click.option(
    "--save",
    "save_path",
    default=None,
    type=click.Path(dir_okay=False),
    help="Write a normalized component inventory to this path as JSON.",
)
@click.option(
    "--resolve",
    is_flag=True,
    default=False,
    help="Enrich each component with a rootcomponentbehavior label and a resolved "
    "name (entities, forms, views, attributes, workflows, and more).",
)
@pass_ctx
def solution_components_cmd(ctx: CLIContext, unique_name, diff_path, save_path, resolve):
    """List solution components; with --save write a normalized inventory, with --diff
    compare live vs expected (non-zero exit on drift).
    """
    # A caller mistake (invalid flag combination) is a usage error (exit 2,
    # ADR 0001), not an operational failure — mirror entity update's pattern.
    if diff_path and save_path:
        raise click.UsageError("--diff and --save are mutually exclusive.")
    # --resolve enriches the plain listing; --save/--diff operate on the raw
    # three-key rows, so combining them is a caller mistake.
    if resolve and (diff_path or save_path):
        raise click.UsageError("--resolve is not valid with --save or --diff.")

    # Parse and validate the expected snapshot BEFORE any network call, so a
    # malformed --diff file fails fast without touching the org.
    expected: list | None = None
    if diff_path:
        try:
            text = Path(diff_path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            ctx.emit(False, error=f"Could not read {diff_path!r}: {exc}")
            return
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            ctx.emit(False, error=f"Could not parse {diff_path!r} as JSON: {exc}")
            return
        if not isinstance(raw, list):
            ctx.emit(
                False, error=f"Expected a JSON list in {diff_path!r}, got {type(raw).__name__}."
            )
            return
        expected = raw

    with d365_errors(ctx):
        items = sol_mod.solution_components(ctx.backend(), unique_name)

    if save_path:
        normalized = sol_mod.normalize_components(items)
        out = Path(save_path)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
        except OSError as exc:
            ctx.emit(False, error=f"Could not write {save_path}: {exc}")
            return
        ctx.emit(True, data={"saved": str(out), "count": len(normalized)})
        return

    if diff_path:
        try:
            result = sol_mod.diff_components(items, expected or [])
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            ctx.emit(False, error=f"Malformed component row in {diff_path!r}: {exc}")
            return
        if not result["matches"]:
            msg = (
                f"Drift detected: {len(result['missing'])} missing, "
                f"{len(result['unexpected'])} unexpected component(s)."
            )
            ctx.emit(False, data=result, error=msg)
            return
        ctx.emit(True, data=result, meta={"matches": True})
        return

    # --resolve enriches each row with a rootcomponentbehavior label and a
    # resolved objectid → name (batched; entity-scoped types also carry the
    # parent entity). Unresolvable ids fall back to the raw GUID (name None).
    if resolve:
        with d365_errors(ctx):
            resolved = sol_mod.resolve_component_names(ctx.backend(), items)

        def _enrich(it):
            r = resolved.get(sol_mod.component_key(it.get("componenttype"), it.get("objectid")), {})
            row = {
                **it,
                "componenttypename": sol_mod.component_type_name(it.get("componenttype", 0)),
                "rootcomponentbehaviorname": sol_mod.root_behavior_name(
                    it.get("rootcomponentbehavior")
                ),
                "name": r.get("name"),
            }
            if r.get("entity") is not None:
                row["entity"] = r["entity"]
            return row

        enriched = [_enrich(it) for it in items]
        if ctx.json_mode:
            ctx.emit(True, data=enriched, meta={"count": len(enriched)})
            return
        headers = ["componenttype", "name", "entity", "rootcomponentbehavior", "objectid"]
        rows = [
            [
                r["componenttypename"],
                r.get("name") or "",
                r.get("entity") or "",
                r["rootcomponentbehaviorname"] or "",
                r["objectid"],
            ]
            for r in enriched
        ]
        ctx.emit(True, table={"headers": headers, "rows": rows}, meta={"count": len(items)})
        return

    if ctx.json_mode:
        # Surface the friendly component-type name alongside the raw integer so
        # JSON callers don't each maintain their own componenttype→name map
        # (#627). The human table below already resolves it for display.
        enriched = [
            {**it, "componenttypename": sol_mod.component_type_name(it.get("componenttype", 0))}
            for it in items
        ]
        ctx.emit(True, data=enriched, meta={"count": len(enriched)})
        return

    headers = ["componenttype", "objectid", "rootcomponentbehavior"]
    rows = [
        [
            sol_mod.component_type_name(it.get("componenttype", 0)),
            it.get("objectid", ""),
            "" if it.get("rootcomponentbehavior") is None else it.get("rootcomponentbehavior"),
        ]
        for it in items
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta={"count": len(items)})


@solution_group.command("audit")
@click.argument("unique_name")
@pass_ctx
def solution_audit_cmd(ctx: CLIContext, unique_name):
    """Audit a solution for AddRequiredComponents cascade / whole-entity drift.

    Surfaces two hygiene problems (#916): entities carried as whole-entity
    (rootcomponentbehavior 0 — where accidental bloat hides) vs shells, and
    components that appear only because another component in the SAME solution
    requires them (cascade candidates, not authored here — classified via the
    RetrieveRequiredComponents dependency graph over the solution's entities).
    Read-only; reports drift, never fixes it.
    """
    with d365_errors(ctx):
        report = sol_mod.audit_solution(ctx.backend(), unique_name)

    if ctx.json_mode:
        ctx.emit(True, data=report)
        return

    s = report["summary"]
    click.echo(
        f"{report['solution']}: {s['total_components']} components, "
        f"{s['entity_count']} entities "
        f"({s['whole_entity_count']} whole-entity, {s['shell_count']} shell), "
        f"{s['required_only_count']} required-only candidate(s)."
    )
    whole = report["whole_entities"]
    if whole:
        click.echo("\nWhole-entity components (accidental-bloat risk):")
        for e in whole:
            click.echo(f"  - {e['name'] or e['objectid']}  [{e['behavior_label']}]")
    cands = report["required_only_candidates"]
    if cands:
        click.echo("\nRequired-only candidates (pulled in by another component's cascade):")
        for c in cands:
            req = ", ".join(c["required_by"])
            click.echo(f"  - {c['type_name']} {c['name'] or c['objectid']}  (required by: {req})")


@solution_group.command("export-spec")
@click.argument("unique_name")
@_output_option(
    help="Write the bare merged spec as YAML to FILE (directly consumable by crm apply -f)."
)
@pass_ctx
def solution_export_spec(ctx: CLIContext, unique_name, output):
    """Project a whole solution into one apply-consumable desired-state spec.

    Walks the solution's members (pure GETs, read-only) and merges every entity
    it touches — entity, attribute, global option set, view, 1:N relationship —
    into a single spec via build_entity_spec per touched entity. A top-level
    `solution:` key is emitted so a round-trip `crm --dry-run apply -f <file>`
    against another org auto-scopes its drift/prune report (the source side of
    the org-to-org drift recipe).

    Apply-seedable members project in full: entities (+ their attributes, option
    sets, views, 1:N relationships, seedable main form), security roles (name,
    business unit, privileges by depth), web resources (inline base64 content,
    display name, type), and model-driven apps (identity + Entity-backed sitemap,
    under a top-level `apps:` block). Members that cannot round-trip a real apply
    (plug-ins, additional main forms, an app's record-backed component bindings,
    ...) are reported in a `skipped` bucket or `warnings`; the verb never fails on
    an unsupported component and never drops one silently.

    With -o, the bare YAML spec is written to FILE (apply-ready). Without -o, a
    summary plus the skipped bucket is emitted under the JSON envelope.
    """
    warnings: list[str] = []
    with d365_errors(ctx):
        result = export_spec_mod.build_solution_spec(ctx.backend(), unique_name, warnings=warnings)
    spec = result["spec"]
    skipped = result["skipped"]
    entities = spec.get("entities", [])
    attr_count = sum(len(e.get("attributes", [])) for e in entities)
    form_count = sum(len(e.get("forms", [])) for e in entities)

    if output:
        import yaml

        try:
            with open(output, "w", encoding="utf-8") as fh:
                yaml.safe_dump(spec, fh, sort_keys=False, allow_unicode=True)
        except OSError as exc:
            ctx.emit(False, error=f"Could not write {output!r}: {exc}")
            return
        ctx.emit(
            True,
            data={
                "path": output,
                "solution": unique_name,
                "entities": len(entities),
                "attributes": attr_count,
                "forms": form_count,
                "optionsets": len(spec.get("optionsets", [])),
                "security_roles": len(spec.get("security_roles", [])),
                "webresources": len(spec.get("webresources", [])),
                "apps": len(spec.get("apps", [])),
                "skipped": skipped,
            },
            warnings=warnings or None,
        )
        return

    ctx.emit(
        True,
        data={
            "solution": unique_name,
            "entities": [e.get("schema_name") for e in entities],
            "attributes": attr_count,
            "forms": form_count,
            "optionsets": [o.get("name") for o in spec.get("optionsets", [])],
            "security_roles": [r.get("name") for r in spec.get("security_roles", [])],
            "webresources": [w.get("name") for w in spec.get("webresources", [])],
            "apps": [a.get("unique_name") for a in spec.get("apps", [])],
            "skipped": skipped,
        },
        warnings=warnings or None,
    )


@solution_group.command("missing-components")
@click.argument("solution_file", type=click.Path(exists=True, dir_okay=False, readable=True))
@pass_ctx
def solution_missing_components_cmd(ctx: CLIContext, solution_file):
    """List components an exported solution needs that this org is missing.

    SOLUTION_FILE is a path to an exported solution .zip. The check runs against
    the connected org (the import target): an empty result means the org already
    has everything the solution requires. Read-only — run before importing.
    """
    try:
        with d365_errors(ctx):
            info = sol_mod.retrieve_missing_components(ctx.backend(), solution_file)
    except OSError as exc:
        ctx.emit(False, error=f"Could not read {solution_file}: {exc}")
        return
    ctx.emit(True, data=info["missing_components"], meta={"count": info["count"]})


@solution_group.command("layer-conflicts")
@click.option("--solution", "managed_name", required=True, help="Managed solution unique name.")
@click.option(
    "--unmanaged-solution", "unmanaged_name", required=True, help="Unmanaged solution unique name."
)
@pass_ctx
def solution_layer_conflicts_cmd(ctx: CLIContext, managed_name, unmanaged_name):
    """Report components present in BOTH a managed and an unmanaged solution.

    Those are managed components that also carry unmanaged-layer customizations —
    the potential unmanaged-layer conflicts. Read-only; works identically on v9.x
    on-prem and Dataverse online. Matching is at solution-component granularity: a
    customized subcomponent (e.g. one attribute) of a whole-table managed component
    is its own component and will not show as a conflict.
    """
    with d365_errors(ctx):
        backend = ctx.backend()
        managed_info = sol_mod.solution_info(backend, managed_name)
        unmanaged_info = sol_mod.solution_info(backend, unmanaged_name)

    # Kind validation is data-dependent (`ismanaged` comes from the server), so a
    # mismatch is an in-command validation failure (exit 1, ADR 0001), not a Click
    # usage error. Check before fetching components / comparing.
    if not managed_info.get("ismanaged"):
        ctx.emit(False, error=f"--solution {managed_name!r} is not a managed solution.")
        return
    if unmanaged_info.get("ismanaged"):
        ctx.emit(
            False, error=f"--unmanaged-solution {unmanaged_name!r} is not an unmanaged solution."
        )
        return

    with d365_errors(ctx):
        managed_comps = sol_mod.solution_components(backend, managed_name)
        unmanaged_comps = sol_mod.solution_components(backend, unmanaged_name)
    conflicts = sol_mod.layer_conflicts(managed_comps, unmanaged_comps)

    meta = {"count": len(conflicts)}
    if ctx.json_mode:
        ctx.emit(True, data=conflicts, meta=meta)
        return
    if not conflicts:
        ctx.emit(True, data={"message": "no conflicts found"}, meta=meta)
        return
    headers = ["type", "type_name", "objectid", "managed_rcb", "unmanaged_rcb"]
    rows = [
        [
            str(c["componenttype"]),
            c["type_name"],
            c["objectid"],
            str(c["managed_rootcomponentbehavior"]),
            str(c["unmanaged_rootcomponentbehavior"]),
        ]
        for c in conflicts
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta=meta)


@solution_group.command("create-publisher")
@click.option("--name", required=True, help="Publisher unique name, e.g. 'crmworx'.")
@click.option("--display", "display", default=None, help="Friendly name (defaults to --name).")
@click.option(
    "--prefix",
    required=True,
    help="Customization prefix: 2-8 alphanumeric, starts with a letter, not 'mscrm'. e.g. 'cwx'.",
)
@click.option(
    "--option-value-prefix",
    "option_value_prefix",
    type=int,
    required=True,
    help="Option-value prefix (integer 10000-99999).",
)
@click.option("--if-exists", type=click.Choice(["error", "skip"]), default="error")
@click.option(
    "--set-default/--no-set-default",
    default=True,
    help="Write publisher_prefix back to the active named profile (default on).",
)
@pass_ctx
def solution_create_publisher(
    ctx: CLIContext, name, display, prefix, option_value_prefix, if_exists, set_default
):
    """Create a solution publisher (publishers)."""
    with d365_errors(ctx):
        info = sol_mod.create_publisher(
            ctx.backend(),
            name=name,
            friendly_name=display,
            prefix=prefix,
            option_value_prefix=option_value_prefix,
            if_exists=if_exists,
        )
    if set_default:
        _autowire_profile(ctx, "publisher_prefix", prefix, info)
    ctx.emit(True, data=info)
    _journal(ctx, name, info)


@solution_group.command("create")
@click.option("--name", required=True, help="Solution unique name, e.g. 'CRMWorx'.")
@click.option("--display", "display", default=None, help="Friendly name (defaults to --name).")
@click.option("--version", default="1.0.0.0", help="Solution version (default 1.0.0.0).")
@click.option(
    "--publisher",
    "publisher",
    default=None,
    help="Publisher unique name (mutually exclusive with --publisher-id).",
)
@click.option(
    "--publisher-id",
    "publisher_id",
    default=None,
    help="Publisher GUID (mutually exclusive with --publisher).",
)
@click.option("--if-exists", type=click.Choice(["error", "skip"]), default="error")
@pass_ctx
def solution_create(ctx: CLIContext, name, display, version, publisher, publisher_id, if_exists):
    """Create an unmanaged solution bound to a publisher (solutions)."""
    if bool(publisher) == bool(publisher_id):
        raise click.UsageError("Provide exactly one of --publisher or --publisher-id.")
    with d365_errors(ctx):
        info = sol_mod.create_solution(
            ctx.backend(),
            name=name,
            friendly_name=display,
            version=version,
            publisher_unique_name=publisher,
            publisher_id=publisher_id,
            if_exists=if_exists,
        )
    ctx.emit(True, data=info)
    _journal(ctx, name, info)


@solution_group.command("set-version")
@click.argument("unique_name")
@click.option("--version", default=None, help="New 4-part dotted version, e.g. 2.0.0.0.")
@click.option("--friendly-name", "friendly_name", default=None, help="New friendly (display) name.")
@click.option("--description", default=None, help="New description.")
@pass_ctx
def solution_set_version(ctx: CLIContext, unique_name, version, friendly_name, description):
    """Update an unmanaged solution's version / friendly name / description in place."""
    with d365_errors(ctx):
        info = sol_mod.update_solution(
            ctx.backend(),
            unique_name,
            version=version,
            friendly_name=friendly_name,
            description=description,
        )
    ctx.emit(True, data=info)
    _journal(ctx, unique_name, info)


def _validate_component_selection(component_ids, type_, components_file):
    """Reject bad --id/--type/--components-file combinations (usage errors, exit 2).

    Fires before any backend call. Type-name/file-content errors are left to the
    d365_errors path (exit 1) to match the single-component command's behavior.
    """
    if not component_ids and not components_file:
        raise click.UsageError("provide --id (with --type) and/or --components-file.")
    if component_ids and not type_:
        raise click.UsageError("--type is required with --id.")
    if type_ and not component_ids:
        raise click.UsageError(
            "--type applies to --id; with --components-file the type is per row."
        )


def _emit_batch(ctx, solution, info, verb):
    """Emit a batch add/remove result: ok unless a row failed, journal on success."""
    failed = info.get("failed", 0)
    ok = failed == 0
    error = None
    if not ok:
        error = (
            f"{failed} of {info['count']} component(s) failed; "
            f"transaction rolled back (no components {verb})."
        )
    ctx.emit(ok, data=info, error=error)
    if ok:
        _journal(ctx, solution, info)


def _collect_add_components(
    component_ids, type_, components_file, no_add_required, no_subcomponents
):
    """Build the resolved component list for a batch add (file rows + --id rows).

    The command-level ``--no-add-required`` / ``--no-subcomponents`` flags are the
    batch-wide default; a --components-file row can override them per row.
    """
    components: list[dict] = []
    if components_file:
        components.extend(
            sol_mod.parse_components_file(
                components_file,
                for_add=True,
                default_no_add_required=no_add_required,
                default_no_subcomponents=no_subcomponents,
            )
        )
    if component_ids:
        component_type = sol_mod.resolve_component_type(type_)
        components.extend(
            {
                "component_id": cid,
                "component_type": component_type,
                "add_required_components": not no_add_required,
                "do_not_include_subcomponents": no_subcomponents,
            }
            for cid in component_ids
        )
    return components


_CASCADE_PREVIEW_LIMIT = 10


def _cascade_gate(ctx, cascading, yes):
    """Interactive-only pre-flight for an AddRequiredComponents cascade (#916).

    ``cascading`` is ``[(component_id, component_type), ...]`` that will cascade
    (AddRequiredComponents on). When those pull in required components, list them
    and require confirmation. Fires ONLY in an interactive TTY — under ``--json``,
    a non-TTY, or ``--dry-run`` it returns silently so scripted / ``--json`` callers
    keep the historical no-prompt behavior. ``--yes`` skips it. On decline emits the
    ``aborted by user`` envelope (Exit 1), so control never returns to the caller.
    """
    if yes or ctx.json_mode or ctx.dry_run or not _stdin_is_tty() or not cascading:
        return
    try:
        required = sol_mod.preview_required_components(ctx.backend(), cascading)
    except D365Error:
        # Preview is best-effort: a RetrieveRequiredComponents failure (transient
        # 5xx, permissions) must not block an add-component that would otherwise
        # succeed — fall through to the historical no-preview behavior.
        return
    if not required:
        return
    click.echo(f"This will also add {len(required)} required component(s):")
    for r in required[:_CASCADE_PREVIEW_LIMIT]:
        click.echo(f"  - {r['type_name']} {r['objectid']}")
    if len(required) > _CASCADE_PREVIEW_LIMIT:
        click.echo(f"  … and {len(required) - _CASCADE_PREVIEW_LIMIT} more")
    if not click.confirm("Proceed?", default=False):
        ctx.emit(False, error="aborted by user")


def _collect_remove_components(component_ids, type_, components_file):
    """Build the resolved component list for a batch remove (file rows + --id rows)."""
    components: list[dict] = []
    if components_file:
        components.extend(sol_mod.parse_components_file(components_file, for_add=False))
    if component_ids:
        component_type = sol_mod.resolve_component_type(type_)
        components.extend(
            {"component_id": cid, "component_type": component_type} for cid in component_ids
        )
    return components


@solution_group.command("add-component")
@click.option("--solution", required=True, help="Target unmanaged solution unique name.")
@click.option(
    "--type",
    "type_",
    default=None,
    help="Component type: integer or friendly name (e.g. 61 or webresource). "
    "Required with --id; ignored with --components-file (type is per row).",
)
@click.option(
    "--id",
    "component_ids",
    multiple=True,
    metavar="GUID",
    help="Component GUID (objectid) to add. Repeat to batch multiple (all share --type).",
)
@click.option(
    "--components-file",
    "components_file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help='JSON list of {"type", "id"[, "no_add_required", "no_subcomponents"]} rows to batch.',
)
@click.option(
    "--no-add-required",
    is_flag=True,
    help="Do not also add required components (AddRequiredComponents: false). "
    "Batch default; a --components-file row can override it.",
)
@click.option(
    "--no-subcomponents",
    is_flag=True,
    help="Exclude subcomponents (DoNotIncludeSubcomponents: true). Only valid for "
    "entity components: as a batch default it applies to entity rows only, and "
    "requesting it for a non-entity component (per-row or via --id/--type) is "
    "rejected client-side. A --components-file row can override the default.",
)
@_destructive_option
@pass_ctx
def solution_add_component(
    ctx: CLIContext,
    solution,
    type_,
    component_ids,
    components_file,
    no_add_required,
    no_subcomponents,
    yes,
):
    """Add one or more existing components to an unmanaged solution (AddSolutionComponent).

    A single --id behaves exactly as before. Repeated --id (sharing --type) and/or
    a --components-file run as one transactional $batch — a mid-batch failure rolls
    all rows back — with a per-row ok/error summary under --json.

    When a cascade would pull in required components (AddRequiredComponents, i.e.
    --no-add-required absent) an interactive run lists them and asks to confirm
    (--yes skips). Under --json / a non-TTY the prompt is skipped (cascade proceeds).
    """
    _validate_component_selection(component_ids, type_, components_file)
    single = components_file is None and len(component_ids) == 1
    with d365_errors(ctx):
        if single:
            component_type = sol_mod.resolve_component_type(type_)
            _cascade_gate(ctx, [] if no_add_required else [(component_ids[0], component_type)], yes)
            info = sol_mod.add_solution_component(
                ctx.backend(),
                solution=solution,
                component_id=component_ids[0],
                component_type=component_type,
                add_required_components=not no_add_required,
                do_not_include_subcomponents=no_subcomponents,
            )
            meta = None
            if component_type == 1 and not no_add_required:  # entity + AddRequiredComponents
                meta = {
                    "note": (
                        "AddRequiredComponents was enabled: the server may have "
                        "silently added required components beyond the requested "
                        "entity; the response does not report them."
                    )
                }
            ctx.emit(True, data=info, meta=meta)
            _journal(ctx, solution, info)
            return
        components = _collect_add_components(
            component_ids, type_, components_file, no_add_required, no_subcomponents
        )
        cascading = [
            (c["component_id"], c["component_type"])
            for c in components
            if c.get("add_required_components")
        ]
        _cascade_gate(ctx, cascading, yes)
        info = sol_mod.add_solution_components(
            ctx.backend(), solution=solution, components=components
        )
    _emit_batch(ctx, solution, info, "added")


@solution_group.command("remove-component")
@click.option("--solution", required=True, help="Target unmanaged solution unique name.")
@click.option(
    "--type",
    "type_",
    default=None,
    help="Component type: integer or friendly name (e.g. 61 or webresource). "
    "Required with --id; ignored with --components-file (type is per row).",
)
@click.option(
    "--id",
    "component_ids",
    multiple=True,
    metavar="GUID",
    help="Component GUID (objectid) to remove. Repeat to batch multiple (all share --type).",
)
@click.option(
    "--components-file",
    "components_file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help='JSON list of {"type", "id"} rows to batch.',
)
@_destructive_option
@pass_ctx
def solution_remove_component(
    ctx: CLIContext, solution, type_, component_ids, components_file, yes
):
    """Remove one or more components from an unmanaged solution (RemoveSolutionComponent).

    A single --id behaves exactly as before. Repeated --id (sharing --type) and/or
    a --components-file run as one transactional $batch — a mid-batch failure rolls
    all rows back — with a per-row ok/error summary under --json.
    """
    _validate_component_selection(component_ids, type_, components_file)
    single = components_file is None and len(component_ids) == 1
    if single:
        _confirm_destructive(
            ctx,
            "component",
            f"{component_ids[0]} from solution {solution!r}",
            yes,
            message=(
                f"Removing component {component_ids[0]} from solution {solution!r}. Continue?"
            ),
        )
        with d365_errors(ctx):
            component_type = sol_mod.resolve_component_type(type_)
            info = sol_mod.remove_solution_component(
                ctx.backend(),
                solution=solution,
                component_id=component_ids[0],
                component_type=component_type,
            )
        ctx.emit(True, data=info)
        _journal(ctx, solution, info)
        return
    with d365_errors(ctx):
        components = _collect_remove_components(component_ids, type_, components_file)
        n = len(components)
        _confirm_destructive(
            ctx,
            "components",
            f"{n} component(s) from solution {solution!r}",
            yes,
            message=(f"Removing {n} component(s) from solution {solution!r}. Continue?"),
        )
        info = sol_mod.remove_solution_components(
            ctx.backend(), solution=solution, components=components
        )
    _emit_batch(ctx, solution, info, "removed")


@solution_group.command("clone-as-patch")
@click.option(
    "--solution",
    "parent_solution",
    required=True,
    help="Parent solution unique name to clone a patch from.",
)
@click.option(
    "--display",
    "display",
    default=None,
    help="Patch display name (defaults to the parent's friendly name).",
)
@click.option(
    "--version",
    default=None,
    help="Patch version (4-part dotted). Must share the parent's "
    "major.minor; defaults to the parent version with the "
    "revision bumped.",
)
@pass_ctx
def solution_clone_as_patch(ctx: CLIContext, parent_solution, display, version):
    """Create a solution patch from a parent solution (CloneAsPatch)."""
    with d365_errors(ctx):
        info = sol_mod.clone_as_patch(
            ctx.backend(),
            parent_solution=parent_solution,
            display_name=display,
            version=version,
        )
    ctx.emit(True, data=info)
    _journal(ctx, parent_solution, info)


@solution_group.command("uninstall")
@click.option(
    "--solution", "unique_name", required=True, help="Unique name of the solution to uninstall."
)
@click.option(
    "--force",
    is_flag=True,
    help="Uninstall even if dependency blockers exist (skip the pre-check).",
)
@_destructive_option
@pass_ctx
def solution_uninstall(ctx: CLIContext, unique_name, force, yes):
    """Uninstall (delete) a solution (DELETE /solutions).

    Pre-checks RetrieveDependenciesForUninstall and refuses with the blocker
    list unless --force. For a managed base solution the server also uninstalls
    its patches.
    """
    _confirm_destructive(
        ctx,
        "solution",
        unique_name,
        yes,
        message=(
            f"Uninstalling solution {unique_name!r} removes it (and, for a "
            f"managed base solution, all of its patches). Continue?"
        ),
    )
    with d365_errors(ctx):
        info = sol_mod.uninstall_solution(ctx.backend(), unique_name, force=force)
    ctx.emit(True, data=info)
    _journal(ctx, unique_name, info)


@solution_group.command("stage-and-upgrade")
@click.argument("zip_path", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--promote",
    is_flag=True,
    help="After staging, apply the upgrade with DeleteAndPromote "
    "(replaces the base solution). Requires --solution.",
)
@click.option(
    "--solution",
    "solution_name",
    default=None,
    help="Unique name of the staged solution to promote (required with --promote).",
)
@click.option("--no-publish", is_flag=True)
@click.option("--no-overwrite", is_flag=True)
@click.option(
    "--skip-dependency-check",
    "skip_dependency_check",
    is_flag=True,
    help="Set ImportSolution SkipProductUpdateDependencies to proceed "
    "past a product-update dependency block.",
)
@click.option(
    "--timeout",
    type=int,
    default=None,
    help="Async operation timeout in seconds. Overrides profile.async_timeout.",
)
@click.option(
    "--no-retry", is_flag=True, help="Disable the 429/5xx retry loop for this invocation."
)
@click.option(
    "--quiet", "-q", is_flag=True, help="Suppress per-tick import-progress lines on stderr."
)
@click.option(
    "--formatted",
    is_flag=True,
    help="Also fetch the Excel-format RetrieveFormattedImportJobResults "
    "report and attach it verbatim under formatted_results.",
)
@click.option("--yes", is_flag=True, help="Skip the staging/promote confirmation prompt.")
@pass_ctx
def solution_stage_and_upgrade_cmd(
    ctx: CLIContext,
    zip_path,
    promote,
    solution_name,
    no_publish,
    no_overwrite,
    skip_dependency_check,
    timeout,
    no_retry,
    quiet,
    formatted,
    yes,
):
    """Stage a managed-solution upgrade as a holding solution (ImportSolution HoldingSolution).

    Stages only by default; pass --promote (with --solution) to also apply the
    upgrade via DeleteAndPromote, replacing the base solution.
    """
    # --promote needs an explicit target (usage error, exit 2 — ADR 0001).
    if promote and not solution_name:
        raise click.UsageError("--promote requires --solution <unique name>.")

    action = (
        f"Staging {zip_path!r} as a holding solution and promoting it over "
        f"{solution_name!r} (DeleteAndPromote replaces the base solution)."
        if promote
        else f"Staging {zip_path!r} as a holding solution for upgrade."
    )
    _confirm_destructive(ctx, "solution", zip_path, yes, message=f"{action} Continue?")

    with _no_retry_scope(ctx, no_retry):
        with d365_errors(ctx):
            info = sol_mod.import_solution(
                ctx.backend(),
                zip_path,
                publish_workflows=not no_publish,
                overwrite_unmanaged_customizations=not no_overwrite,
                holding_solution=True,
                skip_dependency_check=skip_dependency_check,
                timeout=timeout,
                quiet=quiet,
                formatted=formatted,
            )
            # Promote only a real, succeeded stage — never under --dry-run.
            if promote and not info.get("_dry_run"):
                info["promote"] = sol_mod.delete_and_promote(ctx.backend(), solution_name)
        warnings = info.pop("warnings", None)
        ctx.emit(True, data=info, warnings=warnings)
        _journal(ctx, zip_path, info)


@solution_group.command("apply-upgrade")
@click.argument("unique_name")
@_destructive_option
@pass_ctx
def solution_apply_upgrade_cmd(ctx: CLIContext, unique_name, yes):
    """Apply a previously-staged holding-solution upgrade (DeleteAndPromote).

    Promotes a solution already staged via `stage-and-upgrade` (run without
    --promote), replacing the base solution and deleting its patches. This is
    the separate-promote path that decouples stage-time from promote-time;
    `stage-and-upgrade --promote` remains the one-shot path.
    """
    _confirm_destructive(
        ctx,
        "solution",
        unique_name,
        yes,
        message=(
            f"Promoting the staged upgrade for solution {unique_name!r} via "
            f"DeleteAndPromote (replaces the base solution and deletes its "
            f"patches). Continue?"
        ),
    )
    with d365_errors(ctx):
        info = sol_mod.delete_and_promote(ctx.backend(), unique_name)
    ctx.emit(True, data=info)
    _journal(ctx, unique_name, info)


def _solution_pick_label(s: dict) -> str:
    """One-line picker label for a solution: unique name + friendly name + version.

    A ``(managed)`` marker disambiguates managed solutions, since unmanaged and
    managed are interleaved by name in the org but shown unmanaged-first here.
    """
    name = s.get("uniquename", "")
    friendly = s.get("friendlyname") or ""
    version = s.get("version") or ""
    parts = [p for p in (name, friendly, f"v{version}" if version else "") if p]
    if s.get("ismanaged"):
        parts.append("(managed)")
    return "  ".join(parts)


def _pick_solution(ctx: CLIContext, title: str) -> str | None:
    """No-arg `solution export` → interactive picker over the org's solutions (#656).

    Network-backed pilot sibling of `profile use`'s local picker: fetches the
    org's solutions (same source as `solution list`) and shows an arrow-key
    select, returning the chosen unique name. Unmanaged solutions sort first —
    the common export target is your own unmanaged work.

    Gated to a real TTY in human mode: under ``--json`` or a non-TTY (scripts /
    CI) it raises ``UsageError`` (exit 2), preserving the pre-#656
    required-argument behavior. A failed fetch (auth/network) is left to the
    caller's ``d365_errors`` scope, which turns it into the normal
    operational-failure envelope rather than a picker crash. On an empty list or
    a user cancel it emits a clean error envelope via ``ctx.emit(False)`` (which
    always raises ``Exit``), so it never actually returns ``None`` — the
    ``str | None`` return and the caller's guard are defensive.
    """
    if not (_stdin_is_tty() and not ctx.json_mode):
        raise click.UsageError(
            "a solution unique name is required here — the interactive picker "
            "needs a human terminal and is disabled under --json."
        )
    items = sol_mod.list_solutions(ctx.backend())
    items.sort(key=lambda s: (bool(s.get("ismanaged")), s.get("uniquename", "")))
    if not items:
        ctx.emit(False, error="No solutions found in this org.")
        return None
    choices = [(s["uniquename"], _solution_pick_label(s)) for s in items]
    name = select_one(title, choices)
    if not name:
        ctx.emit(False, error="no solution selected")
        return None
    return name


@solution_group.command("export")
@click.argument("unique_name", required=False)
@_output_option(required=True)
@click.option("--managed", is_flag=True)
@click.option(
    "--export-setting",
    "export_settings",
    multiple=True,
    type=click.Choice(sorted(_EXPORT_SETTING_KEYS.keys())),
    help="Repeatable; include a named export setting in the solution payload.",
)
@click.option(
    "--timeout",
    type=int,
    default=None,
    help="Async operation timeout in seconds. Overrides profile.async_timeout.",
)
@click.option(
    "--no-retry", is_flag=True, help="Disable the 429/5xx retry loop for this invocation."
)
@pass_ctx
def solution_export_cmd(
    ctx: CLIContext, unique_name, output, managed, export_settings, timeout, no_retry
):
    """Export a solution to a zip.

    With no UNIQUE_NAME on an interactive terminal, lists the org's solutions
    (unmanaged first) and prompts you to pick one. Under --json or with no TTY
    the name is required (a missing one is a usage error, exit 2).
    """
    kwargs = {_EXPORT_SETTING_KEYS[name]: True for name in export_settings}
    with _no_retry_scope(ctx, no_retry):
        with d365_errors(ctx):
            if not unique_name:
                # No solution named: on a TTY (human mode) pick one interactively;
                # otherwise this raises a UsageError (exit 2). The fetch inside is
                # covered by this d365_errors scope.
                unique_name = _pick_solution(ctx, "Select a solution to export")
                if unique_name is None:
                    return
            info = sol_mod.export_solution(
                ctx.backend(),
                unique_name,
                output,
                managed=managed,
                timeout=timeout,
                **kwargs,
            )
        ctx.emit(True, data=info)
    ctx.hint("solution_export")


@solution_group.command("publish-all")
@pass_ctx
def solution_publish_all(ctx: CLIContext):
    """Call PublishAllXml — publish every unpublished customization."""
    with d365_errors(ctx):
        result = sol_mod.publish_all(ctx.backend())
    data = result or {"published": True}
    ctx.emit(True, data=data)
    _journal(ctx, None, data)


@solution_group.command("publish")
@click.option("--xml", "parameter_xml", help="Inline Publish Request Schema XML.")
@click.option(
    "--xml-file",
    type=click.Path(exists=True, dir_okay=False),
    help="Path to a Publish Request Schema XML file.",
)
@pass_ctx
def solution_publish(ctx: CLIContext, parameter_xml, xml_file):
    """Call PublishXml with a Publish Request Schema XML payload."""
    if parameter_xml and xml_file:
        raise click.UsageError("Provide --xml or --xml-file, not both.")
    if xml_file:
        # click.Path(exists=True) validated the file at parse, but a permission
        # edge or a delete-after-check race can still fail the read — surface it
        # as the clean envelope (exit 1), matching this command's own errors.
        try:
            parameter_xml = Path(xml_file).read_text(encoding="utf-8")
        except OSError as exc:
            ctx.emit(False, error=f"Could not read {xml_file!r}: {exc}")
            return
        # An empty file is a content problem, not a bad argument — the flag *was*
        # provided — so keep the clean envelope (exit 1), like the read error above.
        if not parameter_xml:
            ctx.emit(False, error=f"{xml_file!r} is empty.")
            return
    if not parameter_xml:
        raise click.UsageError("Either --xml or --xml-file is required.")
    with d365_errors(ctx):
        result = sol_mod.publish_xml(ctx.backend(), parameter_xml)
    data = result or {"published": True}
    ctx.emit(True, data=data)
    _journal(ctx, None, data)


@solution_group.command("job-status")
@click.argument("async_operation_id")
@pass_ctx
def solution_job_status(ctx: CLIContext, async_operation_id):
    """Alias for `crm async get <id>` — inspect a solution import/export job."""
    with d365_errors(ctx):
        row = async_ops_mod.get_async_operation(ctx.backend(), async_operation_id)
    ctx.emit(True, data=row)


@solution_group.command("job-cancel")
@click.argument("async_operation_id")
@_destructive_option
@pass_ctx
def solution_job_cancel(ctx: CLIContext, async_operation_id, yes):
    """Alias for `crm async cancel <id>`."""
    _confirm_destructive(ctx, "async job", async_operation_id, yes)
    with d365_errors(ctx):
        async_ops_mod.cancel_async_operation(ctx.backend(), async_operation_id)
    data = {"cancelled": True, "id": async_operation_id}
    ctx.emit(True, data=data)
    _journal(ctx, async_operation_id, data)


@solution_group.command("import")
@click.argument("zip_path", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--publish/--no-publish",
    default=True,
    help="Set the import's PublishWorkflows server option (activate "
    "imported workflows). NOT PublishAllXml. Default: publish.",
)
@click.option(
    "--overwrite/--no-overwrite",
    default=True,
    help="Set ImportSolution OverwriteUnmanagedCustomizations. Default: "
    "overwrite (clobbers unmanaged customizations in the target org).",
)
@click.option(
    "--skip-dependency-check",
    "skip_dependency_check",
    is_flag=True,
    help="Set ImportSolution SkipProductUpdateDependencies to proceed "
    "past a product-update dependency block.",
)
@click.option(
    "--timeout",
    type=int,
    default=None,
    help="Async operation timeout in seconds. Overrides profile.async_timeout.",
)
@click.option(
    "--no-retry", is_flag=True, help="Disable the 429/5xx retry loop for this invocation."
)
@click.option(
    "--quiet", "-q", is_flag=True, help="Suppress per-tick import-progress lines on stderr."
)
@click.option(
    "--formatted",
    is_flag=True,
    help="Also fetch the Excel-format RetrieveFormattedImportJobResults "
    "report and attach it verbatim under formatted_results.",
)
@click.option("--yes", is_flag=True, help="Skip the overwrite confirmation prompt.")
@pass_ctx
def solution_import_cmd(
    ctx: CLIContext,
    zip_path,
    publish,
    overwrite,
    skip_dependency_check,
    timeout,
    no_retry,
    quiet,
    formatted,
    yes,
):
    # An overwrite import (the default) clobbers unmanaged customizations in the
    # target org — gate it like a delete (#67). A `--no-overwrite` import is not
    # prompted here (the PreToolUse hook still requires --yes for any import).
    if overwrite:
        _confirm_destructive(
            ctx,
            "solution",
            zip_path,
            yes,
            message=(
                f"Importing {zip_path!r} will OVERWRITE unmanaged customizations "
                f"in the target org. Continue?"
            ),
        )
    with _no_retry_scope(ctx, no_retry):
        with d365_errors(ctx):
            info = sol_mod.import_solution(
                ctx.backend(),
                zip_path,
                publish_workflows=publish,
                overwrite_unmanaged_customizations=overwrite,
                skip_dependency_check=skip_dependency_check,
                timeout=timeout,
                quiet=quiet,
                formatted=formatted,
            )
        warnings = info.pop("warnings", None)
        ctx.emit(True, data=info, warnings=warnings)
        _journal(ctx, zip_path, info)


def _emit_packager_result(ctx: CLIContext, info: dict) -> None:
    """Emit a pac-solution envelope, failing the command (ADR 0001) when the tool
    returned a non-zero exit code — the data (exit_code, stdout_tail) is kept so
    the failure is diagnosable.
    """
    exit_code = info.get("exit_code")
    if exit_code:
        # Embed the tail in the error itself: human mode drops `data`, so a bare
        # "see stdout_tail" would point at output the user can't see (#107 review).
        tail = info.get("stdout_tail") or ""
        # The envelope `action` (Extract/Pack) is kept stable, but pac's real
        # subcommand differs (unpack/pack) — name it so a user can re-run the
        # failing command verbatim (Copilot #527).
        subcommand = sp_mod.pac_subcommand(info.get("action"))
        msg = f"pac solution {subcommand} failed (exit {exit_code})."
        if tail:
            msg += f"\n{tail}"
        ctx.emit(False, data=info, error=msg)
        return
    ctx.emit(True, data=info)


def _pac_options(f):
    """Shared pac packaging options for `solution extract` / `solution pack`.

    Applied in reverse so the resolved --help order matches the source order
    here (--package-type, --pac-path, --solutionpackager-path, --timeout).
    """
    options = [
        click.option(
            "--package-type",
            "package_type",
            type=click.Choice(["Unmanaged", "Managed", "Both"], case_sensitive=False),
            default="Unmanaged",
            help="pac --packagetype (default Unmanaged).",
        ),
        click.option(
            "--pac-path",
            "pac_path",
            default=None,
            type=click.Path(dir_okay=False),
            help="Path to the pac executable (else CRM_PAC env, then PATH).",
        ),
        click.option(
            "--solutionpackager-path",
            "solutionpackager_path",
            default=None,
            type=click.Path(dir_okay=False),
            hidden=True,
            help="Deprecated alias for --pac-path (point it at pac).",
        ),
        click.option(
            "--timeout", type=int, default=None, help="pac subprocess timeout in seconds."
        ),
    ]
    for opt in reversed(options):
        f = opt(f)
    return f


@solution_group.command("extract")
@click.option(
    "--zipfile",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Exported solution zip to unpack.",
)
@click.option(
    "--folder",
    required=True,
    type=click.Path(file_okay=False),
    help="Destination folder for the source-controllable tree.",
)
@_pac_options
@pass_ctx
def solution_extract_cmd(
    ctx: CLIContext, zipfile, folder, package_type, pac_path, solutionpackager_path, timeout
):
    """Extract a solution zip into a folder tree (offline; pac solution unpack).

    OFFLINE local-file transform — no connection or profile required.
    """
    with d365_errors(ctx):
        info = sp_mod.extract_solution(
            zipfile=zipfile,
            folder=folder,
            package_type=package_type,
            pac_path=pac_path or solutionpackager_path,
            timeout=timeout,
        )
    _emit_packager_result(ctx, info)


@solution_group.command("pack")
@click.option(
    "--zipfile",
    required=True,
    type=click.Path(dir_okay=False),
    help="Destination solution zip to build.",
)
@click.option(
    "--folder",
    required=True,
    type=click.Path(exists=True, file_okay=False),
    help="Source folder tree to pack.",
)
@_pac_options
@pass_ctx
def solution_pack_cmd(
    ctx: CLIContext, zipfile, folder, package_type, pac_path, solutionpackager_path, timeout
):
    """Pack a folder tree back into a solution zip (offline; pac solution pack).

    OFFLINE local-file transform — no connection or profile required.
    """
    with d365_errors(ctx):
        info = sp_mod.pack_solution(
            zipfile=zipfile,
            folder=folder,
            package_type=package_type,
            pac_path=pac_path or solutionpackager_path,
            timeout=timeout,
        )
    _emit_packager_result(ctx, info)


@solution_group.command("validate")
@click.argument("zip_path", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--against-org",
    "against_org",
    is_flag=True,
    help="Also run online checks against the connected org "
    "(form/view + BPF process-stage GUID collisions, web-resource "
    "& option-set existence). Requires a connection/profile.",
)
@pass_ctx
def solution_validate_cmd(ctx: CLIContext, zip_path, against_org):
    """Statically validate a solution zip before import.

    OFFLINE by default -- no connection or profile required. --against-org adds
    online checks (GUID collisions, web-resource & option-set existence). Exits
    non-zero when any error-severity problem is found.
    """
    with d365_errors(ctx):
        # Construct the backend inside the guard so a bad profile / credential
        # failure renders as the house envelope, not a traceback (#698). Stays
        # offline by default: no backend is built without --against-org.
        backend = ctx.backend() if against_org else None
        report = sv_mod.validate_solution(zip_path, backend=backend)
    if report["valid"]:
        ctx.emit(True, data=report)
        return
    n = sum(1 for f in report["findings"] if f["severity"] == "error")
    ctx.emit(False, data=report, error=f"{n} validation error(s) found")


@solution_group.command("import-result")
@click.argument("import_job_id")
@click.option(
    "--formatted",
    is_flag=True,
    help="Also fetch the Excel-format RetrieveFormattedImportJobResults "
    "report and attach it verbatim under formatted_results.",
)
@pass_ctx
def solution_import_result_cmd(ctx: CLIContext, import_job_id, formatted):
    """Re-fetch a prior ImportJob and parse its per-component pass/fail results."""
    with d365_errors(ctx):
        info = sol_mod.import_result(ctx.backend(), import_job_id, formatted=formatted)
    warnings = info.pop("warnings", None)
    ctx.emit(True, data=info, warnings=warnings)
