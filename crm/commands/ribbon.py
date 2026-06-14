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
)


@click.group("ribbon")
def ribbon_group():
    """Read and edit entity command-bar (ribbon) buttons."""


@ribbon_group.command("export")
@click.argument("entity")
@click.option("--output", type=click.Path(dir_okay=False),
              help="Write the ribbon XML to this file instead of stdout.")
@pass_ctx
def ribbon_export(ctx: CLIContext, entity, output):
    """Export an entity's composed ribbon as readable XML."""
    if ctx.dry_run:
        ctx.emit(True, data=ctx.backend().get(
            f"RetrieveEntityRibbon(EntityName={odata_literal(entity)},RibbonLocationFilter='All')"))
        return
    try:
        root = ribbon_mod.retrieve_entity_ribbon(ctx.backend(), entity)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    except ValueError as exc:
        ctx.emit(False, error=str(exc))
        return
    pretty = minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ")
    if output:
        Path(output).write_text(pretty, encoding="utf-8")
        ctx.emit(True, data={"entity": entity, "output": output})
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
def ribbon_list(ctx: CLIContext, entity, solution, require_solution):
    """List the custom buttons declared in a solution's RibbonDiffXml."""
    solution, warning = _resolve_solution(ctx, solution, True)
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
                      function, param, sequence, id_base, solution, require_solution):
    """Add a JavaScript command-bar button to an entity (no manual XML editing)."""
    solution, warning = _resolve_solution(ctx, solution, True)
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
def ribbon_remove(ctx, entity, button_id, yes, solution, require_solution):
    """Remove a custom button (CustomAction + its CommandDefinition)."""
    solution, warning = _resolve_solution(ctx, solution, True)
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
