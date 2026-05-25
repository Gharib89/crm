"""crm — Click-based CLI + REPL for Dynamics 365 CE on-prem 9.x.

Entry point: `crm` (installed) or `python -m crm`.

Running with no subcommand drops into the REPL. Each command supports `--json` for
machine-readable output. `--dry-run` previews the HTTP request without issuing it.
"""
# pyright: basic

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import click

from crm import __version__
from crm.core import (
    async_ops as async_ops_mod,
    batch as batch_mod,
    connection as conn_mod,
    entity as entity_mod,
    export as export_mod,
    metadata as meta_mod,
    query as query_mod,
    relationships as rel_mod,
    session as session_mod,
    solution as sol_mod,
    workflow as workflow_mod,
)
from crm.utils.d365_backend import (
    ConnectionProfile,
    D365Backend,
    D365Error,
)
from crm.utils.repl_skin import ReplSkin


# ── Output helpers ──────────────────────────────────────────────────────


class CLIContext:
    """Per-invocation state shared across subcommands."""

    def __init__(self):
        self.json_mode: bool = False
        self.dry_run: bool = False
        self.profile_name: str | None = None
        self.password: str | None = None
        self.session_name: str = "default"
        self._backend: D365Backend | None = None
        self._backend_key: tuple[str | None, str | None, bool] | None = None
        self.skin: ReplSkin = ReplSkin("d365", version=__version__)

    def emit(self, ok: bool, data: Any = None, *, error: str | None = None,
             meta: dict | None = None, table: dict | None = None) -> None:
        """Print either a JSON envelope or a human-friendly representation."""
        if self.json_mode:
            envelope: dict[str, Any] = {"ok": ok}
            if data is not None:
                envelope["data"] = _sanitize(data)
            if error:
                envelope["error"] = error
            if meta:
                envelope["meta"] = meta
            click.echo(json.dumps(envelope, indent=2, default=str))
            return

        if not ok:
            self.skin.error(error or "Operation failed.")
            return

        if table:
            headers = table.get("headers", [])
            rows = table.get("rows", [])
            self.skin.table(headers, rows)
            if meta:
                for k, v in meta.items():
                    self.skin.status(k, str(v))
            return

        if isinstance(data, dict) and data:
            for k, v in data.items():
                self.skin.status(k, _short_repr(v))
        elif isinstance(data, list):
            self.skin.info(f"{len(data)} item(s)")
            for item in data[:20]:
                click.echo(f"  - {_short_repr(item)}")
            if len(data) > 20:
                self.skin.hint(f"... {len(data) - 20} more items")
        elif data is not None:
            click.echo(str(data))
        if meta:
            for k, v in meta.items():
                self.skin.status(k, str(v))

    def backend(self) -> D365Backend:
        key = (self.profile_name, self.password, self.dry_run)
        if self._backend is None or self._backend_key != key:
            resolved = conn_mod.resolve_credentials(
                profile_name=self.profile_name,
                password_override=self.password,
            )
            self._backend = D365Backend(
                resolved.profile, resolved.password, dry_run=self.dry_run
            )
            self._backend_key = key
        return self._backend

    def invalidate_backend(self) -> None:
        """Drop the cached D365Backend so the next backend() call rebuilds it.

        Called when the profile changes (`connection connect`/`disconnect`) so
        the REPL stops reusing a backend wired up to a stale profile.
        Also triggers automatically if `profile_name`/`password`/`dry_run` change
        between calls (e.g., root opts re-supplied per REPL line).
        """
        self._backend = None
        self._backend_key = None


pass_ctx = click.make_pass_decorator(CLIContext, ensure=True)


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(x) for x in obj]
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return obj


def _short_repr(v: Any, limit: int = 80) -> str:
    s = json.dumps(v, default=str) if isinstance(v, (dict, list)) else str(v)
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _handle_d365_error(ctx: CLIContext, exc: D365Error) -> None:
    ctx.emit(False, error=str(exc), meta={
        "status": exc.status,
        "code": exc.code,
    })


def _admin_header_options(f):
    """Stack `--as-user`, `--suppress-dup-detection`, `--bypass-plugins` flags on a command."""
    f = click.option(
        "--bypass-plugins", is_flag=True, default=False,
        help="Send MSCRM.BypassCustomPluginExecution: true (requires prvBypassCustomPluginExecution).",
    )(f)
    f = click.option(
        "--suppress-dup-detection", is_flag=True, default=False,
        help="Send MSCRM.SuppressDuplicateDetection: true.",
    )(f)
    f = click.option(
        "--as-user", "as_user", metavar="GUID", default=None,
        help="Impersonate systemuser by GUID via MSCRMCallerID header.",
    )(f)
    return f


def _admin_kwargs(as_user: str | None, suppress_dup_detection: bool,
                  bypass_plugins: bool) -> dict[str, Any]:
    """Resolve admin-header CLI flags into backend kwargs.

    `is_flag` defaults to False (flag absent). To preserve the backend's
    tri-state semantics (None = use env default like CRM_SUPPRESS_DUP /
    CRM_BYPASS_PLUGINS), we forward True only when the flag was actually
    set on the command line; otherwise None lets the backend env default
    take effect.
    """
    return {
        "caller_id": as_user,
        "suppress_duplicate_detection": True if suppress_dup_detection else None,
        "bypass_custom_plugin_execution": True if bypass_plugins else None,
    }


