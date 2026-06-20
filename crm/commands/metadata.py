"""Metadata commands (entities, attributes, relationships, option sets)."""
# pyright: basic
from __future__ import annotations
import time
from pathlib import Path
from typing import cast
import click
from crm.core import metadata as meta_mod
from crm.core import metadata_attrs as ma_mod
from crm.core import metadata_cache as mc_mod
from crm.core import metadata_update as mu_mod
from crm.core import optionsets as os_mod
from crm.core import status_meta as sm_mod
from crm.core import mappings as mp_mod
from crm.core import relationships as rel_mod
from crm.core import dependencies as dep_mod
from crm.core import export_spec as export_spec_mod
from crm.core import clone as clone_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _publish_option,
    _destructive_option,
    _parse_value_labels,
    _handle_d365_error,
    d365_errors,
    _confirm_destructive,
    _journal,
    _output_option,
    _resolve_publish,
    _solution_option,
    _resolve_solution,
    _resolve_schema_name,
    _emit_with_warning,
    _parse_expect,
    _check_expectations,
    _emit_expectation_failure,
    _CASCADE,
    _MENU,
    _REQUIRED,
)


@click.group("metadata")
def metadata_group():
    """Browse entity / attribute / relationship metadata."""


@metadata_group.command("entities")
@click.option("--custom-only", is_flag=True)
@click.option("--managed-only", is_flag=True)
@click.option("--filter", "filter_expr")
@click.option("--top", type=int)
@pass_ctx
def metadata_entities(ctx: CLIContext, custom_only, managed_only, filter_expr, top):
    """List entity definitions."""
    use_cache = ctx.cache_metadata or ctx.refresh_metadata
    if use_cache:
        if custom_only or managed_only or filter_expr:
            raise click.UsageError(
                "Filters (--custom-only, --managed-only, --filter) are not supported "
                "with the metadata cache (--cache-metadata / --refresh-metadata); "
                "the cache stores only logical/set names"
            )
        with d365_errors(ctx):
            backend = ctx.backend()
            lookup = mc_mod.load_definitions(
                backend.profile,
                fetch=lambda: meta_mod.list_entity_definitions(backend),
                refresh=ctx.refresh_metadata,
                now=time.time(),
            )
        rows = lookup.definitions
        if top is not None:
            if top < 1:
                _handle_d365_error(ctx, D365Error("--top must be >= 1"))
                return
            rows = rows[:top]
        meta = {"cache": lookup.status, "count": len(rows)}
        if ctx.json_mode:
            ctx.emit(True, data=rows, meta=meta)
            return
        ctx.emit(True, table={"headers": ["LogicalName", "EntitySetName"],
                              "rows": [[r["logical"], r["set_name"]] for r in rows]},
                 meta=meta)
        return
    with d365_errors(ctx):
        items = meta_mod.list_entities(
            ctx.backend(),
            custom_only=custom_only,
            managed_only=managed_only,
            filter_expr=filter_expr,
            top=top
        )
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["LogicalName", "EntitySetName", "SchemaName", "IsCustom", "IsManaged"]
    rows = [
        [it.get("LogicalName", ""), it.get("EntitySetName", ""),
         it.get("SchemaName", ""), str(it.get("IsCustomEntity", False)), str(it.get("IsManaged", False))]
        for it in items
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta={"count": len(items)})


@metadata_group.command("cache-clear")
@pass_ctx
def metadata_cache_clear(ctx: CLIContext):
    """Delete the active profile's on-disk metadata cache."""
    with d365_errors(ctx):
        backend = ctx.backend()
    cleared = mc_mod.clear(backend.profile)
    ctx.emit(True, data={"cleared": cleared})


@metadata_group.command("entity")
@click.argument("logical_name")
@pass_ctx
def metadata_entity(ctx: CLIContext, logical_name):
    """Show full entity definition."""
    with d365_errors(ctx):
        info = meta_mod.entity_info(ctx.backend(), logical_name)
    ctx.emit(True, data=info)


@metadata_group.command("attributes")
@click.argument("logical_name")
@pass_ctx
def metadata_attributes(ctx: CLIContext, logical_name):
    """List attributes for an entity."""
    with d365_errors(ctx):
        items = meta_mod.list_attributes(ctx.backend(), logical_name)
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["LogicalName", "SchemaName", "AttributeType", "Create", "Update",
               "Read", "Required", "IsCustom"]
    rows = [
        [it.get("LogicalName", ""), it.get("SchemaName", ""),
         it.get("AttributeType", ""),
         str(it.get("IsValidForCreate", False)),
         str(it.get("IsValidForUpdate", False)),
         str(it.get("IsValidForRead", False)),
         it.get("RequiredLevel") or "",
         str(it.get("IsCustomAttribute", False))]
        for it in items
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta={"count": len(items)})


@metadata_group.command("keys")
@click.argument("logical_name")
@pass_ctx
def metadata_keys(ctx: CLIContext, logical_name):
    """List alternate keys defined on an entity."""
    with d365_errors(ctx):
        keys = meta_mod.list_entity_keys(ctx.backend(), logical_name)
    if ctx.json_mode:
        ctx.emit(True, data=keys, meta={"count": len(keys)})
        return
    if not keys:
        ctx.emit(True, data=None, meta={"count": 0})
        return
    headers = ["LogicalName", "SchemaName", "KeyAttributes", "Status"]
    rows = [
        [k["logical_name"], k["schema_name"],
         ", ".join(k["key_attributes"]), k["index_status"]]
        for k in keys
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta={"count": len(keys)})


@metadata_group.command("attribute")
@click.argument("logical_name")
@click.argument("attribute_name")
@click.option("--expect", multiple=True, metavar="ATTR=VALUE",
              help="Repeatable; assert str(record[ATTR]) == VALUE (an absent key "
                   "never matches). Any mismatch exits 1 (the --json envelope "
                   "carries meta {attr, expected, actual}; human mode prints the "
                   "error line); all match exits 0.")
