"""Entity CRUD commands."""
# pyright: basic
from __future__ import annotations
from typing import Any
import click
from crm.core import entity as entity_mod
from crm.utils.d365_backend import D365Backend, D365Error, as_dict
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

# D365 error code for alternate-key uniqueness violation (DuplicateRecordEntityKey).
# Live-verified on both on-prem v9.1 and Dataverse cloud v9.2: response body
# {"error": {"code": "0x80060892", ...}} with HTTP 412. Distinct from 0x80040237
# (DuplicateRecord / SQL integrity) and 0x80040333 (duplicate-detection-rules).
_ALT_KEY_ERROR_CODE = "0x80060892"


def _is_alternate_key_error(exc: D365Error) -> bool:
    """Return True when exc is an alternate-key uniqueness violation.

    Checks exc.code first (set correctly after the _parse_response fix).
    Falls back to response_body in case the code was overwritten by an older
    version of the backend or a test that creates exc directly.
    """
    if exc.code == _ALT_KEY_ERROR_CODE:
        return True
    body = exc.response_body
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("code") == _ALT_KEY_ERROR_CODE:
            return True
    return False


def enrich_duplicate_key_error(
    backend: D365Backend,
    entity_set: str,
    payload: dict[str, Any],
    exc: D365Error,
) -> dict[str, Any]:
    """Try to enrich a duplicate-key error with alternate-key metadata.

    Returns a dict suitable for ``extra_meta`` in ``_handle_d365_error``:
    ``{"alternate_keys": [...], "primary_id_hint": "..."}`` on success, or
    ``{}`` if enrichment fails for any reason. The original ``exc`` is never
    masked — all exceptions from the backend are swallowed here.

    Each key entry: ``{name, schema_name, attributes, payload_values}``.
    ``payload_values`` is the plain-name intersection of key attributes with
    ``payload``; lookup columns surfaced as ``field@odata.bind`` are NOT
    matched (v1 limitation — plain names only).
    """
    if not _is_alternate_key_error(exc):
        return {}
    try:
        safe_set = entity_set.replace("'", "''")
        result = as_dict(backend.get(
            "EntityDefinitions",
            params={
                "$select": "LogicalName,PrimaryIdAttribute",
                "$filter": f"EntitySetName eq '{safe_set}'",
            },
        ))
        matches: list[dict[str, Any]] = result.get("value", [])
        if not matches:
            return {}
        logical_name: str = matches[0].get("LogicalName") or ""
        primary_id: str = matches[0].get("PrimaryIdAttribute") or ""
        if not logical_name:
            return {}

        from crm.core import metadata as meta_mod
        keys = meta_mod.list_entity_keys(backend, logical_name)

        enriched: list[dict[str, Any]] = []
        for k in keys:
            key_attrs: list[str] = k["key_attributes"]
            payload_values = {a: payload[a] for a in key_attrs if a in payload}
            enriched.append({
                "name": k["logical_name"],
                "schema_name": k["schema_name"],
                "attributes": key_attrs,
                "payload_values": payload_values,
            })

        out: dict[str, Any] = {"alternate_keys": enriched}
        if primary_id and primary_id in payload:
            out["primary_id_hint"] = (
                f"Payload contains the primary key attribute '{primary_id}'. "
                "The server returns the same error for a primary-key collision."
            )
        return out
    except Exception:
        return {}


def _resolve_return_record(no_return: bool, return_record: bool, *, default: bool) -> bool:
    """Reconcile the symmetric echo flags into the core ``return_record`` bool.

    Both verbs accept ``--no-return`` and ``--return-record``; each keeps its own
    default (create echoes, update is silent). Passing both is a usage error (#230).
    """
    if no_return and return_record:
        raise click.UsageError(
            "--no-return and --return-record are mutually exclusive: one suppresses the "
            "echoed record, the other requests it."
        )
    if no_return:
        return False
    if return_record:
        return True
    return default


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


def _validate_or_emit(
    ctx: CLIContext,
    entity_set: str,
    payload: dict[str, Any],
    *,
    is_create: bool = False,
) -> list[str] | None:
    """Run the pre-write field-name gate (#72, #233).

    Returns a list of warnings (possibly empty) to proceed, or None to abort.
    On a validation miss, emits the failure envelope and returns None. On success
    with a create-path primary-id warning, returns that warning for the caller to
    surface in the final emit.
    """
    try:
        verdict = entity_mod.validate_payload(
            ctx.backend(), entity_set, payload, is_create=is_create
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return None
    if not verdict["ok"]:
        meta = verdict["meta"]
        unknown = ", ".join(meta["unknown_fields"])
        ctx.emit(False, error=f"Unknown field(s) for {entity_set}: {unknown}", meta=meta)
        return None
    return list(verdict.get("meta", {}).get("warnings") or [])


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


@entity_group.command("children")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--non-empty", is_flag=True, default=False,
              help="Drop relationships whose related-record count is 0.")