# ── Root group ──────────────────────────────────────────────────────────


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--json", "json_mode", is_flag=True, help="Emit machine-readable JSON output.")
@click.option("--dry-run", is_flag=True, help="Preview HTTP request without issuing it.")
@click.option("--profile", "profile_name", help="Connection profile name (from ~/.crm/profiles).")
@click.option("--password", help="Override password (otherwise read from D365_PASSWORD).")
@click.option("--session", "session_name", default="default", help="Session name.")
@click.version_option(__version__, prog_name="crm")
@click.pass_context
def cli(ctx: click.Context, json_mode: bool, dry_run: bool,
        profile_name: str | None, password: str | None, session_name: str):
    """Stateful CLI for Dynamics 365 CE on-prem 9.x (Web API)."""
    cli_ctx = ctx.ensure_object(CLIContext)
    cli_ctx.json_mode = json_mode
    cli_ctx.dry_run = dry_run
    # Sticky options: in the REPL the same CLIContext is reused across lines, so only
    # overwrite when the user actually supplied the flag — otherwise prior values
    # (e.g., set by `connection connect`) would be wiped on the next bare command.
    if profile_name is not None:
        cli_ctx.profile_name = profile_name
    if password is not None:
        cli_ctx.password = password
    cli_ctx.session_name = session_name

    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


# ── Connection group ────────────────────────────────────────────────────


@cli.group()
def connection():
    """Manage server connection profiles and authentication."""


@connection.command("connect")
@click.option("--url", required=True, help="Server URL, e.g. https://crm.contoso.local/contoso")
@click.option("--username", required=True)
@click.option("--domain", default="", help="AD domain (optional for on-prem with UPN).")
@click.option("--password", "password_opt", help="Password (else read from D365_PASSWORD).")
@click.option("--profile-name", default="default", help="Save under this profile name.")
@click.option("--api-version", default="v9.2")
@click.option("--no-verify-ssl", is_flag=True, help="Skip SSL certificate verification.")
@pass_ctx
def connection_connect(ctx: CLIContext, url, username, domain, password_opt,
                       profile_name, api_version, no_verify_ssl):
    """Save a connection profile and test the credentials with WhoAmI."""
    profile = ConnectionProfile(
        name=profile_name,
        url=url,
        domain=domain,
        username=username,
        api_version=api_version,
        verify_ssl=not no_verify_ssl,
    )
    session_mod.save_profile(profile)
    ctx.profile_name = profile_name
    ctx.password = password_opt or os.environ.get(conn_mod.ENV_PASSWORD, "")
    try:
        backend = D365Backend(profile, ctx.password, dry_run=ctx.dry_run)
        info = conn_mod.test_connection(backend)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return

    state = session_mod.load_session(ctx.session_name)
    state["active_profile"] = profile_name
    session_mod.save_session(state, ctx.session_name)
    ctx.invalidate_backend()
    ctx.emit(True, data=info, meta={"profile": profile_name})


@connection.command("status")
@pass_ctx
def connection_status(ctx: CLIContext):
    """Show the active session + profile (no network call)."""
    state = session_mod.load_session(ctx.session_name)
    profile_name = ctx.profile_name or state.get("active_profile")
    data = {
        "session": ctx.session_name,
        "active_profile": profile_name,
        "current_entity_set": state.get("current_entity_set"),
    }
    if profile_name:
        try:
            p = session_mod.load_profile(profile_name)
            data["profile"] = p.to_dict()
        except FileNotFoundError:
            data["profile_error"] = f"profile {profile_name!r} not found"
    ctx.emit(True, data=data)


@connection.command("whoami")
@pass_ctx
def connection_whoami(ctx: CLIContext):
    """Issue WhoAmI() against the server."""
    try:
        info = conn_mod.whoami(ctx.backend())
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@connection.command("test")
@pass_ctx
def connection_test(ctx: CLIContext):
    """Reachability check: WhoAmI + report API base."""
    try:
        info = conn_mod.test_connection(ctx.backend())
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@connection.command("profiles")
@pass_ctx
def connection_profiles(ctx: CLIContext):
    """List saved profiles."""
    names = session_mod.list_profiles()
    if ctx.json_mode:
        ctx.emit(True, data=names)
        return
    ctx.skin.section("Profiles")
    if not names:
        ctx.skin.hint("(none)")
    for n in names:
        ctx.skin.info(n)


@connection.command("disconnect")
@pass_ctx
def connection_disconnect(ctx: CLIContext):
    """Clear the active profile from the session."""
    state = session_mod.load_session(ctx.session_name)
    state["active_profile"] = None
    session_mod.save_session(state, ctx.session_name)
    # Also clear in-memory state — sticky-options means these would otherwise
    # persist across REPL lines and defeat the disconnect.
    ctx.profile_name = None
    ctx.password = None
    ctx.invalidate_backend()
    ctx.emit(True, data={"disconnected": True})


# ── Entity group ────────────────────────────────────────────────────────


@cli.group()
def entity():
    """Record CRUD against entity sets (accounts, contacts, ...)."""


@entity.command("get")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--select", multiple=True, help="Repeatable; column names.")
@click.option("--expand", multiple=True, help="Repeatable; navigation properties.")
@click.option("--annotations/--no-annotations", default=True, help="Include formatted values.")
@pass_ctx
def entity_get(ctx, entity_set, record_id, select, expand, annotations):
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


@entity.command("create")
@click.argument("entity_set")
@click.option("--data", "data_json", help="JSON object as string.")
@click.option("--data-file", type=click.Path(exists=True, dir_okay=False),
              help="Path to a JSON file with the record body.")