@pass_ctx
def metadata_attribute(ctx: CLIContext, logical_name, attribute_name, expect):
    """Show a single attribute definition."""
    # Validate untrusted --expect input before any backend call (house rule).
    expectations = _parse_expect(expect)
    with d365_errors(ctx):
        info = meta_mod.attribute_info(ctx.backend(), logical_name, attribute_name)
    if expectations:
        miss = _check_expectations(info, expectations)
        if miss is not None:
            _emit_expectation_failure(ctx, miss)
            return
    ctx.emit(True, data=info)


@metadata_group.command("picklist")
@click.argument("logical_name")
@click.argument("attribute")
@click.option("--no-global", is_flag=True, help="Skip GlobalOptionSet expansion.")
@pass_ctx
def metadata_picklist(ctx: CLIContext, logical_name, attribute, no_global):
    """Retrieve option set values for a picklist / state / status attribute."""
    with d365_errors(ctx):
        info = meta_mod.picklist_options(
            ctx.backend(), logical_name, attribute,
            global_optionset=not no_global,
        )
    # Flatten once for both modes; local OptionSet wins, GlobalOptionSet is the
    # fallback for a global-bound picklist. Labels use the robust `label_text`
    # path (UserLocalizedLabel → LocalizedLabels), so JSON and table agree.
    flat = meta_mod.flatten_options(info.get("OptionSet") or {})
    if not flat:
        flat = meta_mod.flatten_options(info.get("GlobalOptionSet") or {})
    if ctx.json_mode:
        # Raw `data` left untouched (#76); `meta.options` is the convenience list.
        ctx.emit(True, data=info, meta={"options": flat})
        return
    headers = ["Value", "Label"]
    rows = [[str(o["value"]), o["label"]] for o in flat]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"entity": logical_name, "attribute": attribute, "count": len(flat)})


@metadata_group.command("describe")
@click.argument("logical_name")
@pass_ctx
def metadata_describe(ctx: CLIContext, logical_name):
    """One-shot write-readiness brief for an entity.

    Consolidates everything an agent needs to construct a valid create/update
    payload in one read-only call: the entity set name, primary id/name, and each
    writable attribute with its required level. Lookups carry `bind_key`
    (`<Nav>@odata.bind`) plus `targets[]` (logical + set_name); picklist / state /
    status attributes carry inline `{value, label}` options, and a picklist bound
    to a global option set also carries its `global_optionset_id` GUID. Read-only.
    """
    try:
        brief = meta_mod.describe_entity(ctx.backend(), logical_name)
    except D365Error as exc:
        extra: dict | None = None
        hint_text: str | None = None
        if exc.status == 404:
            suggestion = meta_mod.suggest_logical_name(ctx.backend(), logical_name)
            if suggestion:
                hint_text = (
                    f"`metadata describe` takes the logical name (singular), not the "
                    f"entity-set name. Did you mean `{suggestion['logical_name']}`?"
                    if suggestion["reason"] == "exact-set"
                    else f"Did you mean `{suggestion['logical_name']}`?"
                )
                extra = {"did_you_mean": suggestion["logical_name"]}
        _handle_d365_error(ctx, exc, extra_meta=extra,
                           hint=hint_text if extra else None)
        return
    ctx.emit(True, data=brief, meta={
        "writable_attributes": len(brief["writable_attributes"]),
    })


@metadata_group.command("export-spec")
@click.argument("logical_name")
@click.option("--with-views", is_flag=True, default=False,
              help="Include the entity's public views in the spec.")
@click.option("--with-relationships", is_flag=True, default=False,
              help="Include the entity's custom 1:N relationships in the spec.")
@_output_option(help="Write the bare spec as YAML to FILE (directly consumable by crm apply -f).")
@pass_ctx
def metadata_export_spec(ctx: CLIContext, logical_name, with_views, with_relationships, output):
    """Export a live entity as an apply-consumable desired-state spec.

    Reads the entity's metadata over the Web API (pure GETs) and emits a spec
    that round-trips through `crm apply -f`. Without -o, the spec is emitted
    under the standard JSON envelope (pipeable). With -o, the bare YAML spec is
    written to FILE so it is ready for `crm apply -f <file>`.
    """
    warnings: list[str] = []
    with d365_errors(ctx):
        spec = export_spec_mod.build_entity_spec(
            ctx.backend(), logical_name,
            with_views=with_views,
            with_relationships=with_relationships,
            warnings=warnings,
        )

    if output:
        import yaml
        try:
            with open(output, "w", encoding="utf-8") as fh:
                yaml.safe_dump(spec, fh, sort_keys=False, allow_unicode=True)
        except OSError as exc:
            ctx.emit(False, error=f"Could not write {output!r}: {exc}")
            return
        entity = spec["entities"][0]
        ctx.emit(True, data={
            "path": output,
            "entities": 1,
            "attributes": len(entity.get("attributes", [])),
            "relationships": len(entity.get("relationships", [])),
            "views": len(entity.get("views", [])),
            "optionsets": len(spec.get("optionsets", [])),
        }, warnings=warnings or None)
        return

    ctx.emit(True, data=spec, warnings=warnings or None)


@metadata_group.command("dependencies")
@click.argument("target")
@click.option(
    "--kind",
    type=click.Choice(["entity", "attribute", "optionset", "relationship"]),
    default="entity",
    show_default=True,
    help="Component kind of <target>. attribute uses dotted 'entity.attribute'.",
)
@click.option(
    "--for", "for_",
    type=click.Choice(["delete", "dependents"]),
    default="delete",
    show_default=True,
    help=(
        "delete = blockers preventing delete (RetrieveDependenciesForDelete); "
        "dependents = components that depend on it (RetrieveDependentComponents)."
    ),
)
@pass_ctx
def metadata_dependencies(ctx: CLIContext, target, kind, for_):
    """Show solution-component dependencies for a metadata target.

    Read-only. --for delete returns components that block deletion (can_delete +
    blockers[]); --for dependents returns components that depend on the target.
    """
    with d365_errors(ctx):
        info = dep_mod.retrieve_dependencies(ctx.backend(), kind, target, for_=for_)
    meta = {"can_delete": info["can_delete"], "blockers": len(info["blockers"])}
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
        ctx.emit(True, data={"can_delete": info["can_delete"]}, meta=meta)


