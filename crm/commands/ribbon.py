"""Entity ribbon (command-bar) commands — issue #142."""
# pyright: basic
from __future__ import annotations
import tempfile
import zipfile
import xml.dom.minidom as minidom
import xml.etree.ElementTree as ET
from pathlib import Path
import click
from crm.core import ribbon as ribbon_mod
from crm.utils.d365_backend import D365Error, odata_literal
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _destructive_option,
    _handle_d365_error, _journal, _confirm_destructive,
    _solution_option, _resolve_solution, d365_errors,
    _output_option, _publish_option, _resolve_publish,
)

# OOB ribbon commands are Microsoft-published; reusing or overriding them is
# outside Microsoft's supported customization surface (it can break on platform
# updates). We still allow it — both hide methods are documented — but warn.
_OOB_REUSE_WARNING = (
    "Overriding or hiding an out-of-box ribbon command is on unsupported ground "
    "and may change across platform updates.")


@click.group("ribbon")
def ribbon_group():
    """Read and edit entity command-bar (ribbon) buttons."""


@ribbon_group.command("export")
@click.argument("entity", required=False)
@click.option("--application", "-a", "application", is_flag=True,
              help="Export the application-wide ribbon (RetrieveApplicationRibbon) "
                   "instead of a single entity's. Omit ENTITY when set.")
@_output_option(help="Write the ribbon XML to this file instead of stdout.")
@pass_ctx
def ribbon_export(ctx: CLIContext, entity, application, output):
    """Export a composed ribbon as readable XML.

    Pass ENTITY for one table's ribbon, or --application for the app-wide ribbon
    (the commands not bound to a specific table). Read-only.
    """
    # Invalid argument combinations are usage errors (exit 2, ADR 0001), not
    # operational failures — raise UsageError so the CLI's --json usage envelope
    # handles them consistently.
    if application and entity:
        raise click.UsageError("pass either ENTITY or --application, not both")
    if not application and not entity:
        raise click.UsageError("ENTITY is required unless --application is given")
    label = {"application": True} if application else {"entity": entity}
    if ctx.dry_run:
        path = ("RetrieveApplicationRibbon()" if application
                else f"RetrieveEntityRibbon(EntityName={odata_literal(entity)},"
                     "RibbonLocationFilter='All')")
        ctx.emit(True, data=ctx.backend().get(path))
        return
    try:
        root = (ribbon_mod.retrieve_application_ribbon(ctx.backend()) if application
                else ribbon_mod.retrieve_entity_ribbon(ctx.backend(), entity))
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    except ValueError as exc:
        ctx.emit(False, error=str(exc))
        return
    pretty = minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ")
    if output:
        try:
            Path(output).write_text(pretty, encoding="utf-8")
        except OSError as exc:
            ctx.emit(False, error=f"Could not write {output}: {exc}")
            return
        ctx.emit(True, data={**label, "output": output})
    elif ctx.json_mode:
        ctx.emit(True, data={**label, "ribbonxml": pretty})
    else:
        click.echo(pretty)


def _load_solution_ribbon_diff(ctx: CLIContext, solution: str, entity: str):
    """Export the solution and return (cust_root, entity_node, ribbon_diff)."""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "export.zip"
        ribbon_mod.export_solution(ctx.backend(), solution, src,
                                   export_customizations=True)
        with zipfile.ZipFile(src) as z:
            cust_root = ET.fromstring(z.read("customizations.xml"))
    entity_node = ribbon_mod.find_entity_node(cust_root, entity)
    diff = ribbon_mod.get_or_create_ribbon_diff(entity_node)
    return cust_root, entity_node, diff


