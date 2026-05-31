"""Metadata commands (entities, attributes, relationships, option sets)."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import metadata as meta_mod
from crm.core import metadata_update as mu_mod
from crm.core import optionsets as os_mod
from crm.core import relationships as rel_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error,
    _admin_header_options,
    _admin_kwargs,
    _confirm_destructive,
    _resolve_publish,
    _solution_option,
    _require_solution,
    _resolve_solution,
    _resolve_schema_name,
    _emit_with_warning,
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
@pass_ctx
def metadata_attribute(ctx: CLIContext, logical_name, attribute_name):
    """Show a single attribute definition."""
    try:
        info = meta_mod.attribute_info(ctx.backend(), logical_name, attribute_name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
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
    if ctx.json_mode:
        ctx.emit(True, data=info)
        return
    options = (info.get("OptionSet") or {}).get("Options") or []
    if not options:
        options = (info.get("GlobalOptionSet") or {}).get("Options") or []
    headers = ["Value", "Label"]
    rows = [
        [str(o.get("Value")),
         ((o.get("Label") or {}).get("UserLocalizedLabel") or {}).get("Label", "")]
        for o in options
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"entity": logical_name, "attribute": attribute, "count": len(options)})


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


@metadata_group.command("relationships")
@click.argument("logical_name")
@pass_ctx
def metadata_relationships(ctx: CLIContext, logical_name):
    """Show one-to-many + many-to-many relationships."""
    try:
        info = rel_mod.list_relationships(ctx.backend(), logical_name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info, meta={
        "one_to_many": len(info.get("OneToMany", [])),
        "many_to_many": len(info.get("ManyToMany", [])),
    })


@metadata_group.command("delete-entity")
@click.argument("logical_name")
@click.option("--yes", is_flag=True, help="Skip interactive confirmation.")
@_solution_option
@pass_ctx
def metadata_delete_entity(ctx: CLIContext, logical_name, yes, solution, require_solution):
    """Permanently delete a custom entity (table) and ALL its rows."""
    if not _confirm_destructive("entity", logical_name, yes):
        ctx.emit(False, error="aborted by user")
        return
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    try:
        info = meta_mod.delete_entity(
            ctx.backend(), logical_name, solution=solution,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning)


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
        from crm.core import metadata_attrs as ma_mod
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
@click.option("--menu-behavior", type=_MENU, default="UseLabel")
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
    """Retrieve a global option set with options expanded."""
    try:
        info = os_mod.get_optionset(ctx.backend(), name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


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


@metadata_group.command("delete-optionset")
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip interactive confirmation.")
@_solution_option
@pass_ctx
def metadata_delete_optionset(ctx: CLIContext, name, yes, solution, require_solution):
    """Delete a custom global option set."""
    if not _confirm_destructive("option set", name, yes):
        ctx.emit(False, error="aborted by user")
        return
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    try:
        info = os_mod.delete_optionset(ctx.backend(), name, solution=solution)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning)


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