@metadata_group.command("create-entity")
@click.option("--schema-name", default=None,
              help="PascalCase with publisher prefix, e.g. 'new_Project'. "
                   "Defaults to <publisher_prefix>_<Display> from the profile.")
@click.option("--display", "display_name", required=True,
              help="Singular UI label, e.g. 'Project'.")
@click.option("--display-collection", default=None,
              help="Plural UI label. Defaults to <display>+'s'.")
@click.option("--primary-attr", "primary_attr_schema", default=None,
              help="Schema name of the primary name attribute (default '<prefix>_Name').")
@click.option("--primary-label", "primary_attr_label", default=None,
              help="UI label for primary attribute. Default 'Name'.")
@click.option("--primary-max-length", type=int, default=200,
              help="Max length for primary name string column. Default 200.")
@click.option("--description", default=None)
@click.option("--ownership", type=click.Choice(["UserOwned", "OrganizationOwned"]),
              default="UserOwned")
@click.option("--has-activities", is_flag=True)
@click.option("--has-notes", is_flag=True)
@click.option("--is-activity", is_flag=True,
              help="Create as an activity entity.")
@_solution_option
@click.option("--if-exists", type=click.Choice(["error", "skip"]), default="error",
              help="If the entity already exists: error (default) or skip (no-op success).")
@_publish_option
@pass_ctx
def metadata_create_entity(
    ctx: CLIContext, schema_name, display_name, display_collection, primary_attr_schema,
    primary_attr_label, primary_max_length, description, ownership,
    has_activities, has_notes, is_activity, solution, require_solution, if_exists, publish,
):
    """Create a new custom entity (table)."""
    schema_name = _resolve_schema_name(ctx, schema_name, display_name, "--schema-name")
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = meta_mod.create_entity(
            ctx.backend(),
            schema_name=schema_name,
            display_name=display_name,
            display_collection_name=display_collection,
            primary_attr_schema=primary_attr_schema,
            primary_attr_label=primary_attr_label,
            primary_attr_max_length=primary_max_length,
            description=description,
            ownership=ownership,
            has_activities=has_activities,
            has_notes=has_notes,
            is_activity=is_activity,
            solution=solution,
            if_exists=if_exists,
        )
        if publish and not info.get("_dry_run") and not info.get("skipped"):
            from crm.core import solution as sol_mod
            sol_mod.publish_all(ctx.backend())
            info["published"] = True
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, schema_name, info, solution=solution)


@metadata_group.command("clone-entity")
@click.argument("source")
@click.argument("new_schema_name")
@click.option("--display", "display", default=None,
              help="Display name for the clone. Default: '<source display> (Clone)'.")
@click.option("--with-forms", is_flag=True, default=False,
              help="Clone the source's main forms onto the clone.")
@click.option("--with-views", is_flag=True, default=False,
              help="Clone the source's public views onto the clone.")
@click.option("--with-workflows", is_flag=True, default=False,
              help="Clone the source's classic workflows / business rules onto the clone.")
@click.option("--with-charts", is_flag=True, default=False,
              help="Clone the source's public system charts onto the clone.")
@click.option("--with-all", is_flag=True, default=False,
              help="Enable --with-forms, --with-views, --with-workflows, and --with-charts.")
@_solution_option
@_publish_option
@pass_ctx
def metadata_clone_entity(
    ctx: CLIContext, source, new_schema_name, display,
    with_forms, with_views, with_workflows, with_charts, with_all,
    solution, require_solution, publish,
):
    """Duplicate a custom entity (skeleton + opt-in forms/views/workflows/charts).

    Pure Web API -- no XML. The ribbon is not cloned (no API write path; the
    result carries a ribbon_note saying so). N:N relationships and the source's
    parent-side relationships are not cloned.
    """
    if with_all:
        with_forms = with_views = with_workflows = with_charts = True
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = clone_mod.clone_entity(
            ctx.backend(), source, new_schema_name,
            display=display,
            with_forms=with_forms, with_views=with_views,
            with_workflows=with_workflows, with_charts=with_charts,
            solution=solution, publish=publish,
        )
    notes = [warning] if warning else []
    if info.get("views_note"):
        notes.append(info["views_note"])
    skipped = info.get("skipped_workflows") or []
    if skipped:
        names = ", ".join(w["name"] for w in skipped)
        notes.append(f"{len(skipped)} workflow(s) not cloned: {names}")
    _emit_with_warning(ctx, info, "; ".join(notes) or None)
    _journal(ctx, new_schema_name, info, solution=solution)


@metadata_group.command("update-entity")
@click.argument("logical_name")
@click.option("--display", "display_name", default=None, help="New singular UI label.")
@click.option("--display-collection", "display_collection_name", default=None,
              help="New plural UI label.")
@click.option("--description", default=None, help="New entity description.")
@click.option("--ownership", type=click.Choice(["UserOwned", "OrganizationOwned"]),
              default=None,
              help="Note: Dataverse rejects ownership changes post-create.")
@click.option("--has-activities/--no-has-activities", "has_activities", default=None,
              help="Enable/disable activities.")
@click.option("--has-notes/--no-has-notes", "has_notes", default=None,
              help="Enable/disable notes.")
@click.option("--solution", default=None,
              help="Apply via MSCRM.SolutionUniqueName.")
@_publish_option
@pass_ctx
def metadata_update_entity(
    ctx: CLIContext, logical_name, display_name, display_collection_name,
    description, ownership, has_activities, has_notes, solution, publish,
):
    """Update an entity (table) definition (retrieve-merge-write)."""
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = mu_mod.update_entity(
            ctx.backend(),
            logical_name,
            display_name=display_name,
            display_collection_name=display_collection_name,
            description=description,
            ownership=ownership,
            has_activities=has_activities,
            has_notes=has_notes,
            publish=publish,
            solution=solution,
        )
    ctx.emit(True, data=info, meta=ctx.staged_meta())
    _journal(ctx, logical_name, info, solution=solution)


@metadata_group.command("update-attribute")
@click.argument("entity")
@click.argument("attribute")
@click.option("--display", "display_name", default=None, help="New UI label.")
@click.option("--description", default=None)
@click.option("--required", "required",
              type=click.Choice(["None", "Recommended", "ApplicationRequired"]),
              default=None)