@ribbon_group.command("list")
@click.argument("entity")
@_solution_option
@pass_ctx
def ribbon_list(ctx: CLIContext, entity, solution):
    """List the custom buttons declared in a solution's RibbonDiffXml."""
    solution, warning = _resolve_solution(ctx, solution)
    assert solution is not None  # require=True: _resolve_solution raised on no-resolve
    if ctx.dry_run:
        with d365_errors(ctx):
            with tempfile.TemporaryDirectory() as td:
                preview = ribbon_mod.export_solution(
                    ctx.backend(), solution, Path(td) / "dry.zip",
                    export_customizations=True)
        ctx.emit(True, data=preview, warnings=[warning] if warning else None)
        return
    try:
        _, _, diff = _load_solution_ribbon_diff(ctx, solution, entity)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    except ValueError as exc:
        ctx.emit(False, error=str(exc))
        return
    buttons = ribbon_mod.list_custom_buttons(diff)
    rows = [[b.button_id, b.label, b.location, b.command, b.function, b.library]
            for b in buttons]
    ctx.emit(True, data=[b.__dict__ for b in buttons], table={
        "headers": ["button-id", "label", "location", "command",
                    "function", "library"],
        "rows": rows,
    }, warnings=[warning] if warning else None)


@ribbon_group.command("add-button")
@click.argument("entity")
@click.option("--label", required=True, help="Button label text.")
@click.option("--location", required=True,
              type=click.Choice(["form", "homegrid", "subgrid"]),
              help="Where the button appears.")
@click.option("--group", "group_override", default=None,
              help="Override the target ribbon group id.")
@click.option("--webresource", required=True,
              help="JS web resource name, e.g. 'cwx_/scripts/x.js'.")
@click.option("--function", required=True,
              help="JavaScript function name, e.g. 'ns.fn'.")
@click.option("--param", required=True,
              type=click.Choice(["PrimaryControl", "SelectedControlSelectedItemIds"]),
              help="CrmParameter passed to the function.")
@click.option("--sequence", type=int, default=50, show_default=True)
@click.option("--id", "id_base", default=None,
              help="Override the generated id base ({entity}.{location}.{label}).")
@_solution_option
@pass_ctx
def ribbon_add_button(ctx, entity, label, location, group_override, webresource,
                      function, param, sequence, id_base, solution):
    """Add a JavaScript command-bar button to an entity (no manual XML editing)."""
    solution, warning = _resolve_solution(ctx, solution)
    assert solution is not None  # require=True: _resolve_solution raised on no-resolve
    try:
        ribbon_mod.resolve_webresource_id(ctx.backend(), webresource)
    except (D365Error, ValueError) as exc:
        if isinstance(exc, D365Error):
            _handle_d365_error(ctx, exc)
        else:
            ctx.emit(False, error=str(exc))
        return

    group = ribbon_mod.resolve_group(location, entity, group_override)
    ids = ribbon_mod.build_button_ids(entity, location, label, id_base)

    def mutate(cust_root):
        node = ribbon_mod.find_entity_node(cust_root, entity)
        diff = ribbon_mod.get_or_create_ribbon_diff(node)
        ribbon_mod.add_custom_action(
            diff, ids=ids, group=group, label=label, webresource=webresource,
            function=function, param=param, sequence=sequence)

    try:
        result = ribbon_mod.apply_ribbon_change(
            ctx.backend(), solution=solution, entity=entity, mutate=mutate)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    except ValueError as exc:
        ctx.emit(False, error=str(exc))
        return
    ctx.emit(True, data={"button_id": ids.custom_action, "group": group,
                         "result": result},
             warnings=[warning] if warning else None)
    _journal(ctx, ids.custom_action, result, solution=solution)


@ribbon_group.command("remove")
@click.argument("entity")
@click.option("--button-id", "button_id", required=True,
              help="The CustomAction Id to remove (see `crm ribbon list`).")