@click.option("--no-return", is_flag=True, help="Don't request the record back; just GUID.")
@_admin_header_options
@pass_ctx
def entity_create(ctx, entity_set, data_json, data_file, no_return,
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


@entity.command("update")
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
def entity_update(ctx, entity_set, record_id, data_json, data_file, allow_create,
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


@entity.command("upsert")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--data", "data_json", help="JSON object as string.")
@click.option("--data-file", type=click.Path(exists=True, dir_okay=False))
@_admin_header_options
@pass_ctx
def entity_upsert(ctx, entity_set, record_id, data_json, data_file,
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


@entity.command("delete")
@click.argument("entity_set")
@click.argument("record_id")
@click.option("--if-match", "if_match", metavar="ETAG", default=None,
              help='Optimistic concurrency etag.')
@click.confirmation_option(prompt="Delete this record?")
@_admin_header_options
@pass_ctx
def entity_delete(ctx, entity_set, record_id, if_match,
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


@entity.command("associate")
@click.argument("target_set")
@click.argument("target_id")
@click.argument("nav")
@click.argument("related_set")
@click.argument("related_id")
@_admin_header_options
@pass_ctx
def entity_associate(ctx, target_set, target_id, nav, related_set, related_id,
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


@entity.command("disassociate")
@click.argument("target_set")
@click.argument("target_id")
@click.argument("nav")
@click.option("--related-set", help="Required for collection-valued nav properties.")
@click.option("--related-id", help="Required for collection-valued nav properties.")
@_admin_header_options
@pass_ctx
def entity_disassociate(ctx, target_set, target_id, nav, related_set, related_id,
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


@entity.command("set-lookup")
@click.argument("entity_set")
@click.argument("record_id")
@click.argument("nav")
@click.argument("related_set")
@click.argument("related_id")
@_admin_header_options
@pass_ctx
def entity_set_lookup(ctx, entity_set, record_id, nav, related_set, related_id,
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


@entity.command("clear-lookup")
@click.argument("entity_set")
@click.argument("record_id")
@click.argument("nav")
@_admin_header_options
@pass_ctx
def entity_clear_lookup(ctx, entity_set, record_id, nav,
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


# ── Query group ─────────────────────────────────────────────────────────


@cli.group()
def query():
    """Run OData and FetchXML queries."""


@query.command("odata")
@click.argument("entity_set")
@click.option("--select", multiple=True)
@click.option("--filter", "filter_", help="OData $filter expression.")
@click.option("--top", type=int)
@click.option("--orderby")
@click.option("--expand", multiple=True)
@click.option("--count", is_flag=True, help="Also request $count.")
@click.option("--page-size", type=int)
@click.option("--annotations/--no-annotations", default=False)
@pass_ctx
def query_odata(ctx, entity_set, select, filter_, top, orderby, expand,
                count, page_size, annotations):
    """OData v4 query over an entity set."""
    try:
        result = query_mod.odata_query(
            ctx.backend(), entity_set,
            select=list(select) or None,
            filter_=filter_,
            top=top,
            orderby=orderby,
            expand=list(expand) or None,
            count=count,
            page_size=page_size,
            include_annotations=annotations,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_query_result(ctx, result, entity_set)
    _touch_session(ctx, entity_set, last_query={"type": "odata", "filter": filter_})


@query.command("fetchxml")
@click.argument("entity_set")
@click.option("--xml", "xml_inline", help="Inline FetchXML string.")
@click.option("--file", "xml_file", type=click.Path(exists=True, dir_okay=False),
              help="Path to a FetchXML file.")
@click.option("--annotations/--no-annotations", default=False)
@pass_ctx
def query_fetchxml(ctx, entity_set, xml_inline, xml_file, annotations):
    """Run a FetchXML query."""
    if xml_inline and xml_file:
        ctx.emit(False, error="Provide --xml or --file, not both.")
        return
    fetch_xml = xml_inline or (Path(xml_file).read_text(encoding="utf-8") if xml_file else None)
    if not fetch_xml:
        ctx.emit(False, error="Either --xml or --file is required.")
        return
    try:
        result = query_mod.fetchxml_query(
            ctx.backend(), entity_set, fetch_xml,
            include_annotations=annotations,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_query_result(ctx, result, entity_set)
    _touch_session(ctx, entity_set, last_query={"type": "fetchxml"})


@query.command("saved")
@click.argument("entity_set")
@click.argument("savedquery_id")
@click.option("--annotations/--no-annotations", default=True)
@click.option("--page-size", type=int)
@pass_ctx
def query_saved(ctx, entity_set, savedquery_id, annotations, page_size):
    """Execute a system view (savedquery) by GUID. Use `--json query odata savedqueries` to discover IDs."""
    try:
        result = query_mod.saved_query(
            ctx.backend(), entity_set, savedquery_id,
            include_annotations=annotations, page_size=page_size,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_query_result(ctx, result, entity_set)


@query.command("user")
@click.argument("entity_set")
@click.argument("userquery_id")
@click.option("--annotations/--no-annotations", default=True)
@click.option("--page-size", type=int)
@pass_ctx
def query_user(ctx, entity_set, userquery_id, annotations, page_size):
    """Execute a saved view (userquery) by GUID."""
    try:
        result = query_mod.user_query(
            ctx.backend(), entity_set, userquery_id,
            include_annotations=annotations, page_size=page_size,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_query_result(ctx, result, entity_set)


def _emit_query_result(ctx: CLIContext, result: dict, entity_set: str) -> None:
    values = result.get("value", []) if isinstance(result, dict) else []
    meta: dict[str, Any] = {"entity_set": entity_set, "count": len(values)}
    if "@odata.count" in (result or {}):
        meta["odata_count"] = result["@odata.count"]
    if "@odata.nextLink" in (result or {}):
        meta["next_link"] = "(present)"
    if ctx.json_mode:
        ctx.emit(True, data=result, meta=meta)
        return
    if not values:
        ctx.skin.info("No results.")
        return
    headers = _infer_columns(values)
    rows = [[_short_repr(rec.get(h, ""), 40) for h in headers] for rec in values[:50]]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta=meta)
    if len(values) > 50:
        ctx.skin.hint(f"... {len(values) - 50} more rows")


def _infer_columns(values: list[dict]) -> list[str]:
    cols: list[str] = []
    seen: set[str] = set()
    for rec in values[:5]:
        for k in rec.keys():
            if k.startswith("@") or k in seen:
                continue
            cols.append(k)
            seen.add(k)
    return cols[:8]


# ── Metadata group ──────────────────────────────────────────────────────


@cli.group()
def metadata():
    """Browse entity / attribute / relationship metadata."""


@metadata.command("entities")
@click.option("--custom-only", is_flag=True)
@click.option("--top", type=int)
@pass_ctx
def metadata_entities(ctx, custom_only, top):
    """List entity definitions."""
    try:
        items = meta_mod.list_entities(ctx.backend(), custom_only=custom_only, top=top)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["LogicalName", "EntitySetName", "SchemaName", "IsCustom"]
    rows = [
        [it.get("LogicalName", ""), it.get("EntitySetName", ""),
         it.get("SchemaName", ""), str(it.get("IsCustomEntity", False))]
        for it in items
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta={"count": len(items)})


@metadata.command("entity")
@click.argument("logical_name")
@pass_ctx
def metadata_entity(ctx, logical_name):
    """Show full entity definition."""
    try:
        info = meta_mod.entity_info(ctx.backend(), logical_name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@metadata.command("attributes")
@click.argument("logical_name")
@pass_ctx
def metadata_attributes(ctx, logical_name):
    """List attributes for an entity."""
    try:
        items = meta_mod.list_attributes(ctx.backend(), logical_name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["LogicalName", "SchemaName", "AttributeType", "IsCustom"]
    rows = [
        [it.get("LogicalName", ""), it.get("SchemaName", ""),
         it.get("AttributeType", ""), str(it.get("IsCustomAttribute", False))]
        for it in items
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta={"count": len(items)})


@metadata.command("attribute")
@click.argument("logical_name")
@click.argument("attribute_name")
@pass_ctx
def metadata_attribute(ctx, logical_name, attribute_name):
    """Show a single attribute definition."""
    try:
        info = meta_mod.attribute_info(ctx.backend(), logical_name, attribute_name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@metadata.command("picklist")
@click.argument("logical_name")
@click.argument("attribute")
@click.option("--no-global", is_flag=True, help="Skip GlobalOptionSet expansion.")
@pass_ctx
def metadata_picklist(ctx, logical_name, attribute, no_global):
    """Retrieve option set values for a picklist / state / status attribute."""
    try:
        info = meta_mod.picklist_options(
            ctx.backend(), logical_name, attribute,
            global_optionset=not no_global,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if ctx.json_mode:
        ctx.emit(True, data=info)
        return
    options = (info.get("OptionSet") or {}).get("Options") or []
    if not options:
        options = (info.get("GlobalOptionSet") or {}).get("Options") or []
    headers = ["Value", "Label"]
    rows = [
        [str(o.get("Value")),
         ((o.get("Label") or {}).get("UserLocalizedLabel") or {}).get("Label", "")]
        for o in options
    ]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"entity": logical_name, "attribute": attribute, "count": len(options)})


@metadata.command("create-entity")
@click.option("--schema-name", required=True,
              help="PascalCase with publisher prefix, e.g. 'new_Project'.")
@click.option("--display", "display_name", required=True,
              help="Singular UI label, e.g. 'Project'.")
@click.option("--display-collection", default=None,
              help="Plural UI label. Defaults to <display>+'s'.")
@click.option("--primary-attr", "primary_attr_schema", default=None,
              help="Schema name of the primary name attribute (default '<prefix>_Name').")
@click.option("--primary-label", "primary_attr_label", default=None,
              help="UI label for primary attribute. Default 'Name'.")
@click.option("--primary-max-length", type=int, default=200,
              help="Max length for primary name string column. Default 200.")
@click.option("--description", default=None)
@click.option("--ownership", type=click.Choice(["UserOwned", "OrganizationOwned"]),
              default="UserOwned")
@click.option("--has-activities", is_flag=True)
@click.option("--has-notes", is_flag=True)
@click.option("--is-activity", is_flag=True,
              help="Create as an activity entity.")
@click.option("--solution", default=None,
              help="Add to a specific solution (uniquename, via MSCRM.SolutionUniqueName).")
@click.option("--publish/--no-publish", default=True,
              help="Run PublishAllXml after creation. Default: publish.")
@pass_ctx
def metadata_create_entity(
    ctx, schema_name, display_name, display_collection, primary_attr_schema,
    primary_attr_label, primary_max_length, description, ownership,
    has_activities, has_notes, is_activity, solution, publish,
):
    """Create a new custom entity (table)."""
    try:
        info = meta_mod.create_entity(
            ctx.backend(),
            schema_name=schema_name,
            display_name=display_name,
            display_collection_name=display_collection,
            primary_attr_schema=primary_attr_schema,
            primary_attr_label=primary_attr_label,
            primary_attr_max_length=primary_max_length,
            description=description,
            ownership=ownership,
            has_activities=has_activities,
            has_notes=has_notes,
            is_activity=is_activity,
            solution=solution,
        )
        if publish and not info.get("_dry_run"):
            from crm.core import solution as sol_mod
            sol_mod.publish_all(ctx.backend())
            info["published"] = True
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@metadata.command("relationships")
@click.argument("logical_name")
@pass_ctx
def metadata_relationships(ctx, logical_name):
    """Show one-to-many + many-to-many relationships."""
    try:
        info = rel_mod.list_relationships(ctx.backend(), logical_name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info, meta={
        "one_to_many": len(info.get("OneToMany", [])),
        "many_to_many": len(info.get("ManyToMany", [])),
    })


# ── Solution group ──────────────────────────────────────────────────────


@cli.group()
def solution():
    """Solution lifecycle (list / info / components / export / import)."""


@solution.command("list")
@click.option("--managed/--unmanaged", default=None, help="Filter by managed flag.")
@pass_ctx
def solution_list(ctx, managed):
    try:
        items = sol_mod.list_solutions(ctx.backend(), managed=managed)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["uniquename", "friendlyname", "version", "ismanaged"]
    rows = [[it.get(h, "") for h in headers] for it in items]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta={"count": len(items)})


@solution.command("info")
@click.argument("unique_name")
@pass_ctx
def solution_info_cmd(ctx, unique_name):
    try:
        info = sol_mod.solution_info(ctx.backend(), unique_name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@solution.command("components")
@click.argument("unique_name")
@pass_ctx
def solution_components_cmd(ctx, unique_name):
    try:
        items = sol_mod.solution_components(ctx.backend(), unique_name)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=items, meta={"count": len(items)})


@contextmanager
def _no_retry_scope(ctx, enabled: bool):
    """Scope CRM_NO_RETRY=1 to the command body and rebuild the cached backend.

    Without rebuilding, D365Backend's retry config (captured at construction)
    misses the flag. Without restoring, the env var leaks into later REPL
    commands.
    """
    if not enabled:
        yield
        return
    prev = os.environ.get("CRM_NO_RETRY")
    os.environ["CRM_NO_RETRY"] = "1"
    ctx.invalidate_backend()
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("CRM_NO_RETRY", None)
        else:
            os.environ["CRM_NO_RETRY"] = prev
        ctx.invalidate_backend()


_EXPORT_SETTING_KEYS: dict[str, str] = {
    "autonumbering":       "export_autonumbering",
    "calendar":            "export_calendar",
    "customizations":      "export_customizations",
    "email-tracking":      "export_email_tracking",
    "general":             "export_general",
    "isv-config":          "export_isv_config",
    "marketing":           "export_marketing",
    "outlook-sync":        "export_outlook_sync",
    "relationship-roles":  "export_relationship_roles",
    "sales":               "export_sales",
}


@solution.command("export")
@click.argument("unique_name")
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False))
@click.option("--managed", is_flag=True)
@click.option(
    "--export-setting",
    "export_settings",
    multiple=True,
    type=click.Choice(sorted(_EXPORT_SETTING_KEYS.keys())),
    help="Repeatable; include a named export setting in the solution payload.",
)
@click.option("--timeout", type=int, default=None,
              help="Async operation timeout in seconds. Overrides profile.async_timeout.")
@click.option("--no-retry", is_flag=True,
              help="Disable the 429/5xx retry loop for this invocation.")
@pass_ctx
def solution_export_cmd(ctx, unique_name, output, managed, export_settings, timeout, no_retry):
    kwargs = {_EXPORT_SETTING_KEYS[name]: True for name in export_settings}
    with _no_retry_scope(ctx, no_retry):
        try:
            info = sol_mod.export_solution(
                ctx.backend(), unique_name, output, managed=managed,
                timeout=timeout, **kwargs,
            )
        except D365Error as exc:
            _handle_d365_error(ctx, exc)
            return
        ctx.emit(True, data=info)


@solution.command("publish-all")
@pass_ctx
def solution_publish_all(ctx):
    """Call PublishAllXml — publish every unpublished customization."""
    try:
        result = sol_mod.publish_all(ctx.backend())
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result or {"published": True})


@solution.command("publish")
@click.option("--xml", "parameter_xml", help="Inline Publish Request Schema XML.")
@click.option("--xml-file", type=click.Path(exists=True, dir_okay=False),
              help="Path to a Publish Request Schema XML file.")
@pass_ctx
def solution_publish(ctx, parameter_xml, xml_file):
    """Call PublishXml with a Publish Request Schema XML payload."""
    if parameter_xml and xml_file:
        ctx.emit(False, error="Provide --xml or --xml-file, not both.")
        return
    if xml_file:
        parameter_xml = Path(xml_file).read_text(encoding="utf-8")
    if not parameter_xml:
        ctx.emit(False, error="Either --xml or --xml-file is required.")
        return
    try:
        result = sol_mod.publish_xml(ctx.backend(), parameter_xml)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result or {"published": True})


@solution.command("job-status")
@click.argument("async_operation_id")
@pass_ctx
def solution_job_status(ctx, async_operation_id):
    """Alias for `crm async get <id>` — inspect a solution import/export job."""
    try:
        row = async_ops_mod.get_async_operation(ctx.backend(), async_operation_id)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=row)


@solution.command("job-cancel")
@click.argument("async_operation_id")
@click.confirmation_option(prompt="Cancel this job?")
@pass_ctx
def solution_job_cancel(ctx, async_operation_id):
    """Alias for `crm async cancel <id>`."""
    try:
        async_ops_mod.cancel_async_operation(ctx.backend(), async_operation_id)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data={"cancelled": True, "id": async_operation_id})


@cli.command("service-document")
@pass_ctx
def cli_service_document(ctx):
    """GET the root service document — lists every entity set the server exposes."""
    try:
        result = sol_mod.service_document(ctx.backend())
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if ctx.json_mode:
        ctx.emit(True, data=result, meta={"count": len((result or {}).get("value", []))})
        return
    sets = (result or {}).get("value", [])
    headers = ["name", "url", "kind"]
    rows = [[s.get("name", ""), s.get("url", ""), s.get("kind", "")] for s in sets[:200]]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta={"count": len(sets)})


@cli.command("batch")
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--no-transaction", is_flag=True, default=False,
              help="Send each op as a top-level operation; no changeset wrapping.")
@click.option("--continue-on-error", is_flag=True, default=False,
              help="Send Prefer: odata.continue-on-error (requires --no-transaction).")
@click.option("--output", "output_path", type=click.Path(dir_okay=False), default=None,
              help="Write BatchResult[] JSON to this path.")
@click.option("--timeout", type=int, default=None,
              help="Override request timeout (seconds) for the batch call.")
@pass_ctx
def cli_batch(ctx, file_path, no_transaction, continue_on_error, output_path, timeout):
    """Execute a $batch from a JSON file."""
    if continue_on_error and not no_transaction:
        raise click.UsageError(
            "--continue-on-error requires --no-transaction; "
            "Prefer: odata.continue-on-error is meaningless inside a changeset."
        )
    try:
        ops = batch_mod.parse_batch_file(file_path)
        results = ctx.backend().batch(
            ops,
            transactional=not no_transaction,
            continue_on_error=continue_on_error,
            timeout=timeout,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return

    if output_path:
        try:
            Path(output_path).write_text(
                json.dumps(results, indent=2, default=str), encoding="utf-8"
            )
        except OSError as exc:
            ctx.emit(False, error=f"Could not write {output_path}: {exc}")
            return
        ctx.emit(True, data={"written": output_path,
                             **batch_mod.render_batch_summary(results)})
    else:
        ctx.emit(True, data=results, meta=batch_mod.render_batch_summary(results))


@solution.command("import")
@click.argument("zip_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--no-publish", is_flag=True)
@click.option("--no-overwrite", is_flag=True)
@click.option("--timeout", type=int, default=None,
              help="Async operation timeout in seconds. Overrides profile.async_timeout.")
@click.option("--no-retry", is_flag=True,
              help="Disable the 429/5xx retry loop for this invocation.")
@click.option("--quiet", "-q", is_flag=True,
              help="Suppress per-tick import-progress lines on stderr.")
@pass_ctx
def solution_import_cmd(ctx, zip_path, no_publish, no_overwrite, timeout, no_retry, quiet):
    with _no_retry_scope(ctx, no_retry):
        try:
            info = sol_mod.import_solution(
                ctx.backend(), zip_path,
                publish_workflows=not no_publish,
                overwrite_unmanaged_customizations=not no_overwrite,
                timeout=timeout,
                quiet=quiet,
            )
        except D365Error as exc:
            _handle_d365_error(ctx, exc)
            return
        ctx.emit(True, data=info)


# ── Data (bulk) group ───────────────────────────────────────────────────


@cli.group()
def data():
    """Bulk CSV/JSON dataset export."""


@data.command("export")
@click.argument("entity_set")
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False))
@click.option("--select", multiple=True)
@click.option("--filter", "filter_", help="OData $filter.")
@click.option("--page-size", type=int, default=500)
@click.option("--max-records", type=int, default=None)
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]))
@pass_ctx
def data_export(ctx, entity_set, output, select, filter_, page_size, max_records, fmt):
    select_list: list[str] = []
    for s in select:
        select_list.extend(part.strip() for part in s.split(",") if part.strip())
    try:
        info = export_mod.export_records(
            ctx.backend(), entity_set, output,
            select=select_list or None,
            filter_=filter_,
            page_size=page_size,
            max_records=max_records,
            fmt=fmt,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


# ── Action group (functions / actions) ──────────────────────────────────


@cli.group()
def action():
    """Invoke OData functions and actions (unbound or bound)."""


@action.command("function")
@click.argument("name")
@click.option("--params", "params_json", help='JSON dict of function parameters.')
@pass_ctx
def action_function(ctx, name, params_json):
    """Call an unbound OData function. Params encoded inline per OData v4."""
    backend = ctx.backend() if not ctx.dry_run else None
    params = json.loads(params_json) if params_json else None
    if params:
        encoded = ",".join(f"{k}={_odata_literal(v)}" for k, v in params.items())
        path = f"{name}({encoded})"
    else:
        path = f"{name}()"
    try:
        result = (backend or ctx.backend()).get(path)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result or {})


