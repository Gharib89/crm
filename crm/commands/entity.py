"""Entity CRUD commands."""
from __future__ import annotations
import click
from crm.core import entity as entity_mod
from crm.core import session as session_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error,
    _admin_header_options,
    _admin_kwargs,
    _load_payload,
    _touch_session,
)


@click.group("entity")
def entity_group():
    """Record CRUD against entity sets (accounts, contacts, ...)."""


@entity_group.command("get")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--select", multiple=True, help="Repeatable; column names.")
@click.option("--expand", multiple=True, help="Repeatable; navigation properties.")
@click.option("--annotations/--no-annotations", default=True, help="Include formatted values.")
@pass_ctx
def entity_get(ctx: CLIContext, entity_set, record_id, select, expand, annotations):
    """GET <entity-set> <guid>."""
    try:
        result = entity_mod.retrieve(
            ctx.backend(), entity_set, record_id,
            select=list(select) or None,
            expand=list(expand) or None,
            include_annotations=annotations,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result)


@entity_group.command("create")
@click.argument("entity_set")
@click.option("--data", "data_json", help="JSON object as string.")
@click.option("--data-file", type=click.Path(exists=True, dir_okay=False),
              help="Path to a JSON file with the record body.")
@click.option("--no-return", is_flag=True, help="Don't request the record back; just GUID.")
@_admin_header_options
@pass_ctx
def entity_create(ctx: CLIContext, entity_set, data_json, data_file, no_return,
                  as_user, suppress_dup_detection, bypass_plugins):
    """POST a new record."""
    payload = _load_payload(data_json, data_file)
    try:
        result = entity_mod.create(
            ctx.backend(), entity_set, payload,
            return_record=not no_return,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result)
    _touch_session(ctx, entity_set)


@entity_group.command("update")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--data", "data_json", help="JSON object as string.")
@click.option("--data-file", type=click.Path(exists=True, dir_okay=False))
@click.option("--allow-create", is_flag=True, help="Permit upsert (skip If-Match header).")
@click.option("--return-record", is_flag=True, help="Ask server to return the updated row.")
@click.option("--if-match", "if_match", metavar="ETAG", default=None,
              help='Optimistic concurrency etag. Example (POSIX): --if-match \'W/"123"\'. '
                   'Use --if-match "*" to require any current version.')
@_admin_header_options
@pass_ctx
def entity_update(ctx: CLIContext, entity_set, record_id, data_json, data_file, allow_create,
                  return_record, if_match, as_user, suppress_dup_detection, bypass_plugins):
    """PATCH an existing record."""
    if allow_create and if_match:
        raise click.UsageError(
            "--allow-create and --if-match are mutually exclusive: --allow-create permits "
            "upsert (no If-Match), while --if-match enforces optimistic concurrency."
        )
    payload = _load_payload(data_json, data_file)
    try:
        result = entity_mod.update(
            ctx.backend(), entity_set, record_id, payload,
            prevent_create=not allow_create,
            return_record=return_record,
            if_match=if_match,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result or {"updated": True, "id": record_id})


@entity_group.command("upsert")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--data", "data_json", help="JSON object as string.")
@click.option("--data-file", type=click.Path(exists=True, dir_okay=False))
@_admin_header_options
@pass_ctx
def entity_upsert(ctx: CLIContext, entity_set, record_id, data_json, data_file,
                  as_user, suppress_dup_detection, bypass_plugins):
    """PATCH with create-if-missing semantics."""
    payload = _load_payload(data_json, data_file)
    try:
        result = entity_mod.upsert(
            ctx.backend(), entity_set, record_id, payload,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result or {"upserted": True, "id": record_id})


@entity_group.command("delete")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--if-match", "if_match", metavar="ETAG", default=None,
              help='Optimistic concurrency etag.')
@click.confirmation_option(prompt="Delete this record?")
@_admin_header_options
@pass_ctx
def entity_delete(ctx: CLIContext, entity_set, record_id, if_match,
                  as_user, suppress_dup_detection, bypass_plugins):
    """DELETE a record."""
    try:
        result = entity_mod.delete(
            ctx.backend(), entity_set, record_id,
            if_match=if_match,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result)


@entity_group.command("associate")
@click.argument("target_set")
@click.argument("target_id")
@click.argument("nav")
@click.argument("related_set")
@click.argument("related_id")
@_admin_header_options
@pass_ctx
def entity_associate(ctx: CLIContext, target_set, target_id, nav, related_set, related_id,
                     as_user, suppress_dup_detection, bypass_plugins):
    """Associate two records via a collection-valued nav property (1:N from one-side or N:N)."""
    try:
        result = entity_mod.associate(
            ctx.backend(), target_set, target_id, nav, related_set, related_id,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result)


@entity_group.command("disassociate")
@click.argument("target_set")
@click.argument("target_id")
@click.argument("nav")
@click.option("--related-set", help="Required for collection-valued nav properties.")
@click.option("--related-id", help="Required for collection-valued nav properties.")
@_admin_header_options
@pass_ctx
def entity_disassociate(ctx: CLIContext, target_set, target_id, nav, related_set, related_id,
                        as_user, suppress_dup_detection, bypass_plugins):
    """Disassociate two records. Omit --related-* for single-valued lookups."""
    try:
        result = entity_mod.disassociate(
            ctx.backend(), target_set, target_id, nav,
            related_set=related_set, related_id=related_id,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result)


@entity_group.command("set-lookup")
@click.argument("entity_set")
@click.argument("record_id")
@click.argument("nav")
@click.argument("related_set")
@click.argument("related_id")
@_admin_header_options
@pass_ctx
def entity_set_lookup(ctx: CLIContext, entity_set, record_id, nav, related_set, related_id,
                      as_user, suppress_dup_detection, bypass_plugins):
    """Set a single-valued lookup via @odata.bind PATCH."""
    try:
        result = entity_mod.set_lookup(
            ctx.backend(), entity_set, record_id, nav, related_set, related_id,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result or {"set": True, "id": record_id, "nav": nav})


@entity_group.command("clear-lookup")
@click.argument("entity_set")
@click.argument("record_id")
@click.argument("nav")
@_admin_header_options
@pass_ctx
def entity_clear_lookup(ctx: CLIContext, entity_set, record_id, nav,
                        as_user, suppress_dup_detection, bypass_plugins):
    """Clear a single-valued lookup via DELETE /$ref."""
    try:
        result = entity_mod.clear_lookup(
            ctx.backend(), entity_set, record_id, nav,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result)
