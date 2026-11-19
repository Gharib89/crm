"""Offline comparison of two row files by key (``crm data compare``, #1342).

Reads two files in any shape ``crm data export`` writes (csv, json, jsonl) or a
plain CSV, pairs their rows by a key, and reports what differs. Touches no org.

Values are normalized before they are compared, so format noise between a CSV
and an export does not read as a difference: numbers by value, date-times as
UTC instants, an empty string as null, a GUID case-insensitively,
the text ``True``/``False`` (what a csv export writes) or ``true``/``false`` as a
boolean, and a lookup by its GUID whether it is written
as ``_<attr>_value`` or ``<attr>@odata.bind: "/<set>(<guid>)"`` (both name the
column ``<attr>``). A number is read from text only in its plain form, so a
leading-zero or ``+``-signed code such as ``"007"`` stays text. Everything else
is compared verbatim: no trimming or case folding, since those would hide real
differences.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from crm.core.data_import import read_csv_raw, read_jsonl
from crm.core.xml_edit import GUID
from crm.utils.d365_backend import D365Error

# How many keys per category the stdout payload carries; the --out file has all.
SAMPLE_CAP = 20

_VALUE_RE = re.compile(r"^_(?P<attr>.+)_value$")
_BIND_SUFFIX = "@odata.bind"
# A csv export writes a boolean as Python's True/False; JSON spells it true/false.
_BOOL_TEXT = {"True": True, "False": False, "true": True, "false": False}
# The plain form a number takes in an export; a leading zero or a "+" marks a code.
_NUMBER_RE = re.compile(r"^-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?$")
_GUID_RE = re.compile(rf"^(?:\{{(?P<braced>{GUID})\}}|(?P<bare>{GUID}))$")
_BIND_RE = re.compile(rf"^/?[A-Za-z_][\w.]*\((?P<g>{GUID})\)$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


class UnknownColumnError(ValueError):
    """A column named by the caller (key, compared column, total) is absent from a file."""


@dataclass
class Rows:
    """One side's rows, keyed by canonical column name, plus its column list."""

    path: Path
    columns: list[str]
    rows: list[dict[str, Any]]


def canonical_column(name: str) -> str | None:
    """Return the name a column is compared under, or None for an annotation.

    A lookup reads as its attribute whichever form carries it: ``_<attr>_value``
    and ``<attr>@odata.bind`` are both ``<attr>``. Any other name holding ``@``
    is an OData annotation and is dropped.
    """
    if name.endswith(_BIND_SUFFIX):
        return name[: -len(_BIND_SUFFIX)] or None
    if "@" in name:
        return None
    m = _VALUE_RE.match(name)
    return m.group("attr") if m else name


