"""Metadata commands (entities, attributes, relationships, option sets)."""
# pyright: basic
from __future__ import annotations
import time
import click
from crm.core import metadata as meta_mod
from crm.core import metadata_attrs as ma_mod
from crm.core import metadata_cache as mc_mod
from crm.core import metadata_update as mu_mod
from crm.core import optionsets as os_mod
from crm.core import relationships as rel_mod
from crm.core import dependencies as dep_mod
from crm.core import export_spec as export_spec_mod
from crm.core import clone as clone_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error,
    _admin_header_options,
    _admin_kwargs,
    _confirm_destructive,
    _journal,
    _resolve_publish,
    _solution_option,
    _require_solution,
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
@click.option("--top", type=int)
@pass_ctx
def metadata_entities(ctx: CLIContext, custom_only, top):
    """List entity definitions."""
    use_cache = ctx.cache_metadata or ctx.refresh_metadata
    if use_cache:
        if custom_only:
            raise click.UsageError(
                "--custom-only is not supported with the metadata cache "
                "(--cache-metadata / --refresh-metadata); "
                "the cache stores only logical/set names"
            )
        try:
            backend = ctx.backend()
            lookup = mc_mod.load_definitions(
                backend.profile,
                fetch=lambda: meta_mod.list_entity_definitions(backend),
                refresh=ctx.refresh_metadata,
                now=time.time(),
            )
        except D365Error as exc:
            _handle_d365_error(ctx, exc)
            return
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
    try:
        items = meta_mod.list_entities(ctx.backend(), custom_only=custom_only, top=top)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["LogicalName", "EntitySetName", "SchemaName", "IsCustom"]
    rows = [
        [it.get("LogicalName", ""), it.get("EntitySetName", ""),
         it.get("SchemaName", ""), str(it.get("IsCustomEntity", False))]
        for it in items
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta={"count": len(items)})


@metadata_group.command("cache-clear")
@pass_ctx
def metadata_cache_clear(ctx: CLIContext):
    """Delete the active profile's on-disk metadata cache."""
    try:
        backend = ctx.backend()
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    cleared = mc_mod.clear(backend.profile)
    ctx.emit(True, data={"cleared": cleared})


