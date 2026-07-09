"""Entity ribbon (command-bar) commands — issue #142."""
# pyright: basic
from __future__ import annotations
import tempfile
import xml.dom.minidom as minidom
import xml.etree.ElementTree as ET
from pathlib import Path
import click
from crm.core import ribbon as ribbon_mod
from crm.utils.d365_backend import D365Error, odata_literal
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _destructive_option,
    _journal, _confirm_destructive,
    _solution_option, _resolve_solution, d365_errors,
    _output_option, _publish_option, _resolve_publish,
)

# OOB ribbon commands are Microsoft-published; reusing or overriding them is
# outside Microsoft's supported customization surface (it can break on platform
# updates). We still allow it — both hide methods are documented — but warn.
_OOB_REUSE_WARNING = (
    "Overriding or hiding an out-of-box ribbon command is on unsupported ground "
    "and may change across platform updates.")


def _icon_options(fn):
    """Attach the three button-icon flags (issue #679) to a command.

    Shared by `add-button` and `set-icon` so the flag names/help stay identical.
    Each takes a plain web-resource name; the CLI writes the `$webresource:`
    directive and validates existence + slot type up front (`set-icon`/`add-button`).
    """
    fn = click.option(
        "--image32", default=None,
        help="Web resource (32×32 raster: PNG/JPG/GIF/ICO) for the classic "
             "Image32by32 icon.")(fn)
    fn = click.option(
        "--image16", default=None,
        help="Web resource (16×16 raster: PNG/JPG/GIF/ICO) for the classic "
             "Image16by16 icon.")(fn)
    fn = click.option(
        "--modern-image", "modern_image", default=None,
        help="Web resource (SVG) for the Unified Interface ModernImage icon.")(fn)
    return fn


def _validate_icons(ctx, modern_image, image16, image32):
    """Validate each provided icon web resource (existence + slot type) up front.

    Raises D365Error (operational failure, exit 1 per ADR 0001) on a missing or
    wrong-type reference — before the slow solution export/import round-trip. Call
    inside a `d365_errors(ctx)` block."""
    for slot, name in (("modern_image", modern_image), ("image16", image16),
                       ("image32", image32)):
        if name is not None:
            ribbon_mod.validate_icon_webresource(ctx.backend(), slot=slot, name=name)


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
        with d365_errors(ctx):
            payload = ctx.backend().get(path)
        ctx.emit(True, data=payload)
        return
    with d365_errors(ctx):
        root = (ribbon_mod.retrieve_application_ribbon(ctx.backend()) if application
                else ribbon_mod.retrieve_entity_ribbon(ctx.backend(), entity))
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