@click.option("--max-length", type=int, default=None, help="String/memo: max characters.")
@click.option("--precision", type=int, default=None,
              help="Decimal/double/money: precision (decimals).")
@click.option("--min", "min_value", type=float, default=None, help="Numeric: minimum value.")
@click.option("--max", "max_value", type=float, default=None, help="Numeric: maximum value.")
@click.option("--format", "format_name", default=None,
              help="String: Text|Email|Url|Phone|TextArea. Datetime: DateOnly|DateAndTime.")
@click.option("--solution", default=None,
              help="Apply via MSCRM.SolutionUniqueName.")
@_publish_option
@pass_ctx
def metadata_update_attribute(
    ctx: CLIContext, entity, attribute, display_name, description, required,
    max_length, precision, min_value, max_value, format_name, solution, publish,
):
    """Update an attribute (column) definition (retrieve-merge-write).

    Option-set option edits are NOT handled here — use `update-optionset`.
    """
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = mu_mod.update_attribute(
            ctx.backend(),
            entity,
            attribute,
            display_name=display_name,
            description=description,
            required=required,
            max_length=max_length,
            precision=precision,
            min_value=min_value,
            max_value=max_value,
            format_name=format_name,
            publish=publish,
            solution=solution,
        )
    ctx.emit(True, data=info, meta=ctx.staged_meta())
    _journal(ctx, f"{entity}.{attribute}", info, solution=solution)


@metadata_group.command("update-relationship")
@click.argument("schema_name")
@click.option("--cascade-assign", type=_CASCADE, default=None)
@click.option("--cascade-delete", type=_CASCADE, default=None)
@click.option("--cascade-reparent", type=_CASCADE, default=None)
@click.option("--cascade-share", type=_CASCADE, default=None)
@click.option("--cascade-unshare", type=_CASCADE, default=None)
@click.option("--cascade-merge", type=_CASCADE, default=None)
@click.option("--menu-behavior", type=_MENU, default=None)
@click.option("--menu-label", default=None)
@click.option("--menu-order", type=int, default=None)
@click.option("--solution", default=None,
              help="Apply via MSCRM.SolutionUniqueName.")
@_publish_option
@pass_ctx
def metadata_update_relationship(
    ctx: CLIContext, schema_name, cascade_assign, cascade_delete, cascade_reparent,
    cascade_share, cascade_unshare, cascade_merge, menu_behavior, menu_label,
    menu_order, solution, publish,
):
    """Update a relationship definition (retrieve-merge-write)."""
    publish = _resolve_publish(ctx, publish)
    cascade: dict[str, str] = {}
    for member, value in (
        ("Assign", cascade_assign), ("Delete", cascade_delete),
        ("Reparent", cascade_reparent), ("Share", cascade_share),
        ("Unshare", cascade_unshare), ("Merge", cascade_merge),
    ):
        if value is not None:
            cascade[member] = value
    with d365_errors(ctx):
        info = mu_mod.update_relationship(
            ctx.backend(),
            schema_name,
            cascade=cascade or None,
            menu_behavior=menu_behavior,
            menu_label=menu_label,
            menu_order=menu_order,
            publish=publish,
            solution=solution,
        )
    ctx.emit(True, data=info, meta=ctx.staged_meta())
    _journal(ctx, schema_name, info, solution=solution)


@metadata_group.command("relationships")
@click.argument("logical_name")
@pass_ctx
def metadata_relationships(ctx: CLIContext, logical_name):
    """Show one-to-many, many-to-one, and many-to-many relationships."""
    with d365_errors(ctx):
        info = rel_mod.list_relationships(ctx.backend(), logical_name)
    if ctx.json_mode:
        ctx.emit(True, data=info, meta={
            "one_to_many": len(info.get("OneToMany", [])),
            "many_to_one": len(info.get("ManyToOne", [])),
            "many_to_many": len(info.get("ManyToMany", [])),
        })
        return
    # Human mode: one labeled table per category (emit renders only a single
    # table, so drive the skin directly for the three groups).
    rel_cols = ["SchemaName", "ReferencedEntity", "ReferencingEntity",
                "ReferencingAttribute"]
    groups = [
        ("OneToMany", rel_cols),
        ("ManyToOne", rel_cols),
        ("ManyToMany", ["SchemaName", "Entity1LogicalName", "Entity2LogicalName",
                        "IntersectEntityName"]),
    ]
    for title, headers in groups:
        ctx.skin.section(title)
        rows = info.get(title, [])
        if not rows:
            ctx.skin.info("none")
            continue
        ctx.skin.table(headers, [[r.get(h, "") for h in headers] for r in rows])


@metadata_group.command("delete-entity")
@click.argument("logical_name")
@_destructive_option
@_solution_option
@click.option("--check-dependencies", "check_dependencies", is_flag=True, default=False,
              help="Preview blocking dependencies (RetrieveDependenciesForDelete) in the result; pairs with --dry-run.")
@pass_ctx
def metadata_delete_entity(ctx: CLIContext, logical_name, yes, solution, require_solution, check_dependencies):
    """Permanently delete a custom entity (table) and ALL its rows."""
    _confirm_destructive(ctx, "entity", logical_name, yes)
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    with d365_errors(ctx):
        info = meta_mod.delete_entity(
            ctx.backend(), logical_name, solution=solution,
            check_dependencies=check_dependencies,
        )
    _emit_with_warning(ctx, info, warning)
    _journal(ctx, logical_name, info, solution=solution)


@metadata_group.command("add-attribute")
@click.argument("entity")
@click.option("--kind", required=True,
              type=click.Choice([
                  "string", "memo", "integer", "bigint", "decimal", "double",
                  "money", "boolean", "datetime", "picklist", "multiselect",
                  "lookup", "customer", "image", "file",
              ]),
              help="Attribute kind.")
@click.option("--schema-name", default=None,
              help="PascalCase with publisher prefix, e.g. 'new_Amount'. "
                   "Defaults to <publisher_prefix>_<Display> from the profile.")
@click.option("--display", "display_name", required=True,
              help="UI label.")
@click.option("--description", default=None)
@click.option("--required", "required",
              type=click.Choice(["None", "Recommended", "ApplicationRequired"]),
              default="None")
