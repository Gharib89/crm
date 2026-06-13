"""Shared helpers used across crm.commands.*."""
# pyright: basic
from __future__ import annotations
import json
import os
import re
import urllib.parse
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
import click
from crm.core import session as session_mod
if TYPE_CHECKING:
    from crm.cli import CLIContext
    from crm.utils.d365_backend import ConnectionProfile, D365Error


def _journal(ctx, command, target, result, *, solution=None, staged=None):
    """Best-effort audit-journal a successful mutation (issue #89). Never raises."""
    try:
        from crm.core import audit
        # Prefer the RESOLVED profile name from the backend that just ran the
        # mutation — ctx.profile_name is only the explicit --profile override and
        # is None for active-profile runs. The backend is already built (the
        # command called ctx.backend()), so this needs no extra I/O.
        backend = getattr(ctx, "_backend", None)
        profile = getattr(getattr(backend, "profile", None), "name", None) or ctx.profile_name
        audit.record(
            session=ctx.session_name,
            profile=profile,
            command=command,
            target=target,
            result=result,
            solution=solution,
            staged=ctx.stage_only if staged is None else staged,
            dry_run=ctx.dry_run,
        )
    except Exception:
        pass


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


def _handle_d365_error(
    ctx: "CLIContext",
    exc: D365Error,
    *,
    hint: str | None = None,
    extra_meta: dict | None = None,
    warnings: list[str] | None = None,
) -> None:
    # Local import: this only runs after the backend already raised a D365Error,
    # so d365_backend is loaded — keeps it off the `crm --version` fast path.
    from crm.utils.d365_backend import classify_d365_error
    category, retryable = classify_d365_error(exc.status, exc.code, str(exc))
    meta: dict[str, Any] = {
        "status": exc.status,
        "code": exc.code,
        "category": category,
        "retryable": retryable,
    }
    # Auto-derive an auth fix-it hint on a 401 when the caller gave none, so a
    # rejected/stale secret steers the user to re-store it. The active profile
    # name comes from the resolved backend, else the --profile flag.
    if hint is None and exc.status == 401:
        backend = getattr(ctx, "_backend", None)
        pname = (getattr(getattr(backend, "profile", None), "name", None)
                 or ctx.profile_name or "<name>")
        hint = _auth_error_hint(exc.status, pname)
    # Partial-failure context (#64): only the non-transactional optionset update
    # path sets these. Guarded is-not-None so every other error site keeps
    # emitting an identical {status, code, category, retryable} envelope.
    if exc.completed_steps is not None:
        meta["completed_steps"] = exc.completed_steps
    if exc.stage is not None:
        meta["failed_stage"] = exc.stage
    if hint and ctx.json_mode:
        meta["hint"] = hint
    if extra_meta:
        meta.update(extra_meta)
    message = f"{exc}\nHint: {hint}" if hint else str(exc)
    ctx.emit(False, error=message, meta=meta, warnings=warnings)


def _plaintext_secret_warning() -> str:
    """Warning shown after writing a profile secret in PLAINTEXT.

    Shared by `profile add` and `profile set-password` so the wording
    stays identical. POSIX notes the 0600 mode; Windows adds that file perms are
    NOT enforced and steers to --store-password (Credential Manager).
    """
    if os.name == "posix":
        return "Stored the secret in PLAINTEXT in the profile file (0600)."
    return (
        "Stored the secret in PLAINTEXT in the profile file. On Windows file "
        "permissions are NOT enforced — prefer --store-password (Credential Manager)."
    )