@action.command("invoke")
@click.argument("name")
@click.option("--body", "body_json", help="JSON body for the action.")
@click.option("--body-file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--bind-set",
    help="Entity set name to bind the action to (e.g. 'workflows'). Requires --bind-id.",
)
@click.option(
    "--bind-id",
    help="Record id to bind the action to. Requires --bind-set.",
)
@click.option(
    "--cast",
    default="Microsoft.Dynamics.CRM",
    show_default=True,
    help="Namespace for the action when bound. Override only for custom namespaces.",
)
@pass_ctx
def action_invoke(ctx, name, body_json, body_file, bind_set, bind_id, cast):
    """POST an OData action — unbound by default, bound when --bind-set/--bind-id given."""
    if bool(bind_set) ^ bool(bind_id):
        ctx.emit(False, error="--bind-set and --bind-id must be used together.")
        return
    payload = _load_payload(body_json, body_file) if (body_json or body_file) else {}
    if bind_set and bind_id:
        path = f"{bind_set}({bind_id})/{cast}.{name}"
    else:
        path = name
    try:
        result = ctx.backend().post(path, json_body=payload)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=result or {})


# ── Async-operations group ──────────────────────────────────────────────

_ASYNC_STATE_NAMES = {
    "ready": 0,
    "suspended": 1,
    "locked": 2,
    "completed": 3,
}