@_destructive_option
@_solution_option
@pass_ctx
def ribbon_remove(ctx, entity, button_id, yes, solution):
    """Remove a custom button (CustomAction + its CommandDefinition)."""
    solution, warning = _resolve_solution(ctx, solution)
    assert solution is not None  # require=True: _resolve_solution raised on no-resolve
    _confirm_destructive(ctx, "ribbon button", button_id, yes)

    def mutate(cust_root):
        node = ribbon_mod.find_entity_node(cust_root, entity)
        diff = ribbon_mod.get_or_create_ribbon_diff(node)
        if not ribbon_mod.remove_custom_action(diff, button_id):
            available = [b.button_id
                         for b in ribbon_mod.list_custom_buttons(diff)]
            raise ValueError(
                f"button-id {button_id!r} not found; available: {available}")

    try:
        result = ribbon_mod.apply_ribbon_change(
            ctx.backend(), solution=solution, entity=entity, mutate=mutate)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    except ValueError as exc:
        ctx.emit(False, error=str(exc))
        return
    ctx.emit(True, data={"removed": button_id, "result": result},
             warnings=[warning] if warning else None)
    _journal(ctx, button_id, result, solution=solution)


@ribbon_group.command("set-label")
@click.argument("entity")
@click.option("--button-id", "button_id", required=True,
              help="The custom button's CustomAction Id (see `crm ribbon list`).")
@click.option("--label", default=None, help="New button LabelText.")
@click.option("--tooltip-title", "tooltip_title", default=None,
              help="New button ToolTipTitle.")
@click.option("--tooltip-description", "tooltip_description", default=None,
              help="New button ToolTipDescription.")
@click.option("--lcid", type=int, default=None,
              help="Localize the text for this language (LCID) via a $LocLabels "
                   "directive instead of setting it inline. Validated against the "
                   "org's provisioned languages.")
@_publish_option
@_solution_option
@pass_ctx
def ribbon_set_label(ctx, entity, button_id, label, tooltip_title,
                     tooltip_description, lcid, publish, solution):
    """Set a custom command-bar button's label and tooltips.

    Touches only LabelText / ToolTipTitle / ToolTipDescription — the button's
    Command, TemplateAlias, Sequence and Id are protected. Pass at least one of
    --label / --tooltip-title / --tooltip-description. With --lcid the text is
    localized through a CASE-SENSITIVE `$LocLabels:<id>` directive (the text lands
    in a <Title languagecode=LCID> row), so it can be re-run per language; without
    --lcid the text is set inline. Text is XML-escaped automatically.
    """
    solution, warning = _resolve_solution(ctx, solution)
    assert solution is not None  # require=True: _resolve_solution raised on no-resolve
    publish = _resolve_publish(ctx, publish)
    if label is None and tooltip_title is None and tooltip_description is None:
        raise click.UsageError(
            "pass at least one of --label / --tooltip-title / --tooltip-description")

    if lcid is not None:
        try:
            provisioned = ribbon_mod.retrieve_provisioned_languages(ctx.backend())
        except D365Error as exc:
            _handle_d365_error(ctx, exc)
            return
        except ValueError as exc:
            ctx.emit(False, error=str(exc))
            return
        if lcid not in provisioned:
            ctx.emit(False, error=(
                f"--lcid {lcid} is not provisioned on this org; "
                f"provisioned languages: {sorted(provisioned)}"))
            return

    def mutate(cust_root):
        node = ribbon_mod.find_entity_node(cust_root, entity)
        diff = ribbon_mod.get_or_create_ribbon_diff(node)
        ribbon_mod.set_button_label(
            diff, button_id=button_id, label=label, tooltip_title=tooltip_title,
            tooltip_description=tooltip_description, lcid=lcid)

    try:
        result = ribbon_mod.apply_ribbon_change(
            ctx.backend(), solution=solution, entity=entity, mutate=mutate,
            publish=publish)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    except ValueError as exc:
        ctx.emit(False, error=str(exc))
        return
    ctx.emit(True, data={"button_id": button_id, "label": label,
                         "tooltip_title": tooltip_title,
                         "tooltip_description": tooltip_description,
                         "lcid": lcid, "result": result},
             warnings=[warning] if warning else None)
    _journal(ctx, button_id, result, solution=solution)


