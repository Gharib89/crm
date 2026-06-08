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
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error, _journal, _confirm_destructive,
    _solution_option, _require_solution, _resolve_solution,
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
    try:
        root = ribbon_mod.retrieve_entity_ribbon(ctx.backend(), entity)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
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
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    if solution is None:
        ctx.emit(False, error="--solution is required")
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
