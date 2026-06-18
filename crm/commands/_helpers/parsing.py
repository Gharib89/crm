"""Input parsing + expectation-checking helpers and CLI choice constants."""
# pyright: basic
from __future__ import annotations
import json
from typing import Any
import click


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
        # A leading '@' is the curl-style "@file" convention, not valid JSON
        # (no JSON value starts with '@'); point at --data-file rather than
        # letting json.loads fail with an opaque "Expecting value" error.
        if data_json.lstrip().startswith("@"):
            raise click.UsageError(
                "--data does not read files; use --data-file <path> to load a JSON payload from a file."
            )
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


def _parse_value_labels(
    raws: tuple[str, ...], *, flag: str, require_value: bool = False
) -> list[tuple[int | None, str]]:
    """Parse repeatable ``value:label`` flags into (value, label) pairs.

    Split on the FIRST ':' (so a label may itself contain ':'); both sides are
    trimmed. With ``require_value=False`` a bare ``:label`` yields
    ``(None, label)``. With ``require_value=True`` the value must be a non-empty
    integer. A missing ':' — or a non-integer value, in BOTH modes — is a clean
    ``click.UsageError`` (exit 2); the latter folds in the #294 int-guard
    hardening, so a typo like ``--option abc:foo`` no longer raises an unhandled
    ``ValueError``. Empty input yields ``[]``.

    The return type is always the loose ``list[tuple[int | None, str]]``; a
    ``require_value=True`` caller that must satisfy a strict
    ``list[tuple[int, str]]`` parameter (``update_optionset``'s ``update``)
    casts at the call site — sound because ``require_value=True`` guarantees an
    ``int`` for every value.
    """
    parsed: list[tuple[int | None, str]] = []
    for raw in raws:
        if ":" not in raw:
            form = "'value:label'" if require_value else "'value:label' or ':label'"
            raise click.UsageError(f"{flag} must be {form}, got: {raw!r}")
        v, _, lab = raw.partition(":")
        v = v.strip()
        lab = lab.strip()
        if v:
            try:
                value: int | None = int(v)
            except ValueError as exc:
                raise click.UsageError(
                    f"{flag} value must be an integer, got: {raw!r}"
                ) from exc
        elif require_value:
            raise click.UsageError(
                f"{flag} requires an integer value before ':', got: {raw!r}"
            )
        else:
            value = None
        parsed.append((value, lab))
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


def _odata_literal(v: Any) -> str:
    # Delegates to the canonical escaping in d365_backend; the local import keeps
    # d365_backend off the `crm --version` fast path (this only runs once a
    # query/action is being built, by which point the backend is loaded).
    from crm.utils.d365_backend import odata_literal
    return odata_literal(v)


# Characters that are structurally significant in a URL path segment: an inline
# OData function-parameter literal carrying any of them 400/404s. Per the Web
# API docs they must be passed as a query-string parameter alias instead, where
# the value is percent-encoded safely.
# https://learn.microsoft.com/power-apps/developer/data-platform/webapi/use-web-api-functions#passing-parameters-to-a-function
_ODATA_RESERVED = set("/<>*%&:\\?+#")


def _needs_alias(value: Any) -> bool:
    """A function-param value must be passed as a parameter alias (not inline)
    when it is a record reference (a dict) or a string carrying URL-reserved or
    whitespace characters that would break an inline path-segment literal."""
    if isinstance(value, dict):
        return True
    if isinstance(value, str):
        return any(c in _ODATA_RESERVED or c.isspace() for c in value)
    return False


def _record_reference_value(name: str, value: dict[str, Any]) -> str:
    """Validate a record-reference param (``{"@odata.id": "set(guid)"}``) and
    render its alias value as an ``@odata.id`` JSON object. Raises ValueError on
    a malformed reference so the command can report it as an operational
    failure."""
    ref = value.get("@odata.id")
    if set(value) != {"@odata.id"} or not isinstance(ref, str) or not ref:
        raise ValueError(
            f"parameter {name!r} must be a record reference of the form "
            '{"@odata.id": "<entityset>(<guid>)"}'
        )
    return json.dumps({"@odata.id": ref})


def encode_function_params(params: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """Encode ``action function`` params into ``(inline_args, aliases)``.

    ``inline_args`` is the comma-joined ``Name=...`` body for ``Fn(...)``;
    scalars render inline per OData v4. Record references
    (``{"@odata.id": "set(guid)"}``) and reserved-char/whitespace strings instead
    become parameter aliases (``Name=@pN``), with ``aliases`` mapping each ``@pN``
    to its query-string value. The caller passes ``aliases`` as the request
    ``params=`` kwarg so the values land in the query string — the only place the
    server accepts a record reference or a reserved character. Raises ValueError
    on a malformed record reference."""
    parts: list[str] = []
    aliases: dict[str, str] = {}
    for name, value in params.items():
        if _needs_alias(value):
            alias = f"@p{len(aliases) + 1}"
            aliases[alias] = (
                _record_reference_value(name, value)
                if isinstance(value, dict)
                else _odata_literal(value)
            )
            parts.append(f"{name}={alias}")
        else:
            parts.append(f"{name}={_odata_literal(value)}")
    return ",".join(parts), aliases


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
