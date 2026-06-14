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


def _odata_literal(v: Any) -> str:
    # Delegates to the canonical escaping in d365_backend; the local import keeps
    # d365_backend off the `crm --version` fast path (this only runs once a
    # query/action is being built, by which point the backend is loaded).
    from crm.utils.d365_backend import odata_literal
    return odata_literal(v)


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
