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
    _output_option,
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
@click.option(
    "--type", "form_types", multiple=True,
    type=click.Choice(list(forms_mod.FORM_TYPE_BY_NAME), case_sensitive=False),
    help="Form type to list (repeatable). Default: main.")
@click.option("--all", "all_types", is_flag=True,
              help="List every form type (cannot be combined with --type).")
@pass_ctx
def form_list(
    ctx: CLIContext, entity: str, form_types: tuple[str, ...], all_types: bool,
) -> None:
    """List an entity's forms (main forms by default)."""
    if all_types and form_types:
        raise click.UsageError("--all and --type are mutually exclusive.")
    if all_types:
        types = None
    elif form_types:
        types = tuple(forms_mod.FORM_TYPE_BY_NAME[t] for t in form_types)
    else:
        types = (forms_mod.FORM_TYPE_MAIN,)
    with d365_errors(ctx):
        forms = forms_mod.read_entity_forms(ctx.backend(), entity, form_types=types)
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


_event_option = click.option(
    "--event", required=True,
    type=click.Choice(list(forms_mod.EVENT_CHOICES), case_sensitive=False),
    help="Form event to wire the handler to.")
_field_option = click.option(
    "--field",
    help="Attribute whose onchange fires the handler (required for "
         "--event onchange; invalid otherwise).")


@form_group.command("add-library")
@click.argument("entity")
@click.option("--library", required=True,
              help="Unique name of the JS web resource to register (it must "
                   "already exist — the editor never creates it).")
@_form_option
@_publish_option
@_solution_option
@pass_ctx
def form_add_library(
    ctx: CLIContext, entity: str, library: str, form: str | None,
    publish: bool, solution: str | None, require_solution: bool,
) -> None:
    """Register a JS script library on an entity form (idempotent)."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = forms_mod.add_form_library(
            ctx.backend(), entity, library, form=form,
            publish=publish, solution=solution,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, library, info, solution=solution)


@form_group.command("add-handler")
@click.argument("entity")
@_event_option
@click.option("--library", required=True,
              help="Unique name of the JS web resource holding the function "
                   "(it must already exist).")
@click.option("--function", required=True,
              help="JS function to call when the event fires (e.g. App.onLoad).")
@_field_option
@click.option("--param", "params", multiple=True,
              help="Parameter passed to the function (repeatable; emitted as a "
                   "comma-separated list).")
@click.option("--pass-context/--no-pass-context", default=True,
              help="Pass the execution context as the function's first "
                   "parameter. Default: pass.")
@click.option("--enabled/--no-enabled", default=True,
              help="Whether the handler is enabled. Default: enabled.")
@_form_option
@_publish_option
@_solution_option
@pass_ctx
def form_add_handler(
    ctx: CLIContext, entity: str, event: str, library: str, function: str,
    field: str | None, params: tuple[str, ...], pass_context: bool,
    enabled: bool, form: str | None, publish: bool, solution: str | None,
    require_solution: bool,
) -> None:
    """Wire a JS event handler on an entity form (registering its library)."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = forms_mod.add_form_handler(
            ctx.backend(), entity, event=event, function=function, library=library,
            field=field, params=params, pass_context=pass_context, enabled=enabled,
            form=form, publish=publish, solution=solution,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, function, info, solution=solution)


@form_group.command("remove-handler")
@click.argument("entity")
@_event_option
@click.option("--function", required=True,
              help="JS function name of the handler to remove.")
@_field_option
@_form_option
@_publish_option
@_solution_option
@pass_ctx
def form_remove_handler(
    ctx: CLIContext, entity: str, event: str, function: str, field: str | None,
    form: str | None, publish: bool, solution: str | None, require_solution: bool,
) -> None:
    """Remove a JS event handler from an entity form."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    publish = _resolve_publish(ctx, publish)
    with d365_errors(ctx):
        info = forms_mod.remove_form_handler(
            ctx.backend(), entity, event=event, function=function, field=field,
            form=form, publish=publish, solution=solution,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, function, info, solution=solution)


@form_group.command("list-handlers")
@click.argument("entity")
@_form_option
@pass_ctx
def form_list_handlers(ctx: CLIContext, entity: str, form: str | None) -> None:
    """List the JS event handlers wired on an entity form."""
    with d365_errors(ctx):
        info = forms_mod.list_form_handlers(ctx.backend(), entity, form=form)
    handlers = info["handlers"]
    rows = [
        [h["event"], h.get("field") or "", h["function"], h["library"],
         str(h["enabled"]), str(h["pass_context"])]
        for h in handlers
    ]
    # ADR 0008: a list verb puts a bare array in `data`; the resolved-form context
    # (which form was picked) goes to `meta`, mirroring `form list`.
    ctx.emit(True, data=handlers, meta={
        "formid": info["formid"], "form": info["form"],
    }, table={
        "headers": ["event", "field", "function", "library", "enabled",
                    "pass_context"],
        "rows": rows,
    })


@form_group.command("export")
@click.argument("entity")
@click.argument("form_name")
@_output_option(help="Write the formxml to this file instead of stdout.")
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
    elif ctx.json_mode:
        ctx.emit(True, data={"entity": entity, "form": form_name, "formxml": formxml})
    else:
        click.echo(formxml)
