"""Bulk-import records via the D365 Web API $batch endpoint.

All writes are routed through :meth:`~crm.utils.d365_backend.D365Backend.batch`
— the only on-prem bulk mechanism.  The public entry-point is
:func:`import_records`.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast

from crm.core import entity as entity_mod
from crm.core import lookup_bind
from crm.utils.d365_backend import D365Backend, D365Error
from crm.utils.d365_types import BatchOperation, BatchResult


def _batch_error_code(body: dict[str, Any] | str | None) -> str | None:
    """Extract the D365 error code from a failed batch sub-op's parsed body.

    The backend already parses the OData error envelope into ``BatchResult.body``;
    return ``body["error"]["code"]`` when present (e.g. ``0x80060892``), else None.
    """
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            code = cast("dict[str, Any]", err).get("code")
            if isinstance(code, str):
                return code
    return None


# ── CSV value coercion ───────────────────────────────────────────────────────


def _coerce_csv_value(raw: str | None, *, as_string: bool = False) -> Any:
    """Coerce a raw CSV string cell to a Python value.

    Order: empty → None, then bool, then int, then float, else str.
    A missing cell (``None``, as ``csv.DictReader`` yields for a short row) is
    treated as empty. With *as_string* the (non-empty) cell keeps its verbatim
    string identity — no bool/int/float coercion — so a numeric-looking value on
    a string-typed alternate-key column (an account number like ``"10023"``, or a
    leading-zero code) is not turned into a number that would build the wrong
    key-URL form (#683).
    """
    if raw is None or raw == "":
        return None
    if as_string:
        return raw
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        f = float(raw)
    except ValueError:
        pass
    else:
        if math.isfinite(f):
            return f
        # non-finite ("NaN"/"inf"/"Infinity") → treat as plain string, fall through
    return raw


# ── record readers ───────────────────────────────────────────────────────────


def _read_jsonl(path: Path) -> Generator[dict[str, Any]]:
    """Yield one JSON object per non-blank line from a JSONL file."""
    # utf-8-sig tolerates a UTF-8 BOM (Excel/Windows editors add one) so it can't
    # corrupt the first object's first key; pure UTF-8 is unaffected (#683).
    with path.open(encoding="utf-8-sig") as fh:
        for lineno, line in enumerate(fh, 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise D365Error(f"JSONL parse error at line {lineno}: {exc}") from exc
            if not isinstance(obj, dict):
                raise D365Error(
                    f"JSONL line {lineno}: expected JSON object, got {type(obj).__name__}"
                )
            yield obj


def _read_csv(
    path: Path, string_columns: frozenset[str] = frozenset()
) -> Generator[dict[str, Any]]:
    """Yield one coerced dict per row from a CSV file.

    Columns named in *string_columns* keep their verbatim string value instead of
    being coerced by shape — used for string-typed alternate-key columns so a
    numeric-looking key builds the correct quoted key-URL form (#683).
    """
    # utf-8-sig strips a leading UTF-8 BOM (an Excel-saved CSV has one) that would
    # otherwise corrupt the first column's name; pure UTF-8 is unaffected (#683).
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            # csv.DictReader collects columns beyond the header under the None
            # key (a list) — a sign of a malformed row; reject it rather than
            # silently dropping or crashing on the list.
            if None in row:
                raise D365Error(f"CSV line {reader.line_num}: more columns than the header")
            yield {k: _coerce_csv_value(v, as_string=k in string_columns) for k, v in row.items()}


def _string_key_columns(backend: D365Backend, entity_set: str, attrs: list[str]) -> set[str]:
    """Names in *attrs* whose D365 column type renders as a quoted string literal
    in an alternate-key URL (``String`` / ``Memo``).

    A numeric-looking CSV cell for such a column must keep its string identity:
    coerced to a number it builds a bare ``key=10023`` path where Dataverse needs
    the quoted ``key='10023'`` form, so the upsert/delete would silently miss
    (#683). Resolves the set→logical name through the shared cached name-map
    seam (#261) — warm on this path, since :func:`resolve_alternate_key` already
    resolved the same entity set one step earlier — then reads the attribute
    types. Returns an empty set — the caller then falls back to shape-based
    coercion (prior behavior) — when the entity set is unknown *or* the metadata
    reads fail: this check adds a new metadata dependency to a path that worked
    without it, so a transient/permission failure must degrade to the old
    behavior rather than hard-fail a previously-working import.
    """
    # Local imports keep the core package import-cycle-free (mirrors
    # lookup_alternate_key_schema in crm.core.entity).
    from crm.core import entity_names
    from crm.core import metadata as meta_mod

    try:
        logical = entity_names.resolve_logical_name(backend, entity_set)
        wanted = set(attrs)
        return {
            a["LogicalName"]
            for a in meta_mod.list_attributes(backend, logical)
            if a.get("LogicalName") in wanted and a.get("AttributeType") in ("String", "Memo")
        }
    except D365Error:
        return set()


# ── op builders ──────────────────────────────────────────────────────────────


def _build_create_op(entity_set: str, record: dict[str, Any]) -> BatchOperation:
    return BatchOperation(method="POST", url=entity_set, body=record)


def _build_upsert_op(
    entity_set: str,
    record: dict[str, Any],
    row_index: int,
    *,
    id_column: str | None = None,
    alt_key: list[str] | None = None,
) -> BatchOperation:
    if alt_key is not None:
        key_values: dict[str, Any] = {}
        for attr in alt_key:
            if attr not in record:
                raise D365Error(f"Upsert row {row_index}: missing key column {attr!r} in record")
            key_values[attr] = record[attr]
        # Strip the key attributes from the body — Dataverse identifies the
        # record from the URL key and rejects a differing body value.
        body = {k: v for k, v in record.items() if k not in key_values}
        url = entity_mod.build_alternate_key_path(entity_set, key_values)
        return BatchOperation(method="PATCH", url=url, body=body)
    assert id_column is not None  # narrowed by import_records guard
    if id_column not in record:
        raise D365Error(f"Upsert row {row_index}: missing id_column {id_column!r} in record")
    body = {k: v for k, v in record.items() if k != id_column}
    url = entity_mod.build_record_path(entity_set, str(record[id_column]))
    return BatchOperation(method="PATCH", url=url, body=body)


def _build_delete_op(
    entity_set: str,
    record: dict[str, Any],
    row_index: int,
    *,
    id_column: str | None = None,
    alt_key: list[str] | None = None,
) -> BatchOperation:
    # DELETE carries no body; the record path comes from the alternate key or
    # the GUID column, resolved exactly as upsert does.
    if alt_key is not None:
        key_values: dict[str, Any] = {}
        for attr in alt_key:
            if attr not in record:
                raise D365Error(f"Delete row {row_index}: missing key column {attr!r} in record")
            key_values[attr] = record[attr]
        url = entity_mod.build_alternate_key_path(entity_set, key_values)
        return BatchOperation(method="DELETE", url=url)
    assert id_column is not None  # narrowed by import_records guard
    if id_column not in record:
        raise D365Error(f"Delete row {row_index}: missing id_column {id_column!r} in record")
    url = entity_mod.build_record_path(entity_set, str(record[id_column]))
    return BatchOperation(method="DELETE", url=url)


# ── chunking ─────────────────────────────────────────────────────────────────


def _chunked(items: list[BatchOperation], size: int) -> Generator[list[BatchOperation]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


# ── public API ───────────────────────────────────────────────────────────────


def import_records(
    backend: D365Backend,
    entity_set: str,
    input_path: str | Path,
    *,
    fmt: str | None = None,
    mode: str = "create",
    id_column: str | None = None,
    alt_key: list[str] | None = None,
    chunk_size: int = 100,
    transactional: bool = True,
    continue_on_error: bool = False,
    enrich_alt_key: bool = True,
) -> dict[str, Any]:
    """Import records from a JSONL or CSV file via ``$batch``.

    Parameters
    ----------
    backend:
        Configured :class:`~crm.utils.d365_backend.D365Backend`.
    entity_set:
        OData entity-set name (e.g. ``"accounts"``).
    input_path:
        Path to the JSONL or CSV source file.
    fmt:
        ``"jsonl"`` or ``"csv"``.  Inferred from the file suffix when *None*:
        ``.csv`` → ``"csv"``, everything else → ``"jsonl"``.
    mode:
        ``"create"`` (POST), ``"upsert"`` (PATCH by GUID/alternate key), or
        ``"delete"`` (DELETE by GUID/alternate key).
    id_column:
        Column / key that holds the record GUID.  Required for ``"upsert"`` and
        ``"delete"`` unless *alt_key* is given (mutually exclusive with it).
    alt_key:
        Alternate-key attribute(s) (already validated against entity metadata
        by the caller) to upsert by instead of the primary GUID.  Each row's
        record path becomes ``set(attr='value',...)`` and the key attributes are
        stripped from the body.
    chunk_size:
        Records per ``$batch`` call.  Must be ≥ 1.
    transactional:
        Wrap each chunk in a single changeset (atomic).
    continue_on_error:
        Ask the server to continue past individual failures.
        Mutually exclusive with ``transactional=True``.
    enrich_alt_key:
        Attach the alternate-key collision hint to failed rows (see below). The
        *when-to-pay* gate: enrichment costs a metadata lookup, so the command
        passes ``ctx.json_mode`` — the human render drops ``failures`` and never
        shows the hint, so it must not pay for it. ``True`` by default for
        non-command callers that consume the structured result.

    Returns:
    -------
    dict
        Keys: ``imported``, ``failed``, ``chunks``, ``entity_set``, ``mode``,
        ``dry_run``, ``format``, and ``failures`` — a list (``[]`` when none) of
        ``{index, id?, status, error}`` entries, one per failed record, where
        ``index`` is the 1-based input row, ``id`` the record GUID (upserts only),
        and ``status``/``error`` the server's HTTP status and message. A row that
        failed with the alternate-key collision code (``0x80060892``) also carries
        the best-effort ``alternate_keys`` (and ``primary_id_hint`` when relevant)
        enrichment — the same hint ``entity create --json`` attaches (#347); the
        key schema is fetched once per import and the colliding ``payload_values``
        are per row.
    """
    # ── guards ──────────────────────────────────────────────────────────────
    if chunk_size < 1:
        raise D365Error(f"chunk_size must be >= 1; got {chunk_size}")
    if continue_on_error and transactional:
        raise D365Error(
            "continue_on_error requires transactional=False "
            "(a server-side changeset is all-or-nothing)"
        )
    if mode in ("upsert", "delete"):
        if id_column is not None and alt_key is not None:
            raise D365Error(
                "id_column and alt_key are mutually exclusive "
                "(target by primary GUID OR by alternate key, not both)"
            )
        if id_column is None and alt_key is None:
            raise D365Error(f"id_column or alt_key is required when mode={mode!r}")
    elif alt_key is not None:
        raise D365Error("alt_key is only valid when mode='upsert' or mode='delete'")
    if mode not in ("create", "upsert", "delete"):
        raise D365Error(f"Unsupported mode: {mode!r} (use 'create', 'upsert', or 'delete')")

    # ── format ───────────────────────────────────────────────────────────────
    path = Path(input_path)
    resolved_fmt: str
    if fmt is None:
        resolved_fmt = "csv" if path.suffix.lower() == ".csv" else "jsonl"
    else:
        resolved_fmt = fmt.lower()
    if resolved_fmt not in ("jsonl", "csv"):
        raise D365Error(f"Unsupported import format: {resolved_fmt!r} (use 'jsonl' or 'csv')")

    # ── read records ─────────────────────────────────────────────────────────
    if resolved_fmt == "jsonl":
        records: list[dict[str, Any]] = list(_read_jsonl(path))
    else:
        # String-typed alternate-key columns keep their CSV text verbatim rather
        # than being coerced by shape — a numeric-looking key needs the quoted
        # key-URL form (#683). Consult column metadata rather than guessing.
        string_cols: frozenset[str] = (
            frozenset(_string_key_columns(backend, entity_set, alt_key))
            if alt_key is not None
            else frozenset()
        )
        records = list(_read_csv(path, string_cols))

    # ── rebind READ-format lookups ─────────────────────────────────────────────
    # `data export` / `query odata` emit lookups as read-only `_<attr>_value`
    # GUIDs (plus annotations), which the Web API cannot write. Rewrite them to
    # `<nav>@odata.bind` so an export round-trips on import (#333). The metadata
    # read happens once and only when a record actually carries such a key.
    if any(lookup_bind.needs_binding(r) for r in records):
        resolver = lookup_bind.build_resolver(backend, entity_set)
        records = [lookup_bind.bind_lookups(r, resolver) for r in records]

    # ── build ops ────────────────────────────────────────────────────────────
    # Track each op's source-row identity (1-based input index, plus the record
    # GUID for upserts) so a per-record failure can be traced back to its row —
    # the batch result itself carries no link to the input position.
    ops: list[BatchOperation] = []
    op_ids: list[str | None] = []
    for row_index, record in enumerate(records, 1):
        if mode == "create":
            ops.append(_build_create_op(entity_set, record))
            op_ids.append(None)
            continue
        # upsert / delete both target an existing record by GUID or alternate key.
        build = _build_upsert_op if mode == "upsert" else _build_delete_op
        ops.append(build(entity_set, record, row_index, id_column=id_column, alt_key=alt_key))
        if alt_key is not None:
            # Record the alternate-key segment for failure traceability.
            op_ids.append(entity_mod.format_alternate_key_segment({a: record[a] for a in alt_key}))
        else:
            assert id_column is not None  # narrow type for pyright (guarded above)
            op_ids.append(str(record[id_column]))

    # ── dispatch chunks ──────────────────────────────────────────────────────
    imported = 0
    failed = 0
    chunks = 0
    failures: list[dict[str, Any]] = []

    # Alternate-key schema for collision enrichment: fetched at most once per
    # import (the schema is per-entity, identical for every row), lazily on the
    # first 0x80060892 failure. `None` after a fetch means "lookup unavailable".
    alt_key_schema: entity_mod.AltKeySchema | None = None
    alt_key_schema_fetched = False

    op_chunks: list[list[BatchOperation]] = list(_chunked(ops, chunk_size)) if ops else []
    row_offset = 0  # 0-based index into ops/op_ids of the current chunk's first op
    for chunk_ops in op_chunks:
        chunks += 1
        results: list[BatchResult] = backend.batch(
            chunk_ops,
            transactional=transactional,
            continue_on_error=continue_on_error,
        )
        for pos, r in enumerate(results):
            status = r["status"]
            error = r.get("error")
            if 200 <= status < 300:
                imported += 1
            elif error != "dry-run":
                failed += 1
                op_index = row_offset + pos
                entry: dict[str, Any] = {"index": op_index + 1}
                rec_id = op_ids[op_index] if op_index < len(op_ids) else None
                if rec_id is not None:
                    entry["id"] = rec_id
                entry["status"] = status
                entry["error"] = error or f"HTTP {status}"
                # Enrich an alternate-key collision with the entity's key schema
                # and this row's colliding values (#347) — the same best-effort
                # hint `entity create --json` attaches, now on bulk failures too.
                if (
                    enrich_alt_key
                    and _batch_error_code(r.get("body")) == entity_mod.ALT_KEY_ERROR_CODE
                    and op_index < len(records)
                ):
                    if not alt_key_schema_fetched:
                        alt_key_schema = entity_mod.lookup_alternate_key_schema(backend, entity_set)
                        alt_key_schema_fetched = True
                    if alt_key_schema is not None:
                        entry.update(entity_mod.dupe_key_hint(alt_key_schema, records[op_index]))
                failures.append(entry)
        row_offset += len(chunk_ops)

    return {
        "imported": imported,
        "failed": failed,
        "chunks": chunks,
        "entity_set": entity_set,
        "mode": mode,
        "dry_run": backend.dry_run,
        "format": resolved_fmt,
        "failures": failures,
    }