def _confirm_destructive(
    thing: str, name: str, yes: bool, *, message: str | None = None
) -> bool:
    """Return True to proceed, False to bail.

    `--yes` skips the prompt. On a true non-TTY (EOF) stdin, `click.confirm`
    raises `click.Abort`; we catch it and return False so the caller can emit
    the documented ``{"ok": false, "error": "aborted by user"}`` envelope
    (exit 1) instead of click's bare ``Aborted!`` with no JSON.

    `message` overrides the default delete wording for non-delete destructive
    ops (e.g. an overwrite-import that names the actual risk) — see #67.
    """
    if yes:
        return True
    prompt = message or (
        f"This will permanently delete {thing} {name!r} and all related data. Continue?"
    )
    try:
        return click.confirm(prompt, default=False)
    except click.Abort:
        return False


def _admin_header_options(f):
    """Stack `--as-user`, `--as-user-object-id`, `--suppress-dup-detection`, `--bypass-plugins`."""
    f = click.option(
        "--bypass-plugins", is_flag=True, default=False,
        help="Send MSCRM.BypassCustomPluginExecution: true (requires prvBypassCustomPluginExecution).",
    )(f)
    f = click.option(
        "--suppress-dup-detection", is_flag=True, default=False,
        help="Send MSCRM.SuppressDuplicateDetection: true.",
    )(f)
    f = click.option(
        "--as-user-object-id", "as_user_object_id", metavar="GUID", default=None,
        help="Impersonate by Entra ID object id (cloud) via CallerObjectId header. "
             "Mutually exclusive with --as-user.",
    )(f)
    f = click.option(
        "--as-user", "as_user", metavar="GUID", default=None,
        help="Impersonate systemuser by GUID via MSCRMCallerID header. "
             "Mutually exclusive with --as-user-object-id.",
    )(f)
    return f


def _admin_kwargs(as_user: str | None, as_user_object_id: str | None,
                  suppress_dup_detection: bool,
                  bypass_plugins: bool) -> dict[str, Any]:
    """Resolve admin-header CLI flags into backend kwargs.

    `is_flag` defaults to False (flag absent). To preserve the backend's
    tri-state semantics (None = use env default like CRM_SUPPRESS_DUP /
    CRM_BYPASS_PLUGINS), we forward True only when the flag was actually
    set on the command line; otherwise None lets the backend env default
    take effect.

    `caller_id` (--as-user, MSCRMCallerID) and `caller_object_id`
    (--as-user-object-id, CallerObjectId) are forwarded as-is; the backend
    enforces that at most one resolves per request.
    """
    return {
        "caller_id": as_user,
        "caller_object_id": as_user_object_id,
        "suppress_duplicate_detection": True if suppress_dup_detection else None,
        "bypass_custom_plugin_execution": True if bypass_plugins else None,
    }


def _resolve_publish(ctx: "CLIContext", publish: bool) -> bool:
    """Derive the effective publish value, honoring the global --stage-only flag.

    When `ctx.stage_only` is set, every metadata-mutating command behaves as
    --no-publish. Passing an explicit --publish on the command line alongside
    --stage-only is contradictory and rejected. An explicit --no-publish is fine.
    """
    if not ctx.stage_only:
        return publish
    # Imported from click.core (not top-level click) because pyright's bundled click
    # stubs only export ParameterSource there; `click.ParameterSource` / `from click
    # import ParameterSource` fail strict type-checking even though both work at runtime.
    from click.core import ParameterSource
    source = click.get_current_context().get_parameter_source("publish")
    if source == ParameterSource.COMMANDLINE and publish:
        raise click.UsageError("--publish cannot be combined with --stage-only")
    return False


def _active_profile(ctx: "CLIContext") -> ConnectionProfile | None:
    """Load the active connection profile, or None if none is resolvable."""
    name = ctx.profile_name
    if not name:
        state = session_mod.load_session(ctx.session_name)
        name = state.get("active_profile")
    if not name:
        return None
    try:
        return session_mod.load_profile(name)
    except FileNotFoundError:
        return None


