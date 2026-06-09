"""Entity CRUD commands."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import entity as entity_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _handle_d365_error,
    _admin_header_options,
    _admin_kwargs,
    _confirm_destructive,
    _journal,
    _load_payload,
    _prune_annotations,
    _touch_session,
    _parse_expect,
    _check_expectations,
    _emit_expectation_failure,
)

# Metadata entity-sets that reject record-style PATCH; point the user at the
# metadata command group instead (#146d). Matched case-insensitively on the
# collection name that precedes any key, so 'EntityDefinitions(...)' counts.
_METADATA_SETS = frozenset(("entitydefinitions", "attributemetadata"))
_OPTIONSET_SETS = frozenset(("globaloptionsetdefinitions",))


def _metadata_set_hint(entity_set: str) -> str | None:
    """Return a hint for EntityMetadata PATCH operations, None for regular entities."""
    head = entity_set.split("(", 1)[0].strip().lower()
    if head in _OPTIONSET_SETS:
        return ("global option sets are not editable via 'entity update'; use "
                "'crm metadata update-optionset'.")
    if head in _METADATA_SETS or head.endswith("metadata"):
        return ("metadata is not editable via 'entity update'; use "
                "'crm metadata update-entity' / 'crm metadata update-attribute'.")
    return None


@click.group("entity")
def entity_group():
    """Record CRUD against entity sets (accounts, contacts, ...)."""


def _validate_or_emit(ctx: CLIContext, entity_set, payload) -> bool:
    """Run the pre-write field-name gate (#72). Return True to proceed.

    On a validation miss, emits the `{ok:false, meta:{unknown_fields, did_you_mean}}`
    failure envelope (which raises Exit per ADR 0001) and returns False so the
    caller skips the write. A D365Error from the metadata probe is routed through
    the standard error handler.
    """
    try:
        verdict = entity_mod.validate_payload(ctx.backend(), entity_set, payload)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return False
    if verdict["ok"]:
        return True
    meta = verdict["meta"]
    unknown = ", ".join(meta["unknown_fields"])
    ctx.emit(False, error=f"Unknown field(s) for {entity_set}: {unknown}", meta=meta)
    return False


@entity_group.command("get")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--select", multiple=True, help="Repeatable; column names.")
@click.option("--expand", multiple=True, help="Repeatable; navigation properties.")
@click.option("--annotations/--no-annotations", default=True, help="Include formatted values.")
@click.option("--minimal", is_flag=True, default=False,
              help="JSON mode: drop every key containing '@' (OData annotations like "
                   "@odata.etag, *@FormattedValue, *@lookuplogicalname); keeps business "
                   "fields, _*_value lookup GUIDs, and the primary id.")
@click.option("--expect", multiple=True, metavar="ATTR=VALUE",
              help="Repeatable; assert str(record[ATTR]) == VALUE (an absent key "
                   "never matches). Any mismatch exits 1 (the --json envelope "
                   "carries meta {attr, expected, actual}; human mode prints the "
                   "error line); all match exits 0.")
@pass_ctx
def entity_get(ctx: CLIContext, entity_set, record_id, select, expand, annotations, minimal, expect):
    """GET <entity-set> <guid>."""
    # Validate untrusted --expect input before any backend call (house rule):
    # a malformed pair raises UsageError (exit 2) without a round-trip.
    expectations = _parse_expect(expect)
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
    # Verify against the FULL record, before any --minimal projection.
    if expectations:
        miss = _check_expectations(result, expectations)
        if miss is not None:
            _emit_expectation_failure(ctx, miss)
            return
    if minimal and ctx.json_mode and isinstance(result, dict):
        result = _prune_annotations(result)
    ctx.emit(True, data=result)


@entity_group.command("create")
@click.argument("entity_set")
@click.option("--data", "data_json", help="JSON object as string.")
@click.option("--data-file", type=click.Path(exists=True, dir_okay=False),
              help="Path to a JSON file with the record body.")
@click.option("--no-return", is_flag=True, help="Don't request the record back; just GUID.")
@click.option("--validate", is_flag=True,
              help="Pre-write field-name check (1-3 metadata GETs); blocks unknown "
                   "fields with did-you-mean. Composable with --dry-run.")
@_admin_header_options
@pass_ctx
def entity_create(ctx: CLIContext, entity_set, data_json, data_file, no_return, validate,
                  as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """POST a new record."""
    payload = _load_payload(data_json, data_file)
    if validate and not _validate_or_emit(ctx, entity_set, payload):
        return
    try:
        result = entity_mod.create(
            ctx.backend(), entity_set, payload,
            return_record=not no_return,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result)
    _journal(ctx, "entity create", entity_set, result)
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
@click.option("--validate", is_flag=True,
              help="Pre-write field-name check (1-3 metadata GETs); blocks unknown "
                   "fields with did-you-mean. Composable with --dry-run.")
@_admin_header_options
@pass_ctx
def entity_update(ctx: CLIContext, entity_set, record_id, data_json, data_file, allow_create,
                  return_record, if_match, validate, as_user, as_user_object_id,
                  suppress_dup_detection, bypass_plugins):
    """PATCH an existing record."""
    if allow_create and if_match:
        raise click.UsageError(
            "--allow-create and --if-match are mutually exclusive: --allow-create permits "
            "upsert (no If-Match), while --if-match enforces optimistic concurrency."
        )
    payload = _load_payload(data_json, data_file)
    if validate and not _validate_or_emit(ctx, entity_set, payload):
        return
    try:
        result = entity_mod.update(
            ctx.backend(), entity_set, record_id, payload,
            prevent_create=not allow_create,
            return_record=return_record,
            if_match=if_match,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc, hint=_metadata_set_hint(entity_set))
        return
    data = result or {"updated": True, "id": record_id}
    ctx.emit(True, data=data)
    _journal(ctx, "entity update", entity_set, data)


@entity_group.command("upsert")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--data", "data_json", help="JSON object as string.")
@click.option("--data-file", type=click.Path(exists=True, dir_okay=False))
@_admin_header_options
@pass_ctx
def entity_upsert(ctx: CLIContext, entity_set, record_id, data_json, data_file,
                  as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """PATCH with create-if-missing semantics."""
    payload = _load_payload(data_json, data_file)
    try:
        result = entity_mod.upsert(
            ctx.backend(), entity_set, record_id, payload,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    data = result or {"upserted": True, "id": record_id}
    ctx.emit(True, data=data)
    _journal(ctx, "entity upsert", entity_set, data)


@entity_group.command("delete")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--if-match", "if_match", metavar="ETAG", default=None,
              help='Optimistic concurrency etag.')
@click.option("--yes", is_flag=True, help="Skip interactive confirmation.")
@_admin_header_options
@pass_ctx
def entity_delete(ctx: CLIContext, entity_set, record_id, if_match, yes,
                  as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """DELETE a record."""
    if not _confirm_destructive("record", f"{entity_set}({record_id})", yes):
        ctx.emit(False, error="aborted by user")
        return
    try:
        result = entity_mod.delete(
            ctx.backend(), entity_set, record_id,
            if_match=if_match,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        # 0x80045004 is the workflow-specific 'cannot delete a workflow
        # activation' rejection. Import workflow lazily on the error path only,
        # so unrelated `crm entity` subcommands never pay its import cost; gating
        # on the code keeps the resolver's extra GET off every other delete.
        from crm.core import workflow as workflow_mod
        hint = (workflow_mod.activation_delete_hint(ctx.backend(), record_id, exc)
                if exc.code == workflow_mod.ACTIVATION_DELETE_ERROR_CODE else None)
        _handle_d365_error(ctx, exc, hint=hint)
        return
    ctx.emit(True, data=result)
    _journal(ctx, "entity delete", entity_set, result)


@entity_group.command("associate")
@click.argument("target_set")
@click.argument("target_id")
@click.argument("nav")
@click.argument("related_set")
@click.argument("related_id")
@_admin_header_options
@pass_ctx
def entity_associate(ctx: CLIContext, target_set, target_id, nav, related_set, related_id,
                     as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Associate two records via a collection-valued nav property (1:N from one-side or N:N)."""
    try:
        result = entity_mod.associate(
            ctx.backend(), target_set, target_id, nav, related_set, related_id,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result)
    _journal(ctx, "entity associate", target_set, result)


@entity_group.command("disassociate")
@click.argument("target_set")
@click.argument("target_id")
@click.argument("nav")
@click.option("--related-set", help="Required for collection-valued nav properties.")
@click.option("--related-id", help="Required for collection-valued nav properties.")
@_admin_header_options
@pass_ctx
def entity_disassociate(ctx: CLIContext, target_set, target_id, nav, related_set, related_id,
                        as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Disassociate two records. Omit --related-* for single-valued lookups."""
    try:
        result = entity_mod.disassociate(
            ctx.backend(), target_set, target_id, nav,
            related_set=related_set, related_id=related_id,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result)
    _journal(ctx, "entity disassociate", target_set, result)


@entity_group.command("set-lookup")
@click.argument("entity_set")
@click.argument("record_id")
@click.argument("nav")
@click.argument("related_set")
@click.argument("related_id")
@_admin_header_options
@pass_ctx
def entity_set_lookup(ctx: CLIContext, entity_set, record_id, nav, related_set, related_id,
                      as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Set a single-valued lookup via @odata.bind PATCH."""
    try:
        result = entity_mod.set_lookup(
            ctx.backend(), entity_set, record_id, nav, related_set, related_id,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    data = result or {"set": True, "id": record_id, "nav": nav}
    ctx.emit(True, data=data)
    _journal(ctx, "entity set-lookup", entity_set, data)


@entity_group.command("clear-lookup")
@click.argument("entity_set")
@click.argument("record_id")
@click.argument("nav")
@_admin_header_options
@pass_ctx
def entity_clear_lookup(ctx: CLIContext, entity_set, record_id, nav,
                        as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """Clear a single-valued lookup via DELETE /$ref."""
    try:
        result = entity_mod.clear_lookup(
            ctx.backend(), entity_set, record_id, nav,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result)
    _journal(ctx, "entity clear-lookup", entity_set, result)