@click.option("--max-length", type=int, default=None,
              help="String/memo: max characters (default 100/2000).")
@click.option("--format", "format_name", default=None,
              help="String: Text|Email|Url|Phone|TextArea. Datetime: DateOnly|DateAndTime.")
@click.option("--auto-number-format", default=None,
              help="String: Auto-number format pattern (e.g. 'INV-{SEQNUM:5}').")
@click.option("--behavior", "behavior_name",
              type=click.Choice(["UserLocal", "DateOnly", "TimeZoneIndependent"]),
              default=None,
              help="Datetime: DateTimeBehavior. Omit for the server default (UserLocal).")
@click.option("--min", "min_value", type=float, default=None,
              help="Numeric kinds: minimum value.")
@click.option("--max", "max_value", type=float, default=None,
              help="Numeric kinds: maximum value.")
@click.option("--precision", type=int, default=None,
              help="Decimal/double/money: precision (decimals).")
@click.option("--true-label", default="Yes", help="Boolean: label for true.")
@click.option("--false-label", default="No", help="Boolean: label for false.")
@click.option("--default-value", default=None,
              help="Boolean: 'true'/'false'. Picklist: int option value.")
@click.option("--optionset-name", default=None,
              help="Picklist/multiselect: reference an existing global option set.")
@click.option("--option", "options", multiple=True,
              help="Picklist/multiselect: inline option as 'value:label' or ':label' (auto value). Repeatable.")
@click.option("--target-entity", default=None,
              help="Lookup: referenced entity logical name.")
@click.option("--relationship-schema", default=None,
              help="Lookup: override auto-generated relationship name.")
@click.option("--max-size-kb", type=int, default=None,
              help="File: max attachment size in KB. Default 32768.")
@click.option("--type", "source_type",
              type=click.Choice(["simple", "rollup", "calculated"]),
              default="simple",
              help="Column source: simple (default), or rollup/calculated — the "
                   "latter two turn the --kind column into a rollup/calculated "
                   "field and require --formula-file.")
@click.option("--formula-file", "formula_file",
              type=click.Path(exists=True, dir_okay=False),
              default=None,
              help="Rollup/calculated: path to the formula XAML file. Sent "
                   "verbatim — the formula body is officially editor-authored, "
                   "so hand-written XAML is unsupported (not validated here).")
@_solution_option
@click.option("--if-exists", type=click.Choice(["error", "skip"]), default="error",
              help="If the attribute already exists: error (default) or skip (no-op success).")
@_publish_option
@pass_ctx
def metadata_add_attribute(
    ctx: CLIContext, entity, kind, schema_name, display_name, description, required,
    max_length, format_name, auto_number_format, behavior_name, min_value, max_value, precision,
    true_label, false_label, default_value,
    optionset_name, options, target_entity, relationship_schema,
    max_size_kb, source_type, formula_file, solution, require_solution, if_exists, publish,
):
    """Add an attribute (column) to an existing entity."""
    schema_name = _resolve_schema_name(ctx, schema_name, display_name, "--schema-name")
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    parsed_options = _parse_value_labels(options, flag="--option") or None

    formula_definition: str | None = None
    if source_type == "simple":
        if formula_file is not None:
            raise click.UsageError(
                "--formula-file is only valid with --type rollup or calculated."
            )
    else:
        if formula_file is None:
            raise click.UsageError(f"--formula-file is required for --type {source_type}.")
        if kind in ("lookup", "customer"):
            raise click.UsageError(f"--type {source_type} is not valid for --kind {kind}.")
        # click.Path(exists=True) validates at parse time, but a permission edge,
        # a delete-after-check race, or a bad encoding can still fail the read —
        # surface it as a clean usage error rather than an uncaught traceback.
        try:
            formula_definition = Path(formula_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise click.UsageError(f"cannot read --formula-file {formula_file}: {exc}") from exc

    parsed_default: bool | int | None = None
    if default_value is not None:
        if kind == "boolean":
            lv = default_value.lower()
            if lv in ("1", "true", "yes", "on", "t", "y"):
                parsed_default = True
            elif lv in ("0", "false", "no", "off", "f", "n"):
                parsed_default = False
            else:
                raise click.UsageError(
                    f"--default-value for kind 'boolean' must be one of "
                    f"true/false/1/0/yes/no/on/off, got: {default_value!r}"
                )
        else:
            try:
                parsed_default = int(default_value)
            except ValueError as exc:
                raise click.UsageError(
                    f"--default-value must be int for kind {kind!r}: {default_value!r}"
                ) from exc

    with d365_errors(ctx):
        info = ma_mod.add_attribute(
            ctx.backend(),
            entity=entity,
            kind=kind,
            schema_name=schema_name,
            display_name=display_name,
            description=description,
            required=required,
            max_length=max_length,
            format_name=format_name,
            auto_number_format=auto_number_format,
            behavior_name=behavior_name,
            min_value=min_value,
            max_value=max_value,
            precision=precision,
            default_value=parsed_default,
            true_label=true_label,
            false_label=false_label,
            optionset_name=optionset_name,
            options=parsed_options,
            target_entity=target_entity,
            relationship_schema=relationship_schema,
            max_size_kb=max_size_kb,
            source_type=source_type,
            formula_definition=formula_definition,
            publish=publish,
            solution=solution,
            if_exists=if_exists,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, f"{entity}.{schema_name}", info, solution=solution)


@metadata_group.command("delete-attribute")
@click.argument("entity")
@click.argument("attribute")
@_destructive_option
@_solution_option
@click.option("--check-dependencies", "check_dependencies", is_flag=True, default=False,
              help="Preview blocking dependencies (RetrieveDependenciesForDelete) in the result; pairs with --dry-run.")
@pass_ctx
def metadata_delete_attribute(ctx: CLIContext, entity, attribute, yes, solution, require_solution, check_dependencies):
    """Delete a custom attribute (column) from an entity."""
    _confirm_destructive(ctx, "attribute", f"{entity}.{attribute}", yes)
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    with d365_errors(ctx):
        info = ma_mod.delete_attribute(
            ctx.backend(), entity, attribute, solution=solution,
            check_dependencies=check_dependencies,
        )
    _emit_with_warning(ctx, info, warning)
    _journal(ctx, f"{entity}.{attribute}", info, solution=solution)


@metadata_group.command("create-key")
@click.argument("entity")
@click.option("--name", "schema_name", default=None,
              help="Alternate key schema name, PascalCase with publisher prefix, "
                   "e.g. 'new_Code'. Defaults to <prefix>_<display> from the profile.")
@click.option("--key-attributes", required=True,
              help="Comma-separated attribute logical names forming the key, "
                   "e.g. 'accountnumber' or 'firstname,emailaddress1'.")
@click.option("--display", "display_name", default=None,
              help="UI label. Defaults to the schema name.")
@_solution_option
@click.option("--if-exists", type=click.Choice(["error", "skip"]), default="error",
              help="If the key already exists: error (default) or skip (no-op success).")
@_publish_option
@pass_ctx
def metadata_create_key(ctx: CLIContext, entity, schema_name, key_attributes,
                        display_name, solution, require_solution, if_exists, publish):
    """Create an alternate key on an entity."""
    schema_name = _resolve_schema_name(ctx, schema_name, display_name, "--name")
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    attrs = [a.strip() for a in key_attributes.split(",") if a.strip()]
    if not attrs:
        raise click.UsageError("--key-attributes must list at least one attribute.")
    with d365_errors(ctx):
        info = meta_mod.create_entity_key(
            ctx.backend(), entity=entity, schema_name=schema_name,
            key_attributes=attrs, display_name=display_name,
            solution=solution, if_exists=if_exists,
        )
        if publish and not info.get("_dry_run") and not info.get("skipped"):
            from crm.core import solution as sol_mod
            sol_mod.publish_all(ctx.backend())
            info["published"] = True
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, f"{entity}.{schema_name}", info, solution=solution)