def _resolve_async_state(value: str | None) -> int | None:
    if value is None:
        return None
    if value.isdigit():
        return int(value)
    name = value.lower()
    if name in _ASYNC_STATE_NAMES:
        return _ASYNC_STATE_NAMES[name]
    raise click.BadParameter(
        f"--state must be one of {sorted(_ASYNC_STATE_NAMES)} or an integer; got {value!r}"
    )


@cli.group("async")
def async_group():
    """List, inspect, and cancel asynchronous operations."""


@async_group.command("list")
@click.option("--state", default=None,
              help="ready | suspended | locked | completed | <int>")
@click.option("--message", "message_name", default=None,
              help="Filter by messagename (e.g. ImportSolution).")
@click.option("--owner", "owner_id", default=None,
              help="Filter by systemuser GUID.")
@click.option("--top", type=int, default=50, help="Page size per call (default 50).")
@click.option("--all", "fetch_all", is_flag=True, default=False,
              help="Follow @odata.nextLink until exhausted (caps at --max-pages).")
@click.option("--max-pages", type=int, default=20,
              help="Safety cap on pagination depth when --all is set (default 20).")
@pass_ctx
def async_list(ctx, state, message_name, owner_id, top, fetch_all, max_pages):
    """List asyncoperation rows."""
    try:
        state_int = _resolve_async_state(state)
        backend = ctx.backend()
        if fetch_all:
            rows = async_ops_mod.list_all_async_operations(
                backend, state=state_int, message_name=message_name,
                owner_id=owner_id, page_size=top, max_pages=max_pages,
            )
        else:
            rows = async_ops_mod.list_async_operations(
                backend, state=state_int, message_name=message_name,
                owner_id=owner_id, top=top,
            )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=rows, meta={"count": len(rows)})