@ribbon_group.command("hide-button")
@click.argument("entity")
@click.option("--target-id", "target_id", required=True,
              help="The OOB button (control) Id to hide, as it appears in "
                   "`crm ribbon export ENTITY`.")
@click.option("--method", type=click.Choice(["display-rule", "hide-action"]),
              default="display-rule", show_default=True,
              help="display-rule: reversible (override the command with two "
                   "always-false DisplayRules). hide-action: HideCustomAction, a "
                   "one-way trapdoor removable only by a new solution version.")
@_destructive_option
@_publish_option
@_solution_option
@pass_ctx
def ribbon_hide_button(ctx, entity, target_id, method, yes, publish,
                       solution):
    """Hide an out-of-box command-bar button (reversibly by default).

    Validates --target-id against the live composed ribbon so a typo errors instead
    of silently doing nothing. Never touches the button's classid/Command/
    TemplateAlias. `display-rule` overrides the button's command with two
    always-false platform DisplayRules; `hide-action` writes a HideCustomAction,
    which is irreversible without a new solution version and is gated behind --yes.
    """
    solution, warning = _resolve_solution(ctx, solution)
    assert solution is not None  # require=True: _resolve_solution raised on no-resolve
    publish = _resolve_publish(ctx, publish)

    # T2: resolve --target-id in the live composed ribbon; a typo must error here,
    # not silently no-op after a full export/import round-trip (#1 ribbon defect).
    try:
        composed = ribbon_mod.retrieve_entity_ribbon(ctx.backend(), entity)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    except ValueError as exc:
        ctx.emit(False, error=str(exc))
        return
    element = ribbon_mod.find_composed_element(composed, target_id)
    if element is None:
        ctx.emit(False, error=(
            f"target-id {target_id!r} not found in the composed ribbon for "
            f"{entity!r}; check `crm ribbon export {entity}`"))
        return
    command_id = element.get("Command")
    if method == "display-rule" and not command_id:
        ctx.emit(False, error=(
            f"target-id {target_id!r} has no Command to override; use "
            "--method hide-action to hide this element"))
        return

    if method == "hide-action":
        _confirm_destructive(
            ctx, "ribbon element", target_id, yes,
            message=(f"HideCustomAction on {target_id!r} is IRREVERSIBLE without a "
                     "new solution version. Continue?"))

    def mutate(cust_root):
        node = ribbon_mod.find_entity_node(cust_root, entity)
        diff = ribbon_mod.get_or_create_ribbon_diff(node)
        if method == "display-rule":
            assert command_id is not None  # guarded above
            ribbon_mod.hide_button_display_rule(diff, command_id)
        else:
            ribbon_mod.hide_button_hide_action(diff, target_id)

    try:
        result = ribbon_mod.apply_ribbon_change(
            ctx.backend(), solution=solution, entity=entity, mutate=mutate,
            publish=publish)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    except ValueError as exc:
        ctx.emit(False, error=str(exc))
        return
    warnings = [_OOB_REUSE_WARNING]
    if warning:
        warnings.append(warning)
    ctx.emit(True, data={"hidden": target_id, "method": method,
                         "command": command_id, "result": result},
             warnings=warnings)
    _journal(ctx, target_id, result, solution=solution)


@ribbon_group.command("set-rules")
@click.argument("entity")
@click.option("--command-id", "command_id", required=True,
              help="The CommandDefinition Id whose rules to set (see `crm ribbon list`).")
@click.option("--enable-rule", "enable_rules", multiple=True, metavar="RULE_ID",
              help="Enable-rule id to reference (repeatable). Replaces the command's "
                   "enable rules with exactly these, in order.")
@click.option("--display-rule", "display_rules", multiple=True, metavar="RULE_ID",
              help="Display-rule id to reference (repeatable). Replaces the command's "
                   "display rules with exactly these, in order.")