@metadata_group.command("delete-key")
@click.argument("entity")
@click.argument("key")
@_destructive_option
@_solution_option
@pass_ctx
def metadata_delete_key(ctx: CLIContext, entity, key, yes, solution, require_solution):
    """Delete an alternate key from an entity."""
    _confirm_destructive(
        ctx, "alternate key", f"{entity}.{key}", yes,
        message=f"This will delete alternate key {entity}.{key!r}. Continue?",
    )
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    with d365_errors(ctx):
        info = meta_mod.delete_entity_key(ctx.backend(), entity, key, solution=solution)
    _emit_with_warning(ctx, info, warning)
    _journal(ctx, f"{entity}.{key}", info, solution=solution)


# Relationship-creation commands (create-one-to-many / create-many-to-many)
# intentionally keep --schema-name required and do NOT get publisher-prefix
# defaulting: a relationship name spans two entities and cannot be derived from
# a single display token, unlike create-entity/add-attribute/create-optionset.
@metadata_group.command("create-one-to-many")
@click.option("--schema-name", required=True, help="Relationship schema name with publisher prefix.")
@click.option("--referenced-entity", required=True, help='"1" side logical name (e.g. account).')
@click.option("--referencing-entity", required=True, help='"N" side logical name (e.g. new_project).')
@click.option("--lookup-schema", required=True, help="Lookup attribute schema name on referencing entity.")
@click.option("--lookup-display", required=True, help="UI label for the lookup attribute.")
@click.option("--lookup-required", type=_REQUIRED, default="None")
@click.option("--lookup-description", default=None)
@click.option("--cascade-assign", type=_CASCADE, default="NoCascade")
@click.option("--cascade-delete", type=_CASCADE, default="RemoveLink")
@click.option("--cascade-reparent", type=_CASCADE, default="NoCascade")
@click.option("--cascade-share", type=_CASCADE, default="NoCascade")
@click.option("--cascade-unshare", type=_CASCADE, default="NoCascade")
@click.option("--cascade-merge", type=_CASCADE, default="NoCascade")
@click.option("--menu-label", default=None)
@click.option("--menu-behavior", type=_MENU, default="UseCollectionName")
@click.option("--menu-order", type=int, default=10000)
@_solution_option
@click.option("--if-exists", type=click.Choice(["error", "skip"]), default="error",
              help="If the relationship already exists: error (default) or skip (no-op success).")
@_publish_option
@pass_ctx
def metadata_create_one_to_many(
    ctx: CLIContext, schema_name, referenced_entity, referencing_entity, lookup_schema,
    lookup_display, lookup_required, lookup_description,
    cascade_assign, cascade_delete, cascade_reparent, cascade_share,
    cascade_unshare, cascade_merge, menu_label, menu_behavior, menu_order,
    solution, require_solution, if_exists, publish,
):
    """Create a 1:N relationship and its lookup attribute atomically."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = rel_mod.create_one_to_many(
            ctx.backend(),
            schema_name=schema_name,
            referenced_entity=referenced_entity,
            referencing_entity=referencing_entity,
            lookup_schema=lookup_schema,
            lookup_display=lookup_display,
            lookup_required=lookup_required,
            lookup_description=lookup_description,
            cascade_assign=cascade_assign,
            cascade_delete=cascade_delete,
            cascade_reparent=cascade_reparent,
            cascade_share=cascade_share,
            cascade_unshare=cascade_unshare,
            cascade_merge=cascade_merge,
            menu_label=menu_label,
            menu_behavior=menu_behavior,
            menu_order=menu_order,
            publish=publish,
            solution=solution,
            if_exists=if_exists,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, schema_name, info, solution=solution)


@metadata_group.command("create-many-to-many")
@click.option("--schema-name", required=True)
@click.option("--entity1", "entity1_logical", required=True)
@click.option("--entity2", "entity2_logical", required=True)
@click.option("--intersect-entity", required=True)
@click.option("--entity1-menu-label", default=None)
@click.option("--entity1-menu-behavior", type=_MENU, default="UseCollectionName")
@click.option("--entity1-menu-order", type=int, default=10000)
@click.option("--entity2-menu-label", default=None)
@click.option("--entity2-menu-behavior", type=_MENU, default="UseCollectionName")
@click.option("--entity2-menu-order", type=int, default=10000)
@_solution_option
@click.option("--if-exists", type=click.Choice(["error", "skip"]), default="error",
              help="If the relationship already exists: error (default) or skip (no-op success).")
@_publish_option
@pass_ctx
def metadata_create_many_to_many(
    ctx: CLIContext, schema_name, entity1_logical, entity2_logical, intersect_entity,
    entity1_menu_label, entity1_menu_behavior, entity1_menu_order,
    entity2_menu_label, entity2_menu_behavior, entity2_menu_order,
    solution, require_solution, if_exists, publish,
):
    """Create an N:N relationship via the dedicated action."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = rel_mod.create_many_to_many(
            ctx.backend(),
            schema_name=schema_name,
            entity1_logical=entity1_logical,
            entity2_logical=entity2_logical,
            intersect_entity=intersect_entity,
            entity1_menu_label=entity1_menu_label,
            entity1_menu_behavior=entity1_menu_behavior,
            entity1_menu_order=entity1_menu_order,
            entity2_menu_label=entity2_menu_label,
            entity2_menu_behavior=entity2_menu_behavior,
            entity2_menu_order=entity2_menu_order,
            publish=publish,
            solution=solution,
            if_exists=if_exists,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, schema_name, info, solution=solution)