@click.option("--filter-entities", metavar="REGEX",
              help="Only count child entities whose logical name matches REGEX. "
                   "Applied before querying — fewer requests, not a post-filter.")
@pass_ctx
def entity_children(ctx: CLIContext, entity_set, record_id, non_empty, filter_entities):
    """Per-relationship related-record counts for the 1:N relationships where
    <entity-set> <guid> is the parent. One batched call instead of N counts.

    Each row: child entity logical name, referencing attribute, child entity
    set, and count. Read-only (composes with --dry-run)."""
    try:
        rows = entity_mod.count_children(
            ctx.backend(), entity_set, record_id,
            non_empty=non_empty,
            filter_entities=filter_entities,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=rows)


@entity_group.command("create")
@click.argument("entity_set")
@click.option("--data", "data_json", help="JSON object as string.")
@click.option("--data-file", type=click.Path(exists=True, dir_okay=False),
              help="Path to a JSON file with the record body.")
@click.option("--no-return", is_flag=True, help="Don't request the record back; just GUID.")
@click.option("--return-record", is_flag=True,
              help="Ask server to return the created row (the default for create).")
@click.option("--validate", is_flag=True,
              help="Pre-write field-name check (1-3 metadata GETs); blocks unknown "
                   "fields with did-you-mean. Composable with --dry-run.")
@_admin_header_options
@pass_ctx
def entity_create(ctx: CLIContext, entity_set, data_json, data_file, no_return, return_record,
                  validate, as_user, as_user_object_id, suppress_dup_detection, bypass_plugins):
    """POST a new record."""
    return_record = _resolve_return_record(no_return, return_record, default=True)
    payload = _load_payload(data_json, data_file)
    validate_warnings: list[str] = []
    if validate:
        result_warnings = _validate_or_emit(ctx, entity_set, payload, is_create=True)
        if result_warnings is None:
            return
        validate_warnings = result_warnings
    try:
        result = entity_mod.create(
            ctx.backend(), entity_set, payload,
            return_record=return_record,
            **_admin_kwargs(as_user, as_user_object_id, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        extra_meta = (enrich_duplicate_key_error(ctx.backend(), entity_set, payload, exc)
                      if _is_alternate_key_error(exc) and ctx.json_mode else None)
        _handle_d365_error(ctx, exc, extra_meta=extra_meta, warnings=validate_warnings or None)
        return
    ctx.emit(True, data=result, warnings=validate_warnings or None)
    _journal(ctx, "entity create", entity_set, result)
    _touch_session(ctx, entity_set)


@entity_group.command("update")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--data", "data_json", help="JSON object as string.")
@click.option("--data-file", type=click.Path(exists=True, dir_okay=False))
@click.option("--allow-create", is_flag=True, help="Permit upsert (skip If-Match header).")
@click.option("--return-record", is_flag=True,
              help="Ask server to return the updated row (update is silent by default).")
@click.option("--no-return", is_flag=True,
              help="Don't request the record back (the default for update).")
@click.option("--if-match", "if_match", metavar="ETAG", default=None,
              help='Optimistic concurrency etag. Example (POSIX): --if-match \'W/"123"\'. '
                   'Use --if-match "*" to require any current version.')
@click.option("--validate", is_flag=True,
              help="Pre-write field-name check (1-3 metadata GETs); blocks unknown "
                   "fields with did-you-mean. Composable with --dry-run.")
@_admin_header_options
@pass_ctx
def entity_update(ctx: CLIContext, entity_set, record_id, data_json, data_file, allow_create,
                  return_record, no_return, if_match, validate, as_user, as_user_object_id,
                  suppress_dup_detection, bypass_plugins):
    """PATCH an existing record."""
    if allow_create and if_match:
        raise click.UsageError(
            "--allow-create and --if-match are mutually exclusive: --allow-create permits "
            "upsert (no If-Match), while --if-match enforces optimistic concurrency."
        )
    return_record = _resolve_return_record(no_return, return_record, default=False)
    payload = _load_payload(data_json, data_file)
    if validate and _validate_or_emit(ctx, entity_set, payload) is None:
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
        extra_meta = (enrich_duplicate_key_error(ctx.backend(), entity_set, payload, exc)
                      if _is_alternate_key_error(exc) and ctx.json_mode else None)
        _handle_d365_error(ctx, exc, hint=_metadata_set_hint(entity_set), extra_meta=extra_meta)
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
        # activation' rejection. Gate the lazy workflow import on this exact
        # code so unrelated deletes (incl. common 404s) never pay the import
        # cost or the resolver's extra GET. The literal mirrors
        # workflow.ACTIVATION_DELETE_ERROR_CODE (a fixed D365 server code).
        hint = None
        if exc.code == "0x80045004":
            from crm.core import workflow as workflow_mod
            hint = workflow_mod.activation_delete_hint(ctx.backend(), record_id, exc)
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
