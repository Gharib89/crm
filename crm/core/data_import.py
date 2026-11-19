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
from typing import Any

from crm.core import batch as batch_mod
from crm.core import entity as entity_mod
from crm.core import lookup_bind
from crm.utils.d365_backend import D365Backend, D365Error
from crm.utils.d365_types import BatchOperation, BatchResult

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


def read_jsonl(path: Path) -> Generator[dict[str, Any]]:
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


def read_csv_raw(path: Path, header: list[str] | None = None) -> Generator[dict[str, str]]:
    """Yield a CSV file's rows of verbatim string cells, one at a time.

    *header*, when given, is filled with the column names once iteration starts
    (read it after the first ``next``, or after collecting the rows), so a caller
    learns them even from a file with no rows. Raises ``D365Error`` for a
    row with more cells than the header.
    """
    # utf-8-sig strips a leading UTF-8 BOM (an Excel-saved CSV has one) that would
    # otherwise corrupt the first column's name; pure UTF-8 is unaffected (#683).
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if header is not None:
            header[:] = reader.fieldnames or []
        for row in reader:
            # csv.DictReader collects columns beyond the header under the None
            # key (a list) — a sign of a malformed row; reject it rather than
            # silently dropping or crashing on the list.
            if None in row:
                raise D365Error(f"CSV line {reader.line_num}: more columns than the header")
            yield row


def _read_csv(
    path: Path, string_columns: frozenset[str] = frozenset()
) -> Generator[dict[str, Any]]:
    """Yield one coerced dict per row from a CSV file.

    Columns named in *string_columns* keep their verbatim string value instead of
    being coerced by shape — used for string-typed alternate-key columns so a
    numeric-looking key builds the correct quoted key-URL form (#683).
    """
    for row in read_csv_raw(path):
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


def _invalid_columns(
    backend: D365Backend,
    entity_set: str,
    records: list[dict[str, Any]],
    *,
    mode: str,
    skip: set[str],
) -> dict[int, str]:
    """Check each row's columns against the table's attribute metadata (#1283).

    The dry run's validation: returns ``{row_index: error}`` (1-based) for each
    row that names a column the table lacks, or one not valid for create
    (``create``) or for update (``upsert``). Lookup keys in either shape
    (``<nav>@odata.bind``, ``_<attr>_value``), OData annotation keys, deep-insert
    values (a list or object under a navigation property) and the *skip* columns
    (the id or alternate-key columns, which travel in the URL) are not checked.
    """
    from crm.core import entity_names

    logical = entity_names.resolve_logical_name(backend, entity_set)
    specs = {s.logical_name: s for s in entity_names.attribute_specs(backend, logical)}
    invalid: dict[int, str] = {}
    for row_index, record in enumerate(records, 1):
        unknown: list[str] = []
        not_writable: list[str] = []
        for key, value in record.items():
            if "@" in key or key in skip or isinstance(value, (dict, list)):
                continue
            if key.startswith("_") and key.endswith("_value"):
                continue
            spec = specs.get(key)
            if spec is None:
                unknown.append(key)
            elif not (spec.valid_for_create if mode == "create" else spec.valid_for_update):
                not_writable.append(key)
        problems: list[str] = []
        if unknown:
            problems.append(f"column(s) not on {logical}: {', '.join(unknown)}")
        if not_writable:
            verb = "create" if mode == "create" else "update"
            problems.append(f"column(s) not valid for {verb}: {', '.join(not_writable)}")
        if problems:
            invalid[row_index] = "; ".join(problems)
    return invalid


# ── op builders ──────────────────────────────────────────────────────────────