@_publish_option
@_solution_option
@pass_ctx
def ribbon_set_rules(ctx, entity, command_id, enable_rules, display_rules,
                     publish, solution):
    """Set the enable/display rule references on a command's CommandDefinition.

    Each rule id is a platform rule (validated against a curated `Mscrm.*`
    allow-list) or a custom rule (e.g. one added with `ribbon add-custom-rule`).
    The CommandDefinition Id is never touched.
    """
    if not enable_rules and not display_rules:
        raise click.UsageError("pass at least one --enable-rule or --display-rule")
    solution, warning = _resolve_solution(ctx, solution)
    assert solution is not None  # require=True: _resolve_solution raised on no-resolve
    publish = _resolve_publish(ctx, publish)
    try:
        ribbon_mod.validate_rule_ids(enable_rules, kind="enable")
        ribbon_mod.validate_rule_ids(display_rules, kind="display")
    except ValueError as exc:
        ctx.emit(False, error=str(exc))
        return

    warnings = [warning] if warning else []
    if ribbon_mod.is_oob_command(command_id):
        warnings.append(_OOB_REUSE_WARNING)

    def mutate(cust_root):
        node = ribbon_mod.find_entity_node(cust_root, entity)
        diff = ribbon_mod.get_or_create_ribbon_diff(node)
        ribbon_mod.set_command_rules(
            diff, command_id=command_id,
            enable_rules=enable_rules, display_rules=display_rules)

    try:
        result = ribbon_mod.apply_ribbon_change(
            ctx.backend(), solution=solution, entity=entity, mutate=mutate,
            publish=publish)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    except ValueError as exc:
        ctx.emit(False, error=str(exc))
        return
    ctx.emit(True, data={"command_id": command_id,
                         "enable_rules": list(enable_rules),
                         "display_rules": list(display_rules),
                         "result": result},
             warnings=warnings or None)
    _journal(ctx, command_id, result, solution=solution)


@ribbon_group.command("add-custom-rule")
@click.argument("entity")
@click.option("--command-id", "command_id", required=True,
              help="The CommandDefinition Id to attach the rule to.")
@click.option("--webresource", required=True,
              help="JS web resource holding the rule function, e.g. 'cwx_/scripts/x.js'.")
@click.option("--function", required=True,
              help="JavaScript function returning bool/Promise, e.g. 'ns.canRun'.")
@_publish_option
@_solution_option
@pass_ctx
def ribbon_add_custom_rule(ctx, entity, command_id, webresource, function,
                           publish, solution):
    """Add a custom (JavaScript) enable rule to a command and reference it.

    Defines an EnableRule whose CustomRule calls the given web-resource function,
    then references it on the command. The web resource must already exist. The
    CommandDefinition Id is never touched.
    """
    solution, warning = _resolve_solution(ctx, solution)
    assert solution is not None  # require=True: _resolve_solution raised on no-resolve
    publish = _resolve_publish(ctx, publish)
    try:
        ribbon_mod.resolve_webresource_id(ctx.backend(), webresource)
        rule_id = ribbon_mod.build_custom_rule_id(command_id, function)
    except (D365Error, ValueError) as exc:
        if isinstance(exc, D365Error):
            _handle_d365_error(ctx, exc)
        else:
            ctx.emit(False, error=str(exc))
        return

    warnings = [warning] if warning else []
    if ribbon_mod.is_oob_command(command_id):
        warnings.append(_OOB_REUSE_WARNING)

    def mutate(cust_root):
        node = ribbon_mod.find_entity_node(cust_root, entity)
        diff = ribbon_mod.get_or_create_ribbon_diff(node)
        ribbon_mod.add_custom_rule(
            diff, command_id=command_id, rule_id=rule_id,
            webresource=webresource, function=function)

    try:
        result = ribbon_mod.apply_ribbon_change(
            ctx.backend(), solution=solution, entity=entity, mutate=mutate,
            publish=publish)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    except ValueError as exc:
        ctx.emit(False, error=str(exc))
        return
    ctx.emit(True, data={"command_id": command_id, "rule_id": rule_id,
                         "result": result},
             warnings=warnings or None)
    _journal(ctx, rule_id, result, solution=solution)
