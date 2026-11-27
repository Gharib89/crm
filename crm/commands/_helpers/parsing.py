"""Input parsing + expectation-checking helpers and CLI choice constants."""

# pyright: basic
from __future__ import annotations

import json
from typing import Any

import click


def require_exactly_one(message: str, *values: object) -> None:
    """Raise a usage error (exit 2) unless exactly one value is given and it is not "".

    Exclusivity counts values that are not None, so an empty string is given, not
    absent: `--a "" --b X` is two flags. A lone empty value is rejected too (#1412).
    """
    given = [v for v in values if v is not None]
    if len(given) != 1 or given[0] == "":
        raise click.UsageError(message)


_UNSET = click.Option(["--x"]).default
"""Click's sentinel for an option declared with no default."""


class EmptyAllowedOption(click.Option):
    """An optional text option whose explicit empty value clears a field, so
    `reject_empty_values` leaves it alone. Declare it with `cls=EmptyAllowedOption`,
    and test the value with `is not None` in the command.
    """


def reject_empty_values(cmd: click.Command) -> None:
    """Make an explicit `--flag ""` on argv a usage error (exit 2) for every optional
    single-value text or path option of `cmd` (#1417).

    Without it a command testing the value by truthiness reads "" as an omitted flag
    and silently drops it. The check runs at parse time, before the command body
    builds a backend, and replaces each option's `type_cast_value` on the instance
    because `click.Path(path_type=Path)` turns "" into "." before a callback sees it.
    Only the command line counts, so a default or a prompt is untouched. Idempotent,
    since the root group re-walks a command on every resolution.
    """
    for param in cmd.params:
        if (
            not isinstance(param, click.Option)
            or isinstance(param, EmptyAllowedOption)
            or param.is_flag
            or param.multiple
            or param.count
            or param.required
            or param.nargs != 1
            or not (param.default is None or param.default is _UNSET)
            or not isinstance(param.type, click.types.StringParamType | click.Path)
            or "type_cast_value" in vars(param)
        ):
            continue
        param.type_cast_value = _rejecting_empty(param, param.type_cast_value)


def _rejecting_empty(param: click.Option, cast):
    def type_cast_value(ctx: click.Context, value):
        source = ctx.get_parameter_source(param.name or "")
        if value == "" and source is click.core.ParameterSource.COMMANDLINE:
            raise click.BadParameter(
                "an empty value is not allowed; omit the flag instead.", ctx=ctx, param=param
            )
        return cast(ctx, value)

    return type_cast_value


def _load_payload(data_json: str | None, data_file: str | None) -> dict[str, Any]:
    """Load the JSON object payload from `--data` or `--data-file`.

    A leading '@' in `--data` is the curl-style "@file" convention, not valid
    JSON (no JSON value starts with '@'), so it points at `--data-file` rather
    than letting json.loads fail with an opaque "Expecting value" error.
    """
    if data_json is not None and data_file is not None:
        raise click.UsageError("--data and --data-file are mutually exclusive; pass only one.")
    if data_file:
        try:
            with open(data_file, encoding="utf-8") as f:
                parsed = json.load(f)
        except OSError as exc:
            raise click.UsageError(f"cannot read --data-file: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise click.UsageError(f"invalid JSON in --data-file: {exc}") from exc
    elif data_json:
        if data_json.lstrip().startswith("@"):
            raise click.UsageError(
                "--data does not read files; use --data-file <path> to load a JSON payload "
                "from a file."
            )
        try:
            parsed = json.loads(data_json)
        except json.JSONDecodeError as exc:
            raise click.UsageError(f"invalid JSON in --data: {exc}") from exc
    else:
        raise click.UsageError("Either --data or --data-file is required.")
    if not isinstance(parsed, dict):
        raise click.UsageError(f"Payload must be a JSON object, got {type(parsed).__name__}.")
    return parsed


def _read_file(path: str) -> str:
    """Read a UTF-8 file, turning an open failure into a usage error.

    click.Path(exists=True, readable=True) validates at parse time, but a
    permission edge or a delete-after-check race can still fail the open.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise click.UsageError(f"cannot read {path}: {exc}") from exc


def _parse_expect(pairs: tuple[str, ...]) -> list[tuple[str, str]]:
    """Parse repeatable --expect ATTR=VALUE flags into (attr, value) pairs.

    Split on the FIRST '=' so a VALUE may itself contain '='. A pair missing
    '=' or with an empty attr is a usage error (exit 2); validate before any
    backend call so a typo never costs a round-trip. The attr is trimmed; the
    value is taken verbatim, so any leading/trailing whitespace in a value is
    significant.
    """
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
                raise click.UsageError(f"{flag} value must be an integer, got: {raw!r}") from exc
        elif require_value:
            raise click.UsageError(f"{flag} requires an integer value before ':', got: {raw!r}")
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
    mismatch in CLI order (actual is the raw value, for JSON consumers).
    """
    for attr, expected in pairs:
        if attr not in record:
            return {"attr": attr, "expected": expected, "actual": None}
        actual = record[attr]
        if str(actual) != expected:
            return {"attr": attr, "expected": expected, "actual": actual}
    return None