def read_rows(path: Path) -> Rows:
    """Read a csv, json or jsonl file (by suffix) into canonical-column rows.

    Raises ``D365Error`` for a file that cannot be read or parsed.
    """
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            raw_columns, raw_rows = read_csv_raw(path)
        elif suffix == ".jsonl":
            raw_rows = list(read_jsonl(path))
            raw_columns = _union_keys(raw_rows)
        elif suffix == ".json":
            raw_rows = _read_json(path)
            raw_columns = _union_keys(raw_rows)
        else:
            raise D365Error(f"unsupported input format {suffix!r} (use .csv, .json or .jsonl)")
    except OSError as exc:
        raise D365Error(f"cannot read {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise D365Error(f"{path}: not UTF-8 text: {exc}") from exc
    except D365Error as exc:
        raise D365Error(f"{path}: {exc}") from exc
    source: dict[str, str] = {}
    for name in raw_columns:
        canon = canonical_column(name)
        if canon is None:
            continue
        if canon in source:
            raise D365Error(
                f"{path}: columns {source[canon]!r} and {name!r} both read as {canon!r}"
            )
        source[canon] = name
    rows = [
        {
            canon: _bind_guid(k, v)
            for k, v in r.items()
            if (canon := canonical_column(k)) is not None
        }
        for r in raw_rows
    ]
    return Rows(path, list(source), rows)


def _bind_guid(name: str, value: Any) -> Any:
    """The GUID of a ``<attr>@odata.bind`` value ``/<set>(<guid>)``; other cells unchanged."""
    if name.endswith(_BIND_SUFFIX) and isinstance(value, str) and (m := _BIND_RE.fullmatch(value)):
        return m.group("g")
    return value


def _read_json(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise D365Error(f"JSON parse error: {exc}") from exc
    if not isinstance(data, list):
        raise D365Error("expected a JSON array of objects")
    rows: list[dict[str, Any]] = []
    for item in cast(list[Any], data):
        if not isinstance(item, dict):
            raise D365Error("expected a JSON array of objects")
        rows.append(cast(dict[str, Any], item))
    return rows


def _union_keys(rows: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(k for r in rows for k in r))


def normalize(value: Any) -> tuple[str, Any] | None:
    """Return the comparable form of a cell; None for null or an empty string.

    Each form is tagged by kind so a boolean never equals the number 1.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float)):
        return ("num", Decimal(repr(value)))
    if isinstance(value, (dict, list)):
        # The same text `data export` writes into a csv cell for a nested value.
        return ("str", json.dumps(value, default=str))
    text = str(value)
    if text in _BOOL_TEXT:
        return ("bool", _BOOL_TEXT[text])
    if _NUMBER_RE.fullmatch(text):
        return ("num", Decimal(text))
    if m := _GUID_RE.fullmatch(text):
        return ("str", (m.group("braced") or m.group("bare")).lower())
    if _DATETIME_RE.match(text):
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return ("str", text)
        # A date-time with no offset is read as UTC.
        return ("dt", (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC))
    return ("str", text)


def compare_rows(
    a: Rows,
    b: Rows,
    key: list[str],
    *,
    columns: list[str] | None = None,
    column_map: dict[str, str] | None = None,
    totals: list[str] | None = None,
) -> dict[str, Any]:
    """Pair A's rows with B's by ``key`` and report every difference.

    Names are A's (canonical); ``column_map`` gives the B name for an A name.
    ``columns`` defaults to every A column whose B name B also has, minus the
    key. A key that repeats on either side, or is blank in any part, is ambiguous
    and is never paired.
    Returns ``{"summary": ..., "missing_in_b": [...], "extra_in_b": [...],
    "mismatched": [...], "ambiguous": [...]}`` with every entry; the summary
    carries the counts, per-column mismatch counts and totals. Raises
    ``UnknownColumnError`` for a named column a file lacks, and ``D365Error``
    for a non-numeric value in a totalled column.
    """
    cmap = {
        canonical_column(k) or k: canonical_column(v) or v for k, v in (column_map or {}).items()
    }
    key = [canonical_column(k) or k for k in key]

    def check(names: list[str], what: str) -> None:
        # An empty json or jsonl export has no columns to check against.
        for name in names:
            if a.columns and name not in a.columns:
                raise UnknownColumnError(f"{what} column {name!r} is not in {a.path}")
            if b.columns and cmap.get(name, name) not in b.columns:
                raise UnknownColumnError(
                    f"{what} column {cmap.get(name, name)!r} is not in {b.path}"
                )

    check(key, "key")
    if columns is None:
        compared = sorted(n for n in a.columns if n not in key and cmap.get(n, n) in b.columns)
    else:
        compared = [canonical_column(c) or c for c in columns]
        check(compared, "compared")
    total_cols = [canonical_column(t) or t for t in totals or []]
    check(total_cols, "total")

    b_key = [cmap.get(k, k) for k in key]
    a_index = _index(a.rows, key)
    b_index = _index(b.rows, b_key)

    def display(row: dict[str, Any], names: list[str]) -> Any:
        values = [row.get(n) for n in names]
        return values[0] if len(key) == 1 else dict(zip(key, values, strict=True))

    missing: list[Any] = []
    extra: list[Any] = []
    ambiguous: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []
    field_counts: Counter[str] = Counter()
    matched = 0
    for k, a_rows in a_index.items():
        b_rows = b_index.get(k, [])
        # A blank key part says nothing about which record a row is.
        if len(a_rows) > 1 or len(b_rows) > 1 or None in k:
            ambiguous.append({"key": display(a_rows[0], key), "a": len(a_rows), "b": len(b_rows)})
        elif not b_rows:
            missing.append(display(a_rows[0], key))
        else:
            matched += 1
            ra, rb = a_rows[0], b_rows[0]
            differing = [
                c for c in compared if normalize(ra.get(c)) != normalize(rb.get(cmap.get(c, c)))
            ]
            if differing:
                field_counts.update(differing)
                diffs = [
                    {"field": c, "a": ra.get(c), "b": rb.get(cmap.get(c, c))} for c in differing
                ]
                mismatched.append({"key": display(ra, key), "fields": diffs})
    for k, b_rows in b_index.items():
        if k in a_index:
            continue
        if len(b_rows) > 1 or None in k:
            ambiguous.append({"key": display(b_rows[0], b_key), "a": 0, "b": len(b_rows)})
        else:
            extra.append(display(b_rows[0], b_key))

    total_out: dict[str, dict[str, Any]] = {}
    for t in total_cols:
        ta, tb = _total(a, t), _total(b, cmap.get(t, t))
        # Strings, so a money total keeps its exact decimal digits; None where the
        # column held no value at all, since an unknown total is not zero.
        total_out[t] = {
            "a": None if ta is None else str(ta),
            "b": None if tb is None else str(tb),
            "equal": ta == tb,
        }

    summary: dict[str, Any] = {
        "a": {"file": str(a.path), "rows": len(a.rows)},
        "b": {"file": str(b.path), "rows": len(b.rows)},
        "key": key,
        "columns": compared,
        "matched": matched,
        "missing_in_b": len(missing),
        "extra_in_b": len(extra),
        "mismatched": len(mismatched),
        "ambiguous": len(ambiguous),
        "field_mismatches": dict(sorted(field_counts.items())),
        "totals": total_out,
        "equal": not (missing or extra or mismatched or ambiguous)
        and all(t["equal"] for t in total_out.values()),
    }
    return {
        "summary": summary,
        "missing_in_b": missing,
        "extra_in_b": extra,
        "mismatched": mismatched,
        "ambiguous": ambiguous,
    }


def samples(report: dict[str, Any]) -> dict[str, list[Any]]:
    """The first ``SAMPLE_CAP`` entries of each category, for the stdout payload."""
    cats = ("missing_in_b", "extra_in_b", "mismatched", "ambiguous")
    return {c: report[c][:SAMPLE_CAP] for c in cats}


def _index(
    rows: list[dict[str, Any]], key: list[str]
) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    index: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        index.setdefault(tuple(normalize(row.get(k)) for k in key), []).append(row)
    return index


def _total(side: Rows, column: str) -> Decimal | None:
    total: Decimal | None = None
    for n, row in enumerate(side.rows, 1):
        norm = normalize(row.get(column))
        if norm is None:
            continue
        if norm[0] != "num":
            raise D365Error(
                f"{side.path}: row {n}: total column {column!r} "
                f"holds non-numeric {row.get(column)!r}"
            )
        total = norm[1] if total is None else total + norm[1]
    return total