@metadata_group.command("entity")
@click.argument("logical_name")
@pass_ctx
def metadata_entity(ctx: CLIContext, logical_name):
    """Show full entity definition."""
    try:
        info = meta_mod.entity_info(ctx.backend(), logical_name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@metadata_group.command("attributes")
@click.argument("logical_name")
@pass_ctx
def metadata_attributes(ctx: CLIContext, logical_name):
    """List attributes for an entity."""
    try:
        items = meta_mod.list_attributes(ctx.backend(), logical_name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["LogicalName", "SchemaName", "AttributeType", "IsCustom"]
    rows = [
        [it.get("LogicalName", ""), it.get("SchemaName", ""),
         it.get("AttributeType", ""), str(it.get("IsCustomAttribute", False))]
        for it in items
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta={"count": len(items)})


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
    try:
        info = meta_mod.attribute_info(ctx.backend(), logical_name, attribute_name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
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
    try:
        info = meta_mod.picklist_options(
            ctx.backend(), logical_name, attribute,
            global_optionset=not no_global,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
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
        _handle_d365_error(ctx, exc)
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
@click.option("--output", "-o", default=None, type=click.Path(dir_okay=False),
              help="Write the bare spec as YAML to FILE (directly consumable by crm apply -f).")
@pass_ctx
def metadata_export_spec(ctx: CLIContext, logical_name, with_views, with_relationships, output):
    """Export a live entity as an apply-consumable desired-state spec.

    Reads the entity's metadata over the Web API (pure GETs) and emits a spec
    that round-trips through `crm apply -f`. Without -o, the spec is emitted
    under the standard JSON envelope (pipeable). With -o, the bare YAML spec is
    written to FILE so it is ready for `crm apply -f <file>`.
    """
    warnings: list[str] = []
    try:
        spec = export_spec_mod.build_entity_spec(
            ctx.backend(), logical_name,
            with_views=with_views,
            with_relationships=with_relationships,
            warnings=warnings,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return

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
    try:
        info = dep_mod.retrieve_dependencies(ctx.backend(), kind, target, for_=for_)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
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
@click.option("--publish/--no-publish", default=True,
              help="Run PublishAllXml after creation. Default: publish.")
@pass_ctx
def metadata_create_entity(
    ctx: CLIContext, schema_name, display_name, display_collection, primary_attr_schema,
    primary_attr_label, primary_max_length, description, ownership,
    has_activities, has_notes, is_activity, solution, require_solution, if_exists, publish,
):
    """Create a new custom entity (table)."""
    schema_name = _resolve_schema_name(ctx, schema_name, display_name, "--schema-name")
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    publish = _resolve_publish(ctx, publish)
    try:
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
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning, meta={"staged": True} if ctx.stage_only else None)
    _journal(ctx, "metadata create-entity", schema_name, info, solution=solution)


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
@click.option("--publish/--no-publish", default=True,
              help="Run PublishAllXml after creation. Default: publish.")
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
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    publish = _resolve_publish(ctx, publish)
    try:
        info = clone_mod.clone_entity(
            ctx.backend(), source, new_schema_name,
            display=display,
            with_forms=with_forms, with_views=with_views,
            with_workflows=with_workflows, with_charts=with_charts,
            solution=solution, publish=publish,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    notes = [warning] if warning else []
    if info.get("views_note"):
        notes.append(info["views_note"])
    skipped = info.get("skipped_workflows") or []
    if skipped:
        names = ", ".join(w["name"] for w in skipped)
        notes.append(f"{len(skipped)} workflow(s) not cloned: {names}")
    _emit_with_warning(ctx, info, "; ".join(notes) or None)
    _journal(ctx, "metadata clone-entity", new_schema_name, info, solution=solution)


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
@click.option("--publish/--no-publish", default=True,
              help="Run PublishAllXml after update. Default: publish.")
@pass_ctx
def metadata_update_entity(
    ctx: CLIContext, logical_name, display_name, display_collection_name,
    description, ownership, has_activities, has_notes, solution, publish,
):
    """Update an entity (table) definition (retrieve-merge-write)."""
    publish = _resolve_publish(ctx, publish)
    try:
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
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info, meta={"staged": True} if ctx.stage_only else None)
    _journal(ctx, "metadata update-entity", logical_name, info, solution=solution)


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
@click.option("--publish/--no-publish", default=True,
              help="Run PublishAllXml after update. Default: publish.")
@pass_ctx
def metadata_update_attribute(
    ctx: CLIContext, entity, attribute, display_name, description, required,
    max_length, precision, min_value, max_value, format_name, solution, publish,
):
    """Update an attribute (column) definition (retrieve-merge-write).

    Option-set option edits are NOT handled here — use `update-optionset`.
    """
    publish = _resolve_publish(ctx, publish)
    try:
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
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info, meta={"staged": True} if ctx.stage_only else None)
    _journal(ctx, "metadata update-attribute", f"{entity}.{attribute}", info, solution=solution)


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
@click.option("--publish/--no-publish", default=True,
              help="Run PublishAllXml after update. Default: publish.")
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
    try:
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
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info, meta={"staged": True} if ctx.stage_only else None)
    _journal(ctx, "metadata update-relationship", schema_name, info, solution=solution)


@metadata_group.command("relationships")
@click.argument("logical_name")
@pass_ctx
def metadata_relationships(ctx: CLIContext, logical_name):
    """Show one-to-many, many-to-one, and many-to-many relationships."""
    try:
        info = rel_mod.list_relationships(ctx.backend(), logical_name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info, meta={
        "one_to_many": len(info.get("OneToMany", [])),
        "many_to_one": len(info.get("ManyToOne", [])),
        "many_to_many": len(info.get("ManyToMany", [])),
    })


@metadata_group.command("delete-entity")
@click.argument("logical_name")
@click.option("--yes", is_flag=True, help="Skip interactive confirmation.")
@_solution_option
@click.option("--check-dependencies", "check_dependencies", is_flag=True, default=False,
              help="Preview blocking dependencies (RetrieveDependenciesForDelete) in the result; pairs with --dry-run.")
@pass_ctx
def metadata_delete_entity(ctx: CLIContext, logical_name, yes, solution, require_solution, check_dependencies):
    """Permanently delete a custom entity (table) and ALL its rows."""
    if not _confirm_destructive("entity", logical_name, yes):
        ctx.emit(False, error="aborted by user")
        return
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    try:
        info = meta_mod.delete_entity(
            ctx.backend(), logical_name, solution=solution,
            check_dependencies=check_dependencies,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning)
    _journal(ctx, "metadata delete-entity", logical_name, info, solution=solution)


@metadata_group.command("add-attribute")
@click.argument("entity")
@click.option("--kind", required=True,
              type=click.Choice([
                  "string", "memo", "integer", "bigint", "decimal", "double",
                  "money", "boolean", "datetime", "picklist", "multiselect",
                  "lookup", "image", "file",
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
              help="String/memo: max characters.")
@click.option("--format", "format_name", default=None,
              help="String: Text|Email|Url|Phone|TextArea. Datetime: DateOnly|DateAndTime.")
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
@_solution_option
@click.option("--if-exists", type=click.Choice(["error", "skip"]), default="error",
              help="If the attribute already exists: error (default) or skip (no-op success).")
@click.option("--publish/--no-publish", default=True,
              help="Run PublishAllXml after creation. Default: publish.")
@pass_ctx
def metadata_add_attribute(
    ctx: CLIContext, entity, kind, schema_name, display_name, description, required,
    max_length, format_name, min_value, max_value, precision,
    true_label, false_label, default_value,
    optionset_name, options, target_entity, relationship_schema,
    max_size_kb, solution, require_solution, if_exists, publish,
):
    """Add an attribute (column) to an existing entity."""
    schema_name = _resolve_schema_name(ctx, schema_name, display_name, "--schema-name")
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    publish = _resolve_publish(ctx, publish)
    parsed_options: list[tuple[int | None, str]] | None = None
    if options:
        parsed_options = []
        for raw in options:
            if ":" not in raw:
                raise click.UsageError(
                    f"--option must be 'value:label' or ':label', got: {raw!r}"
                )
            v, _, lab = raw.partition(":")
            v = v.strip()
            lab = lab.strip()
            parsed_options.append((int(v) if v else None, lab))

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

    try:
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
            publish=publish,
            solution=solution,
            if_exists=if_exists,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning, meta={"staged": True} if ctx.stage_only else None)
    _journal(ctx, "metadata add-attribute", f"{entity}.{schema_name}", info, solution=solution)


@metadata_group.command("delete-attribute")
@click.argument("entity")
@click.argument("attribute")
@click.option("--yes", is_flag=True, help="Skip interactive confirmation.")
@_solution_option
@click.option("--check-dependencies", "check_dependencies", is_flag=True, default=False,
              help="Preview blocking dependencies (RetrieveDependenciesForDelete) in the result; pairs with --dry-run.")
@pass_ctx
def metadata_delete_attribute(ctx: CLIContext, entity, attribute, yes, solution, require_solution, check_dependencies):
    """Delete a custom attribute (column) from an entity."""
    if not _confirm_destructive("attribute", f"{entity}.{attribute}", yes):
        ctx.emit(False, error="aborted by user")
        return
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    try:
        info = ma_mod.delete_attribute(
            ctx.backend(), entity, attribute, solution=solution,
            check_dependencies=check_dependencies,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning)
    _journal(ctx, "metadata delete-attribute", f"{entity}.{attribute}", info, solution=solution)


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
@click.option("--publish/--no-publish", default=True)
@pass_ctx
def metadata_create_one_to_many(
    ctx: CLIContext, schema_name, referenced_entity, referencing_entity, lookup_schema,
    lookup_display, lookup_required, lookup_description,
    cascade_assign, cascade_delete, cascade_reparent, cascade_share,
    cascade_unshare, cascade_merge, menu_label, menu_behavior, menu_order,
    solution, require_solution, if_exists, publish,
):
    """Create a 1:N relationship and its lookup attribute atomically."""
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    publish = _resolve_publish(ctx, publish)
    try:
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
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning, meta={"staged": True} if ctx.stage_only else None)
    _journal(ctx, "metadata create-one-to-many", schema_name, info, solution=solution)


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
@click.option("--publish/--no-publish", default=True)
@pass_ctx
def metadata_create_many_to_many(
    ctx: CLIContext, schema_name, entity1_logical, entity2_logical, intersect_entity,
    entity1_menu_label, entity1_menu_behavior, entity1_menu_order,
    entity2_menu_label, entity2_menu_behavior, entity2_menu_order,
    solution, require_solution, if_exists, publish,
):
    """Create an N:N relationship via the dedicated action."""
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    publish = _resolve_publish(ctx, publish)
    try:
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
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning, meta={"staged": True} if ctx.stage_only else None)
    _journal(ctx, "metadata create-many-to-many", schema_name, info, solution=solution)


@metadata_group.command("delete-relationship")
@click.argument("schema_name")
@click.option("--yes", is_flag=True, help="Skip interactive confirmation.")
@_solution_option
@click.option("--check-dependencies", "check_dependencies", is_flag=True, default=False,
              help="Preview blocking dependencies (RetrieveDependenciesForDelete) in the result; pairs with --dry-run.")
@pass_ctx
def metadata_delete_relationship(ctx: CLIContext, schema_name, yes, solution, require_solution, check_dependencies):
    """Delete a custom relationship (1:N or N:N) by schema name."""
    if not _confirm_destructive("relationship", schema_name, yes):
        ctx.emit(False, error="aborted by user")
        return
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    try:
        info = rel_mod.delete_relationship(
            ctx.backend(), schema_name, solution=solution,
            check_dependencies=check_dependencies,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning)
    _journal(ctx, "metadata delete-relationship", schema_name, info, solution=solution)


@metadata_group.command("list-optionsets")
@click.option("--custom-only", is_flag=True)
@click.option("--top", type=int, default=None)
@pass_ctx
def metadata_list_optionsets(ctx: CLIContext, custom_only, top):
    """List global option set definitions."""
    try:
        rows = os_mod.list_optionsets(ctx.backend(), custom_only=custom_only, top=top)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
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
    try:
        info = os_mod.get_optionset(ctx.backend(), name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
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
@click.option("--publish/--no-publish", default=True)
@pass_ctx
def metadata_create_optionset(ctx: CLIContext, name, display_name, description, options,
                              solution, require_solution, if_exists, publish):
    """Create a global option set."""
    name = _resolve_schema_name(ctx, name, display_name, "--name")
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    publish = _resolve_publish(ctx, publish)
    parsed: list[tuple[int | None, str]] = []
    for raw in options:
        if ":" not in raw:
            raise click.UsageError(f"--option must be 'value:label' or ':label', got: {raw!r}")
        v, _, lab = raw.partition(":")
        v = v.strip()
        lab = lab.strip()
        parsed.append((int(v) if v else None, lab))
    try:
        info = os_mod.create_optionset(
            ctx.backend(),
            name=name, display_name=display_name,
            description=description, options=parsed or None,
            publish=publish, solution=solution, if_exists=if_exists,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning, meta={"staged": True} if ctx.stage_only else None)
    _journal(ctx, "metadata create-optionset", name, info, solution=solution)


@metadata_group.command("update-optionset")
@click.argument("name")
@click.option("--insert-option", "insert_options", multiple=True,
              help="Insert option 'value:label' or ':label'. Repeatable.")
@click.option("--update-option", "update_options", multiple=True,
              help="Update existing option 'value:new_label'. Repeatable.")
@click.option("--delete-option", "delete_options", multiple=True, type=int,
              help="Delete option by value. Repeatable.")
@click.option("--reorder", default=None,
              help="Comma-separated full ordered list of values, e.g. '1,2,7,4'.")
@_solution_option
@click.option("--publish/--no-publish", default=True)
@pass_ctx
def metadata_update_optionset(ctx: CLIContext, name, insert_options, update_options,
                              delete_options, reorder, solution, require_solution, publish):
    """Granular update: insert/update/delete/reorder options."""
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    publish = _resolve_publish(ctx, publish)
    insert: list[tuple[int | None, str]] = []
    for raw in insert_options:
        if ":" not in raw:
            raise click.UsageError(f"--insert-option must be 'value:label' or ':label': {raw!r}")
        v, _, lab = raw.partition(":")
        v = v.strip()
        lab = lab.strip()
        insert.append((int(v) if v else None, lab))

    update: list[tuple[int, str]] = []
    for raw in update_options:
        if ":" not in raw:
            raise click.UsageError(f"--update-option must be 'value:new_label': {raw!r}")
        v, _, lab = raw.partition(":")
        lab = lab.strip()
        try:
            update.append((int(v.strip()), lab))
        except ValueError as exc:
            raise click.UsageError(
                f"--update-option value must be int: {raw!r}"
            ) from exc

    reorder_list: list[int] | None = None
    if reorder:
        try:
            reorder_list = [int(x.strip()) for x in reorder.split(",") if x.strip()]
        except ValueError as exc:
            raise click.UsageError(
                f"--reorder must be a comma-separated list of integers: {reorder!r}"
            ) from exc

    try:
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
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning, meta={"staged": True} if ctx.stage_only else None)
    _journal(ctx, "metadata update-optionset", name, info, solution=solution)


@metadata_group.command("delete-optionset")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip interactive confirmation.")
@_solution_option
@click.option("--check-dependencies", "check_dependencies", is_flag=True, default=False,
              help="Preview blocking dependencies (RetrieveDependenciesForDelete) in the result; pairs with --dry-run.")
@pass_ctx
def metadata_delete_optionset(ctx: CLIContext, name, yes, solution, require_solution, check_dependencies):
    """Delete a custom global option set."""
    if not _confirm_destructive("option set", name, yes):
        ctx.emit(False, error="aborted by user")
        return
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    try:
        info = os_mod.delete_optionset(ctx.backend(), name, solution=solution,
                                       check_dependencies=check_dependencies)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning)
    _journal(ctx, "metadata delete-optionset", name, info, solution=solution)


from crm.core.metadata import list_actions, list_functions  # noqa: E402


@metadata_group.command("list-actions")
@pass_ctx
def metadata_list_actions(ctx: CLIContext):
    """List OData actions advertised by the service ($metadata)."""
    try:
        items = list_actions(ctx.backend())
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["Name", "Parameters"]
    rows = [
        [a["name"], ", ".join(f"{p['name']}:{p['type']}" for p in a["parameters"])]
        for a in items
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"count": len(items)})


@metadata_group.command("list-functions")
@pass_ctx
def metadata_list_functions(ctx: CLIContext):
    """List OData functions advertised by the service ($metadata)."""
    try:
        items = list_functions(ctx.backend())
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["Name", "Parameters"]
    rows = [
        [f["name"], ", ".join(f"{p['name']}:{p['type']}" for p in f["parameters"])]
        for f in items
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"count": len(items)})
