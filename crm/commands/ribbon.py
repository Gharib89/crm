"""Entity ribbon (command-bar) commands — issue #142."""
# pyright: basic
from __future__ import annotations
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
