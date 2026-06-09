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


def _read_forms_real(ctx: CLIContext, entity: str) -> list[dict]:
    """Read forms with a real GET even under ``--dry-run``.

    The read is an idempotency probe, not a mutation: the dry-run backend's
    ``request`` returns a preview dict with no ``value`` key, so a dry-run
    read would yield zero forms and make ``clone``/``export`` falsely report
    "No form named …". Force ``dry_run`` off for the GET, then restore it so
    the subsequent clone POST is still previewed (pattern from views.py).
    """
    backend = ctx.backend()
    was_dry = backend.dry_run
    backend.dry_run = False
    try:
        return forms_mod.read_entity_forms(backend, entity)
    finally:
        backend.dry_run = was_dry


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
    try:
        forms = _read_forms_real(ctx, entity)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
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
        forms = _read_forms_real(ctx, entity)
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
    _emit_with_warning(ctx, info, warning,
                       meta={"staged": True} if ctx.stage_only else None)
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
        forms = _read_forms_real(ctx, entity)
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