@async_group.command("get")
@click.argument("async_operation_id")
@pass_ctx
def async_get(ctx, async_operation_id):
    """Get one asyncoperation row."""
    try:
        row = async_ops_mod.get_async_operation(ctx.backend(), async_operation_id)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=row)


@async_group.command("cancel")
@click.argument("async_operation_id")
@click.confirmation_option(prompt="Cancel this async operation?")
@pass_ctx
def async_cancel(ctx, async_operation_id):
    """Cancel a pending or suspended asyncoperation."""
    try:
        async_ops_mod.cancel_async_operation(ctx.backend(), async_operation_id)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data={"cancelled": True, "id": async_operation_id})


# ── Workflow group ──────────────────────────────────────────────────────


@cli.group()
def workflow():
    """List, activate, and trigger D365 workflows."""


@workflow.command("list")
@click.option("--category", type=int, help="Filter by category (0=Workflow, 4=BPF, 5=Modern Flow).")
@click.option("--entity", "primary_entity", help="Filter by primary entity logical name.")
@click.option("--activated/--all", "activated_only", default=False,
              help="Restrict to activated workflows. Default returns all states.")
@click.option("--on-demand", "on_demand_only", is_flag=True, default=False,
              help="Only on-demand workflows.")
@pass_ctx
def workflow_list(ctx, category, primary_entity, activated_only, on_demand_only):
    """List workflow definitions."""
    try:
        items = workflow_mod.list_workflows(
            ctx.backend(),
            category=category,
            primary_entity=primary_entity,
            activated_only=activated_only,
            on_demand_only=on_demand_only,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=items, meta={"count": len(items)})