def _require_solution(require_flag: bool) -> bool:
    """Strict mode active when the --require-solution flag OR CRM_REQUIRE_SOLUTION set."""
    if require_flag:
        return True
    return os.environ.get("CRM_REQUIRE_SOLUTION", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _solution_option(f):
    """Stack `--solution` / `--require-solution` on a mutating metadata command."""
    f = click.option(
        "--require-solution", is_flag=True, default=False,
        help="Fail if no solution resolves (also via CRM_REQUIRE_SOLUTION).",
    )(f)
    f = click.option(
        "--solution", default=None,
        help="Target solution uniquename (MSCRM.SolutionUniqueName). "
             "Defaults to the profile's default_solution.",
    )(f)
    return f


def _resolve_solution(
    ctx: "CLIContext", explicit: str | None, *, require: bool,
) -> tuple[str | None, str | None]:
    """Resolve the effective solution for a mutating metadata command.

    Precedence: explicit `--solution` > profile `default_solution` > None.

    Returns `(solution, warning)`. When none resolves and `require` is False,
    `warning` is a non-empty string the caller stashes under the JSON `meta`
    envelope (or prints via skin.warning in human mode). When none resolves and
    `require` is True, this routes a hard failure through `ctx.emit(False)`
    (raising click.exceptions.Exit per ADR 0001) instead of returning.
    """
    if explicit:
        return explicit, None
    profile = _active_profile(ctx)
    if profile and profile.default_solution:
        return profile.default_solution, None
    msg = (
        "No solution resolved: pass --solution or set a profile default_solution. "
        "The change targets the default (unmanaged) solution."
    )
    if require:
        ctx.emit(False, error=msg)
        return None, None  # unreachable: emit(False) raises Exit
    return None, msg


def _emit_with_warning(
    ctx: "CLIContext", data: Any, warning: str | None,
    *, meta: dict[str, Any] | None = None,
) -> None:
    """Emit a successful result, surfacing advisories via the warnings channel.

    Rolls the solution `warning` (if any) plus any `*_lookup_error` read-back
    keys found in `data` into the structured `meta.warnings` array (#64) —
    appending, never clobbering. The `*_lookup_error` keys stay in `data` for
    back-compat. In human mode emit prints each via skin.warning.
    """
    warnings: list[str] = []
    if warning:
        warnings.append(warning)
    if isinstance(data, dict):
        for key, value in data.items():
            if key.endswith("_lookup_error") and value:
                warnings.append(str(value))
    ctx.emit(True, data=data, meta=meta, warnings=warnings or None)


def _resolve_schema_name(
    ctx: "CLIContext", schema_name: str | None, token: str | None, flag: str,
) -> str:
    """Resolve a create command's schema name from an explicit value or prefix.

    If `schema_name` is given, return it verbatim. Otherwise build
    `<publisher_prefix>_<PascalToken>` from the active profile prefix and the
    display/name token. Raises UsageError when neither is available.
    """
    if schema_name:
        return schema_name
    profile = _active_profile(ctx)
    prefix = profile.publisher_prefix if profile else None
    if not prefix:
        raise click.UsageError(
            f"{flag} is required (no publisher_prefix on the active profile to "
            "default from)."
        )
    if not token:
        raise click.UsageError(f"{flag} is required to default the schema name.")
    # PascalCase across word boundaries and drop non-alphanumerics so a
    # multi-word display like "Project Task" -> "ProjectTask", not the invalid
    # "Project Task". Preserve casing of the rest of each word (don't lower it).
    pascal = "".join(
        w[:1].upper() + w[1:] for w in re.split(r"[^0-9A-Za-z]+", token) if w
    )
    if not pascal:
        raise click.UsageError(
            f"{flag} could not be defaulted from {token!r} (no alphanumeric "
            f"characters); pass {flag} explicitly."
        )
    return f"{prefix}_{pascal}"


def _load_payload(data_json: str | None, data_file: str | None) -> dict[str, Any]:
    if data_file:
        try:
            with open(data_file, "r", encoding="utf-8") as f:
                parsed = json.load(f)
        except OSError as exc:
            raise click.UsageError(f"cannot read --data-file: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise click.UsageError(f"invalid JSON in --data-file: {exc}") from exc
    elif data_json:
        try:
            parsed = json.loads(data_json)
        except json.JSONDecodeError as exc:
            raise click.UsageError(f"invalid JSON in --data: {exc}") from exc
    else:
        raise click.UsageError("Either --data or --data-file is required.")
    if not isinstance(parsed, dict):
        raise click.UsageError(
            f"Payload must be a JSON object, got {type(parsed).__name__}."
        )
    return parsed


def _parse_expect(pairs: tuple[str, ...]) -> list[tuple[str, str]]:
    """Parse repeatable --expect ATTR=VALUE flags into (attr, value) pairs.

    Split on the FIRST '=' so a VALUE may itself contain '='. A pair missing
    '=' or with an empty attr is a usage error (exit 2); validate before any
    backend call so a typo never costs a round-trip. The attr is trimmed; the
    value is taken verbatim, so any leading/trailing whitespace in a value is
    significant."""
    parsed: list[tuple[str, str]] = []
    for raw in pairs:
        attr, sep, value = raw.partition("=")
        if not sep or not attr.strip():
            raise click.UsageError(
                f"--expect must be ATTR=VALUE with a non-empty attribute, got {raw!r}"
            )
        parsed.append((attr.strip(), value))
    return parsed


def _check_expectations(
    record: dict[str, Any], pairs: list[tuple[str, str]]
) -> dict[str, Any] | None:
    """AND-gate stringified comparison of a retrieved record against expected
    values. Each expected VALUE (a CLI string) is compared to str(record[attr]).
    A key absent from the record is ALWAYS a mismatch (reported with
    actual=None): it can never satisfy an expectation, so a typo'd attribute
    name fails instead of accidentally matching `--expect attr=None`. A key
    that is present with a null value compares as the string 'None'. Returns
    None when every pair matches, else {attr, expected, actual} for the FIRST
    mismatch in CLI order (actual is the raw value, for JSON consumers)."""
    for attr, expected in pairs:
        if attr not in record:
            return {"attr": attr, "expected": expected, "actual": None}
        actual = record[attr]
        if str(actual) != expected:
            return {"attr": attr, "expected": expected, "actual": actual}
    return None


def _emit_expectation_failure(ctx: "CLIContext", miss: dict[str, Any]) -> None:
    """Emit the standard `--expect` mismatch envelope (exit 1).

    `miss` is the {attr, expected, actual} dict from `_check_expectations`. The
    human-readable error string embeds the same three values because `emit`'s
    human-mode failure path renders only `error`, not `meta`."""
    ctx.emit(
        False,
        error=f"Expectation failed: {miss['attr']}={miss['expected']!r} "
              f"(actual {miss['actual']!r})",
        meta=miss,
    )


def _touch_session(ctx: "CLIContext", entity_set: str, *,
                   last_query: dict | None = None) -> None:
    state = session_mod.load_session(ctx.session_name)
    state["current_entity_set"] = entity_set
    if last_query is not None:
        state["last_query"] = last_query
    session_mod.save_session(state, ctx.session_name)


def _odata_literal(v: Any) -> str:
    # Delegates to the canonical escaping in d365_backend; the local import keeps
    # d365_backend off the `crm --version` fast path (this only runs once a
    # query/action is being built, by which point the backend is loaded).
    from crm.utils.d365_backend import odata_literal
    return odata_literal(v)


def _prune_annotations(record: dict[str, Any]) -> dict[str, Any]:
    """Drop OData annotation keys (any key containing '@') from a record,
    keeping business fields, `_*_value` lookup GUIDs, and the primary id.

    Shallow prune: only top-level keys are stripped — annotations nested
    inside expanded records (under `--expand`) are not pruned."""
    return {k: v for k, v in record.items() if "@" not in k}


def _emit_query_result(
    ctx: "CLIContext", result: dict, entity_set: str, *, minimal: bool = False,
) -> None:
    values = result.get("value", []) if isinstance(result, dict) else []
    meta: dict[str, Any] = {"entity_set": entity_set, "count": len(values)}
    if "@odata.count" in (result or {}):
        meta["odata_count"] = result["@odata.count"]
    if "@odata.nextLink" in (result or {}):
        meta["next_link"] = "(present)"
    if ctx.json_mode:
        if minimal:
            result = {**result, "value": [
                _prune_annotations(r) if isinstance(r, dict) else r for r in values
            ]}
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


@contextmanager
def _no_retry_scope(ctx: "CLIContext", enabled: bool):
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


_CASCADE = click.Choice(
    ["NoCascade", "Cascade", "Active", "UserOwned", "RemoveLink", "Restrict"]
)
_MENU = click.Choice(["UseLabel", "UseCollectionName", "DoNotDisplay"])
_REQUIRED = click.Choice(["None", "Recommended", "ApplicationRequired"])


# ── Profile-UX helpers (credential revamp) ───────────────────────────────

# Dataverse online hosts always end in this suffix (crm.dynamics.com,
# crm4.dynamics.com, crm.dynamics.cn, ...). Anything else is treated as on-prem.
_CLOUD_HOST_MARKER = ".dynamics."


def infer_auth_scheme(url: str) -> str:
    """Guess the auth scheme from the server URL: oauth for Dataverse online
    (`*.dynamics.*`), else ntlm. The wizard shows this as an overridable default."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return "oauth" if _CLOUD_HOST_MARKER in host else "ntlm"


def default_profile_name(url: str) -> str:
    """Default profile name = the first label of the URL host (`crm.contoso.local`
    -> `crm`, `orgd080.crm.dynamics.com` -> `orgd080`). Falls back to 'default'
    when the URL has no parseable host."""
    host = urllib.parse.urlparse(url).hostname or ""
    label = host.split(".")[0] if host else ""
    return label or "default"


def _auth_error_hint(status: int | None, profile_name: str) -> str:
    """Map an auth failure to a copy-paste fix command, or '' when none applies.

    A 401 (rejected secret) steers the user to re-store the secret for the
    active profile."""
    if status == 401:
        return f"run: crm profile set-password --profile {profile_name}"
    return ""


def _stdin_is_tty() -> bool:
    """Re-export of cli._stdin_is_tty as a module-level name so tests can
    monkeypatch it without triggering a circular import at module load time
    (cli.py imports _helpers, so a top-level import of cli would be circular)."""
    from crm.cli import _stdin_is_tty as _impl
    return _impl()


def select_one(title: str, items: list[tuple[str, str]],
               default: str | None = None) -> str | None:
    """Show an inline arrow-key single-select picker; return the chosen value
    (the first element of the chosen tuple) or None if the user cancelled.

    `items` is a list of (value, label) pairs. `default`, if given, is a value
    that should be pre-selected and must match one of the item values. Raises
    ValueError on empty input or a default that isn't among the choices, and
    RuntimeError when stdin is not a TTY (scripts/CI must pass an explicit
    choice instead of relying on the picker)."""
    if not items:
        raise ValueError("select_one: no choices to display")
    if default is not None and default not in {value for value, _ in items}:
        raise ValueError(f"select_one: default {default!r} is not among the choices")
    if not _stdin_is_tty():
        raise RuntimeError(
            "select_one: no interactive terminal — pass an explicit choice instead"
        )
    # Lazy import: questionary (and its prompt_toolkit backend) is heavy; keep
    # it off the `crm --version` fast path (_helpers is imported by cli.py).
    # questionary.select renders inline (↑/↓ + Enter confirms, Esc cancels) —
    # no alternate-screen modal — and .ask() returns None on cancel.
    import questionary
    choices = [questionary.Choice(title=label, value=value) for value, label in items]
    return questionary.select(title, choices=choices, default=default).ask()