@metadata_group.command("delete-relationship")
@click.argument("schema_name")
@_destructive_option
@_solution_option
@click.option("--check-dependencies", "check_dependencies", is_flag=True, default=False,
              help="Preview blocking dependencies (RetrieveDependenciesForDelete) in the result; pairs with --dry-run.")
@pass_ctx
def metadata_delete_relationship(ctx: CLIContext, schema_name, yes, solution, require_solution, check_dependencies):
    """Delete a custom relationship (1:N or N:N) by schema name."""
    _confirm_destructive(ctx, "relationship", schema_name, yes)
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    with d365_errors(ctx):
        info = rel_mod.delete_relationship(
            ctx.backend(), schema_name, solution=solution,
            check_dependencies=check_dependencies,
        )
    _emit_with_warning(ctx, info, warning)
    _journal(ctx, schema_name, info, solution=solution)


@metadata_group.command("list-optionsets")
@click.option("--custom-only", is_flag=True)
@click.option("--top", type=int, default=None)
@pass_ctx
def metadata_list_optionsets(ctx: CLIContext, custom_only, top):
    """List global option set definitions."""
    with d365_errors(ctx):
        rows = os_mod.list_optionsets(ctx.backend(), custom_only=custom_only, top=top)
    headers = ["Name", "IsCustomOptionSet", "IsManaged"]
    table_rows = [
        [r.get("Name", ""), str(r.get("IsCustomOptionSet", "")),
         str(r.get("IsManaged", ""))]
        for r in rows
    ]
    ctx.emit(True, data=rows, table={"headers": headers, "rows": table_rows},
             meta={"count": len(rows)})


@metadata_group.command("get-optionset")
@click.argument("name")
@pass_ctx
def metadata_get_optionset(ctx: CLIContext, name):
    """Retrieve a global option set, including its options."""
    with d365_errors(ctx):
        info = os_mod.get_optionset(ctx.backend(), name)
    # Flattened convenience list (#76); raw `data` left untouched. Options live at
    # the root for a global option set. `ctx.emit` prints `meta` in human mode too,
    # so gate it on JSON mode to keep human output unchanged (#76).
    meta = {"options": meta_mod.flatten_options(info)} if ctx.json_mode else None
    ctx.emit(True, data=info, meta=meta)


@metadata_group.command("create-optionset")
@click.option("--name", default=None,
              help="Fully prefixed option set name, e.g. 'new_priority'. "
                   "Defaults to <publisher_prefix>_<display> from the profile.")
@click.option("--display", "display_name", required=True)
@click.option("--description", default=None)
@click.option("--option", "options", multiple=True,
              help="Option as 'value:label' or ':label' (auto value). Repeatable.")
@_solution_option
@click.option("--if-exists", type=click.Choice(["error", "skip"]), default="error",
              help="If the option set already exists: error (default) or skip (no-op success).")
@_publish_option
@pass_ctx
def metadata_create_optionset(ctx: CLIContext, name, display_name, description, options,
                              solution, require_solution, if_exists, publish):
    """Create a global option set."""
    name = _resolve_schema_name(ctx, name, display_name, "--name")
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    parsed = _parse_value_labels(options, flag="--option")
    with d365_errors(ctx):
        info = os_mod.create_optionset(
            ctx.backend(),
            name=name, display_name=display_name,
            description=description, options=parsed or None,
            publish=publish, solution=solution, if_exists=if_exists,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, name, info, solution=solution)


@metadata_group.command("update-optionset")
@click.argument("name")
@click.option("--insert-option", "insert_options", multiple=True,
              help="Insert option 'value:label' or ':label'. Repeatable.")
@click.option("--update-option", "update_options", multiple=True,
              help="Update an existing option's label, 'value:label'. Repeatable.")
@click.option("--delete-option", "delete_options", multiple=True, type=int,
              help="Delete option by value. Repeatable.")
@click.option("--reorder", default=None,
              help="Comma-separated full ordered list of values, e.g. '1,2,7,4'.")