@ribbon_group.command("list")
@click.argument("entity")
@_solution_option
@pass_ctx
def ribbon_list(ctx: CLIContext, entity, solution):
    """List the custom buttons declared in a solution's RibbonDiffXml."""
    solution = _resolve_solution(ctx, solution)
    if ctx.dry_run:
        with d365_errors(ctx):
            with tempfile.TemporaryDirectory() as td:
                preview = ribbon_mod.export_solution(
                    ctx.backend(), solution, Path(td) / "dry.zip",
                    export_customizations=True)
        ctx.emit(True, data=preview, warnings=None)
        return
    with d365_errors(ctx):
        diff = ribbon_mod.load_solution_ribbon_diff(ctx.backend(), solution, entity)
    buttons = ribbon_mod.list_custom_buttons(diff)
    rows = [[b.button_id, b.label, b.location, b.command, b.function, b.library]
            for b in buttons]
    ctx.emit(True, data=[b.__dict__ for b in buttons], table={
        "headers": ["button-id", "label", "location", "command",
                    "function", "library"],
        "rows": rows,
    }, warnings=None)


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
@_icon_options
@_publish_option
@_solution_option
@pass_ctx
def ribbon_add_button(ctx, entity, label, location, group_override, webresource,
                      function, param, sequence, id_base, modern_image, image16,
                      image32, publish, solution):
    """Add a JavaScript command-bar button to an entity (no manual XML editing).

    Optionally set the button's icon at creation: --modern-image (SVG web resource,
    Unified Interface) and/or --image16 / --image32 (raster web resources, classic
    UI). Each is written as a $webresource: reference (which adds a solution
    dependency on the icon) and validated for existence + type before the import.
    """
    solution = _resolve_solution(ctx, solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        ribbon_mod.resolve_webresource_id(ctx.backend(), webresource)
        _validate_icons(ctx, modern_image, image16, image32)
        group = ribbon_mod.resolve_group(location, entity, group_override)
        ids = ribbon_mod.build_button_ids(entity, location, label, id_base)

        def mutate(diff):
            ribbon_mod.add_custom_action(
                diff, ids=ids, group=group, label=label, webresource=webresource,
                function=function, param=param, sequence=sequence,
                modern_image=modern_image, image16=image16, image32=image32)

        result = ribbon_mod.apply_ribbon_change(
            ctx.backend(), solution=solution, entity=entity, mutate=mutate,
            publish=publish)
    ctx.emit(True, data={"button_id": ids.custom_action, "group": group,
                         "result": result},
             warnings=None)
    _journal(ctx, ids.custom_action, result, solution=solution)


@ribbon_group.command("remove")
@click.argument("entity")
@click.option("--button-id", "button_id", required=True,
              help="The CustomAction Id to remove (see `crm ribbon list`).")
@_destructive_option
@_publish_option
@_solution_option
@pass_ctx
def ribbon_remove(ctx, entity, button_id, yes, publish, solution):
    """Remove a custom button (CustomAction + its CommandDefinition)."""
    solution = _resolve_solution(ctx, solution)
    publish = _resolve_publish(ctx, publish)
    _confirm_destructive(ctx, "ribbon button", button_id, yes)

    def mutate(diff):
        if not ribbon_mod.remove_custom_action(diff, button_id):
            available = [b.button_id
                         for b in ribbon_mod.list_custom_buttons(diff)]
            raise D365Error(
                f"button-id {button_id!r} not found; available: {available}")

    with d365_errors(ctx):
        result = ribbon_mod.apply_ribbon_change(
            ctx.backend(), solution=solution, entity=entity, mutate=mutate,
            publish=publish)
    ctx.emit(True, data={"removed": button_id, "result": result},
             warnings=None)
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
    solution = _resolve_solution(ctx, solution)
    publish = _resolve_publish(ctx, publish)
    if label is None and tooltip_title is None and tooltip_description is None:
        raise click.UsageError(
            "pass at least one of --label / --tooltip-title / --tooltip-description")

    if lcid is not None:
        with d365_errors(ctx):
            provisioned = ribbon_mod.retrieve_provisioned_languages(ctx.backend())
        if lcid not in provisioned:
            ctx.emit(False, error=(
                f"--lcid {lcid} is not provisioned on this org; "
                f"provisioned languages: {sorted(provisioned)}"))
            return

    def mutate(diff):
        ribbon_mod.set_button_label(
            diff, button_id=button_id, label=label, tooltip_title=tooltip_title,
            tooltip_description=tooltip_description, lcid=lcid)

    with d365_errors(ctx):
        result = ribbon_mod.apply_ribbon_change(
            ctx.backend(), solution=solution, entity=entity, mutate=mutate,
            publish=publish)
    ctx.emit(True, data={"button_id": button_id, "label": label,
                         "tooltip_title": tooltip_title,
                         "tooltip_description": tooltip_description,
                         "lcid": lcid, "result": result},
             warnings=None)
    _journal(ctx, button_id, result, solution=solution)


@ribbon_group.command("set-icon")
@click.argument("entity")
@click.option("--button-id", "button_id", required=True,
              help="The custom button's CustomAction Id (see `crm ribbon list`).")
@_icon_options
@_publish_option
@_solution_option
@pass_ctx
def ribbon_set_icon(ctx, entity, button_id, modern_image, image16, image32,
                    publish, solution):
    """Set a custom command-bar button's icon on an existing button.

    Writes only the icon attributes — ModernImage (--modern-image, an SVG web
    resource for the Unified Interface) and/or Image16by16 / Image32by32
    (--image16 / --image32, raster web resources for the classic UI). The button's
    Command, LabelText, TemplateAlias, Sequence and Id are protected. Pass at least
    one icon flag. Each reference is validated (exists + slot type) before the
    solution round-trip, and written as a $webresource: directive so the solution
    gains a dependency on the icon web resource.
    """
    solution = _resolve_solution(ctx, solution)
    publish = _resolve_publish(ctx, publish)
    if modern_image is None and image16 is None and image32 is None:
        raise click.UsageError(
            "pass at least one of --modern-image / --image16 / --image32")

    with d365_errors(ctx):
        _validate_icons(ctx, modern_image, image16, image32)

        def mutate(diff):
            ribbon_mod.set_button_icon(
                diff, button_id=button_id, modern_image=modern_image,
                image16=image16, image32=image32)

        result = ribbon_mod.apply_ribbon_change(
            ctx.backend(), solution=solution, entity=entity, mutate=mutate,
            publish=publish)
    ctx.emit(True, data={"button_id": button_id, "modern_image": modern_image,
                         "image16": image16, "image32": image32,
                         "result": result},
             warnings=None)
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
    solution = _resolve_solution(ctx, solution)
    publish = _resolve_publish(ctx, publish)

    # T2: resolve --target-id in the live composed ribbon; a typo must error here,
    # not silently no-op after a full export/import round-trip (#1 ribbon defect).
    with d365_errors(ctx):
        composed = ribbon_mod.retrieve_entity_ribbon(ctx.backend(), entity)
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

    def mutate(diff):
        if method == "display-rule":
            assert command_id is not None  # guarded above
            ribbon_mod.hide_button_display_rule(diff, command_id)
        else:
            ribbon_mod.hide_button_hide_action(diff, target_id)

    with d365_errors(ctx):
        result = ribbon_mod.apply_ribbon_change(
            ctx.backend(), solution=solution, entity=entity, mutate=mutate,
            publish=publish)
    warnings = [_OOB_REUSE_WARNING]
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
    solution = _resolve_solution(ctx, solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        ribbon_mod.validate_rule_ids(enable_rules, kind="enable")
        ribbon_mod.validate_rule_ids(display_rules, kind="display")

    warnings = []
    if ribbon_mod.is_oob_command(command_id):
        warnings.append(_OOB_REUSE_WARNING)

    def mutate(diff):
        ribbon_mod.set_command_rules(
            diff, command_id=command_id,
            enable_rules=enable_rules, display_rules=display_rules)

    with d365_errors(ctx):
        result = ribbon_mod.apply_ribbon_change(
            ctx.backend(), solution=solution, entity=entity, mutate=mutate,
            publish=publish)
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
    solution = _resolve_solution(ctx, solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        ribbon_mod.resolve_webresource_id(ctx.backend(), webresource)
        rule_id = ribbon_mod.build_custom_rule_id(command_id, function)

    warnings = []
    if ribbon_mod.is_oob_command(command_id):
        warnings.append(_OOB_REUSE_WARNING)

    def mutate(diff):
        ribbon_mod.add_custom_rule(
            diff, command_id=command_id, rule_id=rule_id,
            webresource=webresource, function=function)

    with d365_errors(ctx):
        result = ribbon_mod.apply_ribbon_change(
            ctx.backend(), solution=solution, entity=entity, mutate=mutate,
            publish=publish)
    ctx.emit(True, data={"command_id": command_id, "rule_id": rule_id,
                         "result": result},
             warnings=warnings or None)
    _journal(ctx, rule_id, result, solution=solution)
