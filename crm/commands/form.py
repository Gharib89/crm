"""Entity form commands — issue #151."""
# pyright: basic
from __future__ import annotations

from pathlib import Path

import click

from crm.core import forms as forms_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error, _journal, _emit_with_warning,
    _solution_option, _require_solution, _resolve_solution, _resolve_publish,
)


@click.group("form")
def form_group():
    """Read and clone entity forms."""


def _resolve_single_form(
    ctx: CLIContext, forms: list[dict], form_name: str
) -> dict | None:
    """Filter forms to exactly one match by name; emit error and return None otherwise."""
    matches = [f for f in forms if f.get("name") == form_name]
    if len(matches) == 0:
        ctx.emit(False, error=f"No form named {form_name!r} found.")
        return None
    if len(matches) > 1:
        details = ", ".join(
            f"formid={m['formid']!r} type={m['type']}" for m in matches
        )
        ctx.emit(False, error=(
            f"Ambiguous: {len(matches)} forms named {form_name!r} — "
            f"cannot pick one automatically. Matches: {details}"
        ))
        return None
    return matches[0]


@form_group.command("list")
@click.argument("entity")
@pass_ctx
def form_list(ctx: CLIContext, entity: str) -> None:
    """List the main forms for an entity."""
    try:
        forms = forms_mod.read_entity_forms(ctx.backend(), entity)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    rows = [
        [f.get("name", ""), f.get("type", ""), f.get("formid", ""),
         str(f.get("isdefault", False))]
        for f in forms
    ]
    ctx.emit(True, data=forms, table={
        "headers": ["name", "type", "formid", "default"],
        "rows": rows,
    })


@form_group.command("clone")
@click.argument("entity")
@click.argument("form_name")
@click.option("--to", "target_entity", required=True,
              help="Target entity logical name.")
@click.option("--publish/--no-publish", default=True,
              help="Run PublishAllXml after creation. Default: publish.")
@_solution_option
@pass_ctx
def form_clone(
    ctx: CLIContext, entity: str, form_name: str, target_entity: str,
    publish: bool, solution: str | None, require_solution: bool,
) -> None:
    """Clone a named form to another entity."""
    solution, warning = _resolve_solution(
        ctx, solution, require=_require_solution(require_solution))
    publish = _resolve_publish(ctx, publish)
    try:
        forms = forms_mod.read_entity_forms(ctx.backend(), entity)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    form = _resolve_single_form(ctx, forms, form_name)
    if form is None:
        return
    try:
        info = forms_mod.clone_form_to_entity(
            ctx.backend(), form, target_entity,
            publish=publish, solution=solution,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning)
    _journal(ctx, "form clone", form_name, info, solution=solution)


@form_group.command("export")
@click.argument("entity")
@click.argument("form_name")
@click.option("--output", type=click.Path(dir_okay=False),
              help="Write the formxml to this file instead of stdout.")
@pass_ctx
def form_export(ctx: CLIContext, entity: str, form_name: str, output: str | None) -> None:
    """Export a form's formxml."""
    try:
        forms = forms_mod.read_entity_forms(ctx.backend(), entity)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    form = _resolve_single_form(ctx, forms, form_name)
    if form is None:
        return
    formxml = form.get("formxml", "")
    if output:
        Path(output).write_text(formxml, encoding="utf-8")
        ctx.emit(True, data={"entity": entity, "form": form_name, "output": output})
    else:
        click.echo(formxml)