@_solution_option
@_publish_option
@pass_ctx
def metadata_update_optionset(ctx: CLIContext, name, insert_options, update_options,
                              delete_options, reorder, solution, require_solution, publish):
    """Granular update: insert/update/delete/reorder options."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    insert = _parse_value_labels(insert_options, flag="--insert-option")
    # require_value=True guarantees an int for every value, so this narrowing
    # cast is sound; it satisfies update_optionset's strict list[tuple[int, str]].
    update = cast(
        "list[tuple[int, str]]",
        _parse_value_labels(update_options, flag="--update-option", require_value=True),
    )

    reorder_list: list[int] | None = None
    if reorder:
        try:
            reorder_list = [int(x.strip()) for x in reorder.split(",") if x.strip()]
        except ValueError as exc:
            raise click.UsageError(
                f"--reorder must be a comma-separated list of integers: {reorder!r}"
            ) from exc

    with d365_errors(ctx):
        info = os_mod.update_optionset(
            ctx.backend(),
            name,
            insert=insert or None,
            update=update or None,
            delete=list(delete_options) or None,
            reorder=reorder_list,
            publish=publish,
            solution=solution,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, name, info, solution=solution)


@metadata_group.command("delete-optionset")
@click.argument("name")
@_destructive_option
@_solution_option
@click.option("--check-dependencies", "check_dependencies", is_flag=True, default=False,
              help="Preview blocking dependencies (RetrieveDependenciesForDelete) in the result; pairs with --dry-run.")
@pass_ctx
def metadata_delete_optionset(ctx: CLIContext, name, yes, solution, require_solution, check_dependencies):
    """Delete a custom global option set."""
    _confirm_destructive(ctx, "option set", name, yes)
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    with d365_errors(ctx):
        info = os_mod.delete_optionset(ctx.backend(), name, solution=solution,
                                       check_dependencies=check_dependencies)
    _emit_with_warning(ctx, info, warning)
    _journal(ctx, name, info, solution=solution)


@metadata_group.command("status-add")
@click.argument("entity")
@click.option("--state", "state_code", type=int, required=True,
              help="statecode value the new status belongs to (e.g. 0 = Active).")
@click.option("--label", "label_text", required=True, help="Status option label.")
@click.option("--value", type=int, default=None,
              help="Explicit statuscode value. Omit to let the server assign one.")
@click.option("--description", default=None)
@_solution_option
@_publish_option
@pass_ctx
def metadata_status_add(ctx: CLIContext, entity, state_code, label_text, value,
                        description, solution, require_solution, publish):
    """Add a statuscode option tied to a state (InsertStatusValue)."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = sm_mod.add_status_value(
            ctx.backend(), entity, state_code=state_code, label_text=label_text,
            value=value, description=description, publish=publish, solution=solution,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, entity, info, solution=solution)


@metadata_group.command("state-relabel")
@click.argument("entity")
@click.option("--value", type=int, required=True,
              help="statecode value to relabel (e.g. 1 = Inactive).")
@click.option("--label", "label_text", required=True, help="New state label.")
@click.option("--description", default=None)
@click.option("--merge-labels/--no-merge-labels", default=False,
              help="Preserve labels in untouched languages (MergeLabels).")
@_solution_option
@_publish_option
@pass_ctx
def metadata_state_relabel(ctx: CLIContext, entity, value, label_text, description,
                           merge_labels, solution, require_solution, publish):
    """Relabel a statecode state option (UpdateStateValue)."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = sm_mod.relabel_state_value(
            ctx.backend(), entity, value=value, label_text=label_text,
            description=description, merge_labels=merge_labels,
            publish=publish, solution=solution,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, entity, info, solution=solution)


@metadata_group.command("set-transitions")
@click.argument("entity")
@click.option("--transition", "transitions", multiple=True, required=True,
              help="Allowed status transition 'fromValue:toValue' (statuscode "
                   "values). Repeatable. EnforceStateTransitions (which activates "
                   "enforcement) is app-set/read-only and out of the CLI's reach.")
@_solution_option
@_publish_option
@pass_ctx
def metadata_set_transitions(ctx: CLIContext, entity, transitions, solution,
                             require_solution, publish):
    """Define allowed statuscode transitions (StatusOptionMetadata.TransitionData)."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    parsed: list[tuple[int, int]] = []
    for item in transitions:
        if ":" not in item:
            raise click.UsageError(
                f"--transition must be 'fromValue:toValue': {item!r}"
            )
        src, _, tgt = item.partition(":")
        try:
            parsed.append((int(src.strip()), int(tgt.strip())))
        except ValueError as exc:
            raise click.UsageError(
                f"--transition values must be integers: {item!r}"
            ) from exc
    with d365_errors(ctx):
        info = sm_mod.set_status_transitions(
            ctx.backend(), entity, transitions=parsed,
            publish=publish, solution=solution,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, entity, info, solution=solution)


@metadata_group.command("create-mapping")
@click.argument("relationship")
@click.option("--from", "source_attr", default=None,
              help="Source (parent) attribute logical name.")
@click.option("--to", "target_attr", default=None,
              help="Target (child) attribute logical name.")
@click.option("--auto", is_flag=True, default=False,
              help="Bulk-generate likely mappings via AutoMapEntity (replaces "
                   "any existing maps for the pair).")
@_solution_option
@pass_ctx
def metadata_create_mapping(ctx: CLIContext, relationship, source_attr, target_attr,
                            auto, solution, require_solution):
    """Create a field mapping on a 1:N relationship, or --auto generate them."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    if auto:
        if source_attr or target_attr:
            raise click.UsageError("--auto cannot be combined with --from/--to.")
        with d365_errors(ctx):
            info = mp_mod.auto_map(ctx.backend(), relationship, solution=solution)
    else:
        if not (source_attr and target_attr):
            raise click.UsageError("pass both --from and --to, or use --auto.")
        with d365_errors(ctx):
            info = mp_mod.create_mapping(
                ctx.backend(), relationship, source_attr=source_attr,
                target_attr=target_attr, solution=solution,
            )
    _emit_with_warning(ctx, info, warning)
    _journal(ctx, relationship, info, solution=solution)


from crm.core.metadata import list_actions, list_functions  # noqa: E402


@metadata_group.command("list-actions")
@pass_ctx
def metadata_list_actions(ctx: CLIContext):
    """List OData actions advertised by the service ($metadata)."""
    with d365_errors(ctx):
        items = list_actions(ctx.backend())
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["Name", "Bound", "Returns", "Parameters"]
    rows = [
        [a["name"], a["is_bound"], a["return_type"] or "",
         ", ".join(f"{p['name']}:{p['type']}" for p in a["parameters"])]
        for a in items
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"count": len(items)})


@metadata_group.command("list-functions")
@pass_ctx
def metadata_list_functions(ctx: CLIContext):
    """List OData functions advertised by the service ($metadata)."""
    with d365_errors(ctx):
        items = list_functions(ctx.backend())
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["Name", "Bound", "Composable", "Returns", "Parameters"]
    rows = [
        [f["name"], f["is_bound"], f["is_composable"], f["return_type"] or "",
         ", ".join(f"{p['name']}:{p['type']}" for p in f["parameters"])]
        for f in items
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"count": len(items)})
