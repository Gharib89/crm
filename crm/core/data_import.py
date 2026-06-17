"""Bulk-import records via the D365 Web API $batch endpoint.

All writes are routed through :meth:`~crm.utils.d365_backend.D365Backend.batch`
— the only on-prem bulk mechanism.  The public entry-point is
:func:`import_records`.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Generator, cast

from crm.core import entity as entity_mod
from crm.core import lookup_bind
from crm.utils.d365_backend import D365Backend, D365Error
from crm.utils.d365_types import BatchOperation, BatchResult


def _batch_error_code(body: "dict[str, Any] | str | None") -> str | None:
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


def _coerce_csv_value(raw: str | None) -> Any:
    """Coerce a raw CSV string cell to a Python value.

    Order: empty → None, then bool, then int, then float, else str.
    A missing cell (``None``, as ``csv.DictReader`` yields for a short row) is
    treated as empty.
    """
    if raw is None or raw == "":
        return None
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


def _read_jsonl(path: Path) -> Generator[dict[str, Any], None, None]:
    """Yield one JSON object per non-blank line from a JSONL file."""
    with path.open(encoding="utf-8") as fh:
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


def _read_csv(path: Path) -> Generator[dict[str, Any], None, None]:
    """Yield one coerced dict per row from a CSV file."""
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            # csv.DictReader collects columns beyond the header under the None
            # key (a list) — a sign of a malformed row; reject it rather than
            # silently dropping or crashing on the list.
            if None in row:
                raise D365Error(
                    f"CSV line {reader.line_num}: more columns than the header"
                )
            yield {k: _coerce_csv_value(v) for k, v in row.items()}


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
                raise D365Error(
                    f"Upsert row {row_index}: missing key column {attr!r} in record"
                )
            key_values[attr] = record[attr]
        # Strip the key attributes from the body — Dataverse identifies the
        # record from the URL key and rejects a differing body value.
        body = {k: v for k, v in record.items() if k not in key_values}
        url = entity_mod.build_alternate_key_path(entity_set, key_values)
        return BatchOperation(method="PATCH", url=url, body=body)
    assert id_column is not None  # narrowed by import_records guard
    if id_column not in record:
        raise D365Error(
            f"Upsert row {row_index}: missing id_column {id_column!r} in record"
        )
    body = {k: v for k, v in record.items() if k != id_column}
    url = entity_mod.build_record_path(entity_set, str(record[id_column]))
    return BatchOperation(method="PATCH", url=url, body=body)


# ── chunking ─────────────────────────────────────────────────────────────────


def _chunked(
    items: list[BatchOperation], size: int
) -> Generator[list[BatchOperation], None, None]:
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
        ``"create"`` (POST) or ``"upsert"`` (PATCH by GUID).
    id_column:
        Column / key that holds the record GUID.  Required for ``"upsert"``
        unless *alt_key* is given (mutually exclusive with it).
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

    Returns
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
    if mode == "upsert":
        if id_column is not None and alt_key is not None:
            raise D365Error(
                "id_column and alt_key are mutually exclusive "
                "(upsert by primary GUID OR by alternate key, not both)"
            )
        if id_column is None and alt_key is None:
            raise D365Error("id_column or alt_key is required when mode='upsert'")
    elif alt_key is not None:
        raise D365Error("alt_key is only valid when mode='upsert'")
    if mode not in ("create", "upsert"):
        raise D365Error(f"Unsupported mode: {mode!r} (use 'create' or 'upsert')")

    # ── format ───────────────────────────────────────────────────────────────
    path = Path(input_path)
    resolved_fmt: str
    if fmt is None:
        resolved_fmt = "csv" if path.suffix.lower() == ".csv" else "jsonl"
    else:
        resolved_fmt = fmt.lower()
    if resolved_fmt not in ("jsonl", "csv"):
        raise D365Error(
            f"Unsupported import format: {resolved_fmt!r} (use 'jsonl' or 'csv')"
        )

    # ── read records ─────────────────────────────────────────────────────────
    if resolved_fmt == "jsonl":
        records: list[dict[str, Any]] = list(_read_jsonl(path))
    else:
        records = list(_read_csv(path))

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
        elif alt_key is not None:
            op = _build_upsert_op(entity_set, record, row_index, alt_key=alt_key)
            ops.append(op)
            # Record the alternate-key segment for failure traceability.
            op_ids.append(entity_mod.format_alternate_key_segment(
                {a: record[a] for a in alt_key}
            ))
        else:
            # mode == "upsert" by primary GUID; id_column is not None (guarded above)
            assert id_column is not None  # narrow type for pyright
            ops.append(_build_upsert_op(entity_set, record, row_index, id_column=id_column))
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
                if (_batch_error_code(r.get("body")) == entity_mod.ALT_KEY_ERROR_CODE
                        and op_index < len(records)):
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