@workflow.command("activate")
@click.argument("workflow_id")
@_admin_header_options
@pass_ctx
def workflow_activate(ctx, workflow_id, as_user, suppress_dup_detection, bypass_plugins):
    """Activate a workflow (statecode=1, statuscode=2)."""
    try:
        info = workflow_mod.set_workflow_state(
            ctx.backend(), workflow_id, activate=True,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@workflow.command("deactivate")
@click.argument("workflow_id")
@_admin_header_options
@pass_ctx
def workflow_deactivate(ctx, workflow_id, as_user, suppress_dup_detection, bypass_plugins):
    """Deactivate a workflow (statecode=0, statuscode=1)."""
    try:
        info = workflow_mod.set_workflow_state(
            ctx.backend(), workflow_id, activate=False,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


@workflow.command("run")
@click.argument("workflow_id")
@click.option("--target", "target_record_id", required=True,
              help="GUID of the record to run the workflow against.")
@_admin_header_options
@pass_ctx
def workflow_run(ctx, workflow_id, target_record_id,
                 as_user, suppress_dup_detection, bypass_plugins):
    """Trigger an on-demand workflow against a target record via ExecuteWorkflow."""
    try:
        info = workflow_mod.execute_workflow(
            ctx.backend(), workflow_id, target_record_id,
            **_admin_kwargs(as_user, suppress_dup_detection, bypass_plugins),
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)


def _odata_literal(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("'", "''")
    return f"'{s}'"


# ── Skill group ─────────────────────────────────────────────────────────


SKILL_TARGETS: dict[str, Path] = {
    "copilot": Path.home() / ".copilot" / "skills" / "crm",
    "claude": Path.home() / ".claude" / "skills" / "crm",
    "cursor": Path.home() / ".cursor" / "rules" / "crm",
}


def _bundled_skill_path() -> Path:
    """Return path to the SKILL.md shipped inside the installed crm package."""
    import crm as _crm_pkg
    return Path(_crm_pkg.__file__).resolve().parent / "skills" / "SKILL.md"


def _resolve_skill_dest(target: str | None, dest: str | None) -> Path:
    if dest:
        return Path(dest).expanduser().resolve()
    return SKILL_TARGETS[target or "copilot"]


@cli.group("skill")
def skill_group():
    """Install the bundled agent skill (SKILL.md) for Copilot / Claude / Cursor."""


@skill_group.command("path")
@pass_ctx
def skill_path(ctx: CLIContext):
    """Show the path of the bundled SKILL.md inside the installed package."""
    src = _bundled_skill_path()
    ctx.emit(src.exists(), data={"path": str(src), "exists": src.exists()})


@skill_group.command("install")
@click.option(
    "--target",
    type=click.Choice(sorted(SKILL_TARGETS.keys())),
    default="copilot",
    show_default=True,
    help="Where to install the skill. Ignored if --dest is given.",
)
@click.option(
    "--dest",
    type=click.Path(file_okay=False),
    default=None,
    help="Custom destination directory (overrides --target).",
)
@click.option("--force", is_flag=True, help="Overwrite an existing SKILL.md at the destination.")
@pass_ctx
def skill_install(ctx: CLIContext, target: str, dest: str | None, force: bool):
    """Copy the bundled SKILL.md into the agent's skill directory."""
    src = _bundled_skill_path()
    if not src.exists():
        ctx.emit(False, error=f"Bundled SKILL.md not found at {src}.")
        sys.exit(1)

    dest_dir = _resolve_skill_dest(target, dest)
    dest_file = dest_dir / "SKILL.md"

    if dest_file.exists() and not force:
        ctx.emit(
            False,
            error=f"{dest_file} already exists. Use --force to overwrite.",
            meta={"target": target, "dest": str(dest_dir)},
        )
        sys.exit(1)

    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest_file)
    ctx.emit(
        True,
        data={"installed": True, "source": str(src), "dest": str(dest_file)},
        meta={"target": target if not dest else "custom"},
    )


@skill_group.command("uninstall")
@click.option(
    "--target",
    type=click.Choice(sorted(SKILL_TARGETS.keys())),
    default="copilot",
    show_default=True,
)
@click.option("--dest", type=click.Path(file_okay=False), default=None)
@pass_ctx
def skill_uninstall(ctx: CLIContext, target: str, dest: str | None):
    """Remove the installed SKILL.md (and its directory if empty)."""
    dest_dir = _resolve_skill_dest(target, dest)
    dest_file = dest_dir / "SKILL.md"
    if not dest_file.exists():
        ctx.emit(True, data={"removed": False, "reason": "not installed", "dest": str(dest_file)})
        return
    dest_file.unlink()
    try:
        dest_dir.rmdir()
    except OSError:
        pass
    ctx.emit(True, data={"removed": True, "dest": str(dest_file)})


# ── Session group ───────────────────────────────────────────────────────


@cli.group()
def session():
    """Local session state."""


@session.command("info")
@pass_ctx
def session_info(ctx):
    state = session_mod.load_session(ctx.session_name)
    ctx.emit(True, data=state)


@session.command("clear")
@pass_ctx
def session_clear(ctx):
    state = {
        "name": ctx.session_name,
        "active_profile": None,
        "current_entity_set": None,
        "last_query": None,
        "history": [],
    }
    session_mod.save_session(state, ctx.session_name)
    ctx.emit(True, data={"cleared": True})


@session.command("history")
@pass_ctx
def session_history(ctx):
    state = session_mod.load_session(ctx.session_name)
    history = state.get("history", [])
    if ctx.json_mode:
        ctx.emit(True, data=history)
        return
    for i, line in enumerate(history[-50:], 1):
        click.echo(f"  {i:>3}  {line}")


# ── REPL ────────────────────────────────────────────────────────────────


@cli.command("repl")
@pass_ctx
def repl(ctx: CLIContext):
    """Interactive REPL (default when no subcommand is provided)."""
    ctx.skin.print_banner()
    ctx.skin.info(f"Session: {ctx.session_name}  |  Type 'help' for commands, 'quit' to exit.")
    pt_session = ctx.skin.create_prompt_session()
    state = session_mod.load_session(ctx.session_name)

    while True:
        try:
            profile_label = state.get("active_profile") or "<no profile>"
            line = ctx.skin.get_input(
                pt_session, project_name=profile_label,
                modified=bool(state.get("last_query")),
            )
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        cmd = line.strip()
        if cmd in ("quit", "exit", ":q"):
            break
        if cmd in ("help", "?"):
            _repl_help(ctx)
            continue
        if cmd == "clear":
            click.clear()
            continue

        session_mod.append_history(state, cmd)
        try:
            argv = shlex.split(cmd)
        except ValueError as exc:
            ctx.skin.error(f"Parse error: {exc}")
            continue
        try:
            cli.main(args=argv, obj=ctx, standalone_mode=False, prog_name="crm")
        except SystemExit:
            pass
        except click.ClickException as exc:
            ctx.skin.error(exc.format_message())
        except D365Error as exc:
            ctx.skin.error(str(exc))
        except Exception as exc:  # noqa: BLE001 — REPL must keep running
            ctx.skin.error(f"{type(exc).__name__}: {exc}")
        state = session_mod.load_session(ctx.session_name)
        session_mod.save_session(state, ctx.session_name)

    session_mod.save_session(state, ctx.session_name)
    ctx.skin.print_goodbye()


def _repl_help(ctx: CLIContext):
    ctx.skin.help({
        "connection connect": "Save profile and verify with WhoAmI",
        "connection status": "Show active session/profile",
        "connection whoami": "Issue WhoAmI() against the server",
        "entity get <set> <id>": "GET a record",
        "entity create <set> --data '{...}'": "POST a new record",
        "entity update <set> <id> --data '{...}'": "PATCH a record",
        "entity delete <set> <id>": "DELETE a record",
        "query odata <set> [--filter ...] [--top N]": "OData query",
        "query fetchxml <set> --xml '<fetch>...</fetch>'": "FetchXML query",
        "metadata entities": "List entity definitions",
        "metadata attributes <entity>": "List attributes",
        "solution list / info / export / import": "Solution lifecycle",
        "data export <set> -o file.csv": "Bulk export",
        "action function/invoke <name>": "Call OData function/action",
        "session info / clear / history": "Local session state",
        "help / quit": "REPL controls",
    })


# ── Helpers ─────────────────────────────────────────────────────────────


def _load_payload(data_json: str | None, data_file: str | None) -> dict[str, Any]:
    if data_file:
        with open(data_file, "r", encoding="utf-8") as f:
            parsed = json.load(f)
    elif data_json:
        parsed = json.loads(data_json)
    else:
        raise click.UsageError("Either --data or --data-file is required.")
    if not isinstance(parsed, dict):
        raise click.UsageError(
            f"Payload must be a JSON object, got {type(parsed).__name__}."
        )
    return parsed


def _touch_session(ctx: CLIContext, entity_set: str, *,
                   last_query: dict | None = None) -> None:
    state = session_mod.load_session(ctx.session_name)
    state["current_entity_set"] = entity_set
    if last_query is not None:
        state["last_query"] = last_query
    session_mod.save_session(state, ctx.session_name)


if __name__ == "__main__":
    cli()
