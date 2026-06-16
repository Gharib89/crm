"""Entity form commands — issue #151."""
# pyright: basic
from __future__ import annotations

from pathlib import Path

import click

from crm.core import forms as forms_mod
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _publish_option,
    d365_errors, _journal, _emit_with_warning,
    _solution_option, _resolve_solution, _resolve_publish,
)

_form_option = click.option(
    "--form", "form",
    help="Target form by name or id (default: the sole main form, or the "
         "primary form if the entity has several).")
_tab_option = click.option(
    "--tab", help="Target tab by name or id (default: the first tab).")
_section_option = click.option(
    "--section", help="Target section by name or id (default: the first section).")


@click.group("form")
def form_group():
    """Read and clone entity forms."""


def _resolve_single_form(
    ctx: CLIContext, forms: list[dict], form_name: str
) -> dict | None:
    """Filter forms to exactly one match by name.

    On 0 or >1 matches, emits the error envelope via ``ctx.emit(False, ...)``,
    which raises ``click.exceptions.Exit`` (per ADR 0001). The ``return None``
    after each is unreachable but kept so the declared ``dict | None`` return
    type holds for pyright; the caller's ``if form is None`` guard mirrors it.
    """
    matches = [f for f in forms if f.get("name") == form_name]
    if len(matches) == 0:
        ctx.emit(False, error=f"No form named {form_name!r} found.")
        return None  # unreachable: emit(False) raises Exit
    if len(matches) > 1:
        details = ", ".join(
            f"formid={m['formid']!r} type={m['type']}" for m in matches
        )
        ctx.emit(False, error=(
            f"Ambiguous: {len(matches)} forms named {form_name!r} — "
            f"cannot pick one automatically. Matches: {details}"
        ))
        return None  # unreachable: emit(False) raises Exit
    return matches[0]


@form_group.command("list")
@click.argument("entity")
@pass_ctx
def form_list(ctx: CLIContext, entity: str) -> None:
    """List the main forms for an entity."""
    with d365_errors(ctx):
        forms = forms_mod.read_entity_forms(ctx.backend(), entity)
    # Project to the list-oriented fields only — read_entity_forms also returns
    # formxml (potentially large) + description/objecttypecode, which would
    # bloat --json output and surprise consumers expecting list columns.
    listed = [
        {"name": f.get("name", ""), "type": f.get("type"),
         "formid": f.get("formid"), "isdefault": bool(f.get("isdefault", False))}
        for f in forms
    ]
    rows = [
        [r["name"], "" if r["type"] is None else r["type"],
         r["formid"] or "", str(r["isdefault"])]
        for r in listed
    ]
    ctx.emit(True, data=listed, table={
        "headers": ["name", "type", "formid", "isdefault"],
        "rows": rows,
    })


@form_group.command("clone")
@click.argument("entity")
@click.argument("form_name")
@click.option("--to", "target_entity", required=True,
              help="Target entity logical name.")
@_publish_option
@_solution_option
@pass_ctx
def form_clone(
    ctx: CLIContext, entity: str, form_name: str, target_entity: str,
    publish: bool, solution: str | None, require_solution: bool,
) -> None:
    """Clone a named form to another entity."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        forms = forms_mod.read_entity_forms(ctx.backend(), entity)
    form = _resolve_single_form(ctx, forms, form_name)
    if form is None:
        return
    with d365_errors(ctx):
        info = forms_mod.clone_form_to_entity(
            ctx.backend(), form, target_entity,
            publish=publish, solution=solution,
        )
    _emit_with_warning(ctx, info, warning,
                       meta=ctx.staged_meta())
    _journal(ctx, form_name, info, solution=solution)


@form_group.command("add-field")
@click.argument("entity")
@click.argument("attribute")
@_form_option
@_tab_option
@_section_option
@_publish_option
@_solution_option
@pass_ctx
def form_add_field(
    ctx: CLIContext, entity: str, attribute: str, form: str | None,
    tab: str | None, section: str | None, publish: bool,
    solution: str | None, require_solution: bool,
) -> None:
    """Add a field to an entity form (resolves the control type from metadata)."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = forms_mod.add_form_field(
            ctx.backend(), entity, attribute, form=form, tab=tab, section=section,
            publish=publish, solution=solution,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, attribute, info, solution=solution)


@form_group.command("remove-field")
@click.argument("entity")
@click.argument("attribute")
@_form_option
@_publish_option
@_solution_option
@pass_ctx
def form_remove_field(
    ctx: CLIContext, entity: str, attribute: str, form: str | None,
    publish: bool, solution: str | None, require_solution: bool,
) -> None:
    """Remove a field from an entity form."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = forms_mod.remove_form_field(
            ctx.backend(), entity, attribute, form=form,
            publish=publish, solution=solution,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, attribute, info, solution=solution)


@form_group.command("set-field")
@click.argument("entity")
@click.argument("attribute")
@_form_option
@_tab_option
@_section_option
@_publish_option
@_solution_option
@pass_ctx
def form_set_field(
    ctx: CLIContext, entity: str, attribute: str, form: str | None,
    tab: str | None, section: str | None, publish: bool,
    solution: str | None, require_solution: bool,
) -> None:
    """Move an existing field to a different tab/section of an entity form."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = forms_mod.set_form_field(
            ctx.backend(), entity, attribute, form=form, tab=tab, section=section,
            publish=publish, solution=solution,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, attribute, info, solution=solution)


@form_group.command("export")
@click.argument("entity")
@click.argument("form_name")
@click.option("--output", type=click.Path(dir_okay=False),
              help="Write the formxml to this file instead of stdout.")
@pass_ctx
def form_export(ctx: CLIContext, entity: str, form_name: str, output: str | None) -> None:
    """Export a form's formxml."""
    with d365_errors(ctx):
        forms = forms_mod.read_entity_forms(ctx.backend(), entity)
    form = _resolve_single_form(ctx, forms, form_name)
    if form is None:
        return
    formxml = form.get("formxml", "")
    if output:
        Path(output).write_text(formxml, encoding="utf-8")
        ctx.emit(True, data={"entity": entity, "form": form_name, "output": output})
    else:
        click.echo(formxml)
