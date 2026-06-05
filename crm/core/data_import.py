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
from typing import Any, Generator

from crm.core import entity as entity_mod
from crm.utils.d365_backend import D365Backend, D365Error
from crm.utils.d365_types import BatchOperation, BatchResult


# ── CSV value coercion ───────────────────────────────────────────────────────


def _coerce_csv_value(raw: str) -> Any:
    """Coerce a raw CSV string cell to a Python value.

    Order: empty → None, then bool, then int, then float, else str.
    """
    if raw == "":
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
            yield {k: _coerce_csv_value(v) for k, v in row.items()}


# ── op builders ──────────────────────────────────────────────────────────────


def _build_create_op(entity_set: str, record: dict[str, Any]) -> BatchOperation:
    return BatchOperation(method="POST", url=entity_set, body=record)


def _build_upsert_op(
    entity_set: str,
    record: dict[str, Any],
    id_column: str,
    row_index: int,
) -> BatchOperation:
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
        Column / key that holds the record GUID.  Required when
        *mode* is ``"upsert"``.
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
        ``dry_run``, ``format``.
    """
    # ── guards ──────────────────────────────────────────────────────────────
    if chunk_size < 1:
        raise D365Error(f"chunk_size must be >= 1; got {chunk_size}")
    if continue_on_error and transactional:
        raise D365Error(
            "continue_on_error requires transactional=False "
            "(a server-side changeset is all-or-nothing)"
        )
    if mode == "upsert" and id_column is None:
        raise D365Error("id_column is required when mode='upsert'")
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

    # ── build ops ────────────────────────────────────────────────────────────
    ops: list[BatchOperation] = []
    for row_index, record in enumerate(records, 1):
        if mode == "create":
            ops.append(_build_create_op(entity_set, record))
        else:
            # mode == "upsert"; id_column is not None (guarded above)
            assert id_column is not None  # narrow type for pyright
            ops.append(_build_upsert_op(entity_set, record, id_column, row_index))

    # ── dispatch chunks ──────────────────────────────────────────────────────
    imported = 0
    failed = 0
    chunks = 0

    op_chunks: list[list[BatchOperation]] = list(_chunked(ops, chunk_size)) if ops else []
    for chunk_ops in op_chunks:
        chunks += 1
        results: list[BatchResult] = backend.batch(
            chunk_ops,
            transactional=transactional,
            continue_on_error=continue_on_error,
        )
        for r in results:
            status = r["status"]
            error = r.get("error")
            if 200 <= status < 300:
                imported += 1
            elif error != "dry-run":
                failed += 1

    return {
        "imported": imported,
        "failed": failed,
        "chunks": chunks,
        "entity_set": entity_set,
        "mode": mode,
        "dry_run": backend.dry_run,
        "format": resolved_fmt,
    }