def _row_chunks(row_ops: list[list[BatchOperation]], chunk_size: int) -> list[range]:
    """Group rows into chunks of at most *chunk_size* rows and ``MAX_BATCH_OPS`` requests.

    A chunk never splits a row, so a row's write and its clears share one
    changeset. A row is never left out: one that alone exceeds the cap gets a
    chunk of its own, and the server reports it.
    """
    chunks: list[range] = []
    start = ops = 0
    for row, row_op_list in enumerate(row_ops):
        if row > start and (
            row - start == chunk_size or ops + len(row_op_list) > batch_mod.MAX_BATCH_OPS
        ):
            chunks.append(range(start, row))
            start, ops = row, 0
        ops += len(row_op_list)
    if row_ops:
        chunks.append(range(start, len(row_ops)))
    return chunks


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
    # Unreachable: import_records requires one of id_column / alt_key for this mode.
    if id_column is None:
        raise D365Error(f"Upsert row {row_index}: id_column or alt_key is required")
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
    # Unreachable: import_records requires one of id_column / alt_key for this mode.
    if id_column is None:
        raise D365Error(f"Delete row {row_index}: id_column or alt_key is required")
    if id_column not in record:
        raise D365Error(f"Delete row {row_index}: missing id_column {id_column!r} in record")
    url = entity_mod.build_record_path(entity_set, str(record[id_column]))
    return BatchOperation(method="DELETE", url=url)


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
        records: list[dict[str, Any]] = list(read_jsonl(path))
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
    # A polymorphic lookup with no target annotation is dropped by the rebind;
    # count the rows that lost each one so the caller can warn (#1283).
    lookups_dropped: dict[str, int] = {}
    invalid: dict[int, str] = {}
    if backend.dry_run and mode != "delete":
        skip: set[str] = set(alt_key or [])
        if id_column:
            skip.add(id_column)
        invalid = _invalid_columns(backend, entity_set, records, mode=mode, skip=skip)
    if any(lookup_bind.needs_binding(r) for r in records):
        resolver = lookup_bind.build_resolver(backend, entity_set)
        bound: list[dict[str, Any]] = []
        for r in records:
            dropped: list[str] = []
            bound.append(lookup_bind.bind_lookups(r, resolver, dropped=dropped))
            for attr in dropped:
                lookups_dropped[attr] = lookups_dropped.get(attr, 0) + 1
        records = bound
    # On-premises v9 rejects `<nav>@odata.bind: null` and fails the whole write
    # (#1325): take each null bind out of the body. An upsert row clears those
    # lookups with `DELETE <record>/<nav>/$ref` beside its PATCH; a create row
    # has nothing to clear.
    row_clears: list[list[str]] = [[] for _ in records]
    if mode != "delete":
        split = [lookup_bind.split_null_binds(backend, r) for r in records]
        records = [body for body, _ in split]
        row_clears = [clears for _, clears in split]

    # ── build ops ────────────────────────────────────────────────────────────
    # One op list per input row (its write, then any $ref clears), plus the
    # row's identity (the record GUID or key segment for upserts) so a
    # per-record failure can be traced back to its row: the batch result itself
    # carries no link to the input position.
    row_ops: list[list[BatchOperation]] = []
    op_ids: list[str | None] = []
    for row_index, record in enumerate(records, 1):
        if mode == "create":
            row_ops.append([_build_create_op(entity_set, record)])
            op_ids.append(None)
            continue
        # upsert / delete both target an existing record by GUID or alternate key.
        build = _build_upsert_op if mode == "upsert" else _build_delete_op
        op = build(entity_set, record, row_index, id_column=id_column, alt_key=alt_key)
        clear_ops = [
            BatchOperation(method="DELETE", url=entity_mod.nav_ref_path(op.get("url", ""), nav))
            for nav in row_clears[row_index - 1]
        ]
        row_ops.append([op, *clear_ops])
        if alt_key is not None:
            # Record the alternate-key segment for failure traceability.
            op_ids.append(entity_mod.format_alternate_key_segment({a: record[a] for a in alt_key}))
        else:
            # Unreachable: the up-front guard requires one of id_column / alt_key.
            if id_column is None:
                raise D365Error(
                    f"Row {row_index}: id_column or alt_key is required when mode={mode!r}"
                )
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

    for chunk_rows in _row_chunks(row_ops, chunk_size):
        chunk_ops = [op for i in chunk_rows for op in row_ops[i]]
        op_rows = [i for i in chunk_rows for _ in row_ops[i]]  # 0-based row per op
        chunks += 1
        try:
            results: list[BatchResult] = backend.batch(
                chunk_ops,
                transactional=transactional,
                continue_on_error=continue_on_error,
            )
        except D365Error as exc:
            # A failed changeset names its failing part by Content-ID, the
            # operation's 1-based position in the chunk (the codec numbers them
            # so when the caller sets none): name the input row (#1283).
            cid = exc.content_id or ""
            pos = int(cid) if cid.isdigit() else 0
            if not transactional or not 1 <= pos <= len(chunk_ops):
                raise
            # Prefix the message in place so the error keeps its class and
            # every field (status, code, body, stage, completed_steps).
            exc.args = (f"row {op_rows[pos - 1] + 1}: {exc}", *exc.args[1:])
            raise
        # A row is imported when every one of its ops succeeded, and fails once,
        # on its first failing op.
        for row in chunk_rows:
            row_results = [r for r, i in zip(results, op_rows, strict=True) if i == row]
            if all(200 <= r["status"] < 300 for r in row_results):
                imported += 1
                continue
            r = next(r for r in row_results if not 200 <= r["status"] < 300)
            status = r["status"]
            error = r.get("error")
            if error != "dry-run":
                failed += 1
                entry: dict[str, Any] = {"index": row + 1}
                rec_id = op_ids[row]
                if rec_id is not None:
                    entry["id"] = rec_id
                entry["status"] = status
                entry["error"] = error or f"HTTP {status}"
                # Enrich an alternate-key collision with the entity's key schema
                # and this row's colliding values (#347) — the same best-effort
                # hint `entity create --json` attaches, now on bulk failures too.
                if (
                    enrich_alt_key
                    and batch_mod.batch_error_code(r.get("body")) == entity_mod.ALT_KEY_ERROR_CODE
                ):
                    if not alt_key_schema_fetched:
                        alt_key_schema = entity_mod.lookup_alternate_key_schema(backend, entity_set)
                        alt_key_schema_fetched = True
                    if alt_key_schema is not None:
                        entry.update(entity_mod.dupe_key_hint(alt_key_schema, records[row]))
                failures.append(entry)

    result: dict[str, Any] = {
        "imported": imported,
        "failed": failed,
        "chunks": chunks,
        "entity_set": entity_set,
        "mode": mode,
        "dry_run": backend.dry_run,
        "format": resolved_fmt,
        "failures": failures,
    }
    if backend.dry_run:
        # A validation finding has no server answer, so its status is None,
        # never a number an HTTP status could be.
        failures.extend(
            {"index": i, "status": None, "error": err} for i, err in sorted(invalid.items())
        )
        result["failed"] = len(failures)
        result["would_import"] = len(records) - len(invalid)
    if lookups_dropped:
        result["lookups_dropped"] = lookups_dropped
    return result
