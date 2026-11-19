"""Data bulk import/export commands."""

# pyright: basic
from __future__ import annotations

import json
from pathlib import Path

import click

from crm.cli import CLIContext, _complete_entity_set_names, pass_ctx
from crm.commands._helpers import (
    _confirm_destructive,
    _destructive_option,
    _journal,
    _output_option,
    d365_errors,
)
from crm.core import bulk_delete as bulk_delete_mod
from crm.core import data_compare as compare_mod
from crm.core import data_import as import_mod
from crm.core import entity as entity_mod
from crm.core import export as export_mod


@click.group("data")
def data_group():
    """Bulk CSV/JSON dataset import/export."""


@data_group.command("export")
@click.argument("entity_set", shell_complete=_complete_entity_set_names)
@_output_option(required=True)
@click.option("--select", multiple=True)
@click.option("--filter", "filter_", help="OData $filter.")
@click.option("--page-size", type=int, default=500)
@click.option("--max-records", type=int, default=None)
@click.option("--format", "fmt", type=click.Choice(["csv", "json", "jsonl"]))
@click.option(
    "--include-lookups",
    is_flag=True,
    default=False,
    help="Add each lookup's lookuplogicalname annotation to json output and the "
    "_<attr>_value columns to csv output (jsonl always carries the annotation).",
)
@pass_ctx
def data_export(
    ctx: CLIContext,
    entity_set,
    output,
    select,
    filter_,
    page_size,
    max_records,
    fmt,
    include_lookups,
):
    """Export records from an entity set to a CSV/JSON/JSONL file.

    Pages through the entity set (``--page-size`` per request, capped by
    ``--max-records``), narrowing columns with ``--select`` and rows with an OData
    ``--filter``, and writes the result to ``--output``. ``--format`` is optional —
    when omitted it is inferred from the output file's suffix (``.csv`` → csv,
    ``.jsonl`` → jsonl, else json). ``jsonl`` writes one record per line with its
    lookups' target annotations, the shape ``data import`` reads back.
    """
    select_list: list[str] = []
    for s in select:
        select_list.extend(part.strip() for part in s.split(",") if part.strip())
    with d365_errors(ctx):
        info = export_mod.export_records(
            ctx.backend(),
            entity_set,
            output,
            select=select_list or None,
            filter_=filter_,
            page_size=page_size,
            max_records=max_records,
            fmt=fmt,
            include_lookups=include_lookups,
        )
    ctx.emit(True, data=info)


@data_group.command("import")
@click.argument("entity_set", shell_complete=_complete_entity_set_names)
@click.argument("input_file", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["jsonl", "csv"]),
    default=None,
    help="Input format; inferred from suffix when omitted (.csv→csv, else jsonl).",
)
@click.option(
    "--mode",
    type=click.Choice(["create", "upsert", "delete"]),
    default="create",
    help="create=POST new records; upsert=PATCH by GUID (--id-column) "
    "or by alternate key (--key); delete=DELETE by GUID (--id-column) "
    "or by alternate key (--key).",
)
@click.option(
    "--id-column",
    default=None,
    help="Column/key holding the record GUID (for --mode upsert/delete; "
    "mutually exclusive with --key).",
)
@click.option(
    "--key",
    "alt_key",
    default=None,
    metavar="ATTR[,ATTR...]",
    help="Upsert by an alternate key instead of the primary GUID: one "
    "attribute, or a comma-separated composite key. Values are read "
    "from each row. Mutually exclusive with --id-column.",
)
@click.option(
    "--chunk-size",
    type=int,
    default=100,
    help="Records per $batch call (each chunk is one transactional changeset by default).",
)
@click.option(
    "--no-transaction",
    is_flag=True,
    default=False,
    help="Send each op as a top-level operation; no changeset wrapping.",
)
@click.option(
    "--continue-on-error",
    is_flag=True,
    default=False,
    help="Send Prefer: odata.continue-on-error (requires --no-transaction).",
)
@click.option(
    "--fail-on-error",
    is_flag=True,
    default=False,
    help="Exit 1 when any row failed (a dry run's validation findings count); "
    "the output still carries the counts and per-row failures. Without it a "
    "run with failed rows exits 0.",
)
@_destructive_option
@pass_ctx
def data_import(
    ctx: CLIContext,
    entity_set,
    input_file,
    fmt,
    mode,
    id_column,
    alt_key,
    chunk_size,
    no_transaction,
    continue_on_error,
    fail_on_error,
    yes,
):
    """Bulk-import records from a JSONL/CSV file via $batch.

    Under ``--dry-run`` nothing is written: each row's columns are checked
    against the table's attribute metadata (a column the table lacks, or one not
    valid for create or update in the chosen mode), offending rows are reported
    in ``failures`` with ``status`` null, and ``would_import`` counts the rows
    that passed. Lookup targets are not checked.
    """
    if continue_on_error and not no_transaction:
        raise click.UsageError(
            "--continue-on-error requires --no-transaction; "
            "Prefer: odata.continue-on-error is meaningless inside a changeset."
        )
    if mode in ("upsert", "delete"):
        if id_column and alt_key:
            raise click.UsageError(
                f"--id-column and --key are mutually exclusive: {mode} by the "
                "primary GUID OR by an alternate key, not both."
            )
        if not id_column and not alt_key:
            raise click.UsageError(
                f"--mode {mode} requires --id-column (the GUID column) or --key (an alternate key)."
            )
    elif alt_key:
        raise click.UsageError("--key applies only to --mode upsert or --mode delete.")
    requested_key = [a.strip() for a in alt_key.split(",") if a.strip()] if alt_key else []
    if alt_key and not requested_key:
        raise click.UsageError("--key must name at least one attribute.")
    if mode == "delete":
        _confirm_destructive(
            ctx,
            "records",
            entity_set,
            yes,
            message=(f"This permanently deletes {entity_set} records from {input_file}. Continue?"),
        )
    with d365_errors(ctx):
        alt_key_attrs = (
            entity_mod.resolve_alternate_key(ctx.backend(), entity_set, requested_key)
            if requested_key
            else None
        )
        info = import_mod.import_records(
            ctx.backend(),
            entity_set,
            input_file,
            fmt=fmt,
            mode=mode,
            id_column=id_column,
            alt_key=alt_key_attrs,
            chunk_size=chunk_size,
            transactional=not no_transaction,
            continue_on_error=continue_on_error,
            # When-to-pay gate: only json mode surfaces the alt-key hint, so only
            # it pays for the enrichment metadata lookup (human render drops it).
            enrich_alt_key=ctx.json_mode,
        )
    failures = info.get("failures") or []
    warnings: list[str] = [
        f"lookup {attr!r} dropped from {n} row(s): a polymorphic lookup needs its "
        "_<attr>_value@Microsoft.Dynamics.CRM.lookuplogicalname annotation to name the target."
        for attr, n in (info.get("lookups_dropped") or {}).items()
    ]
    if failures:
        warnings.append(
            f"{info['failed']} record(s) failed to import "
            f"({info['imported']} succeeded across {info['chunks']} chunk(s))."
        )
        if not ctx.json_mode:
            # Human mode: spell out each failed row (JSON mode carries the same
            # detail in data.failures, so don't duplicate it into warnings there).
            for f in failures:
                # Always lead with the input row (the reliable locator back to the
                # source file); append the record id for upserts where it's known.
                where = f"row {f['index']}"
                if "id" in f:
                    where += f" (id {f['id']})"
                # A dry-run validation finding has no HTTP status (None).
                status = "" if f["status"] is None else f"HTTP {f['status']} — "
                warnings.append(f"  {where}: {status}{f['error']}")
    data = info
    if not ctx.json_mode:
        # The failures list renders as an unreadable truncated repr in the human
        # status dump; we surface it via the warnings above instead.
        data = {k: v for k, v in info.items() if k != "failures"}
    _journal(ctx, entity_set, info)
    if fail_on_error and info["failed"]:
        ctx.emit(
            False,
            data=data,
            error=f"{info['failed']} record(s) failed (--fail-on-error).",
            warnings=warnings or None,
        )
    ctx.emit(True, data=data, warnings=warnings or None)


def _split_names(values: tuple[str, ...]) -> list[str]:
    return [part.strip() for v in values for part in v.split(",") if part.strip()]


@data_group.command("compare")
@click.argument("a", type=click.Path(dir_okay=False, path_type=Path))
@click.argument("b", type=click.Path(dir_okay=False, path_type=Path))
@click.option(
    "--key",
    required=True,
    metavar="ATTR[,ATTR...]",
    help="Column, or comma-separated composite key, that pairs A's rows with B's.",
)
@click.option(
    "--columns",
    multiple=True,
    metavar="COL[,COL...]",
    help="Compare only these columns (A's names). Default: every column both "
    "files carry, minus the key and OData annotations.",
)
@click.option(
    "--map",
    "mappings",
    multiple=True,
    metavar="A_COL=B_COL",
    help="Read B's column B_COL where A's is named A_COL; repeatable.",
)
@click.option(
    "--total",
    multiple=True,
    metavar="COL[,COL...]",
    help="Sum this numeric column on each side (A's name); repeatable.",
)
@click.option(
    "--out",
    "out_file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the full per-key report to this JSON file.",
)
@click.option(
    "--fail-on-diff",
    is_flag=True,
    default=False,
    help="Exit 1 when any difference is found. Without it differences exit 0.",
)
@pass_ctx
def data_compare(
    ctx: CLIContext,
    a: Path,
    b: Path,
    key: str,
    columns: tuple[str, ...],
    mappings: tuple[str, ...],
    total: tuple[str, ...],
    out_file: Path | None,
    fail_on_diff: bool,
):
    """Compare two row files A and B by key: offline, touches no org.

    Each file is a `data export` output (csv, json or jsonl, by suffix) or a
    plain CSV. Rows pair by --key; a key repeated on either side, or blank in
    any part, is ambiguous and never paired. Values are normalized first: numbers by value (a
    leading-zero code such as 007 stays text), date-times
    as UTC instants, an empty string as null, True/False or true/false text as
    a boolean, a GUID case-insensitively, and a
    lookup by its GUID whether written `_<attr>_value` or `<attr>@odata.bind`
    (both read as column `<attr>`); other text is compared verbatim. A field
    a sparse JSON row omits differs from an explicit null. Returns
    counts (rows per side, matched, missing in B, extra in B, mismatched,
    ambiguous), per-column mismatch counts, the --total sums and up to 20 keys
    per category; --out writes every entry. An unknown key or column exits 2,
    an unreadable file 1.
    """
    column_map: dict[str, str] = {}
    for m in mappings:
        left, sep, right = m.partition("=")
        if not sep or not left.strip() or not right.strip():
            raise click.BadParameter(f"{m!r} is not A_COL=B_COL", param_hint="--map")
        column_map[left.strip()] = right.strip()
    key_cols = _split_names((key,))
    if not key_cols:
        raise click.BadParameter("must name at least one column", param_hint="--key")
    try:
        with d365_errors(ctx):
            report = compare_mod.compare_rows(
                compare_mod.read_rows(a),
                compare_mod.read_rows(b),
                key_cols,
                columns=_split_names(columns) or None,
                column_map=column_map,
                totals=_split_names(total),
            )
    except compare_mod.UnknownColumnError as exc:
        raise click.UsageError(str(exc)) from exc
    if out_file is not None:
        try:
            out_file.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        except OSError as exc:
            ctx.emit(False, error=f"--out {out_file}: {exc}")
            return
    summary = report["summary"]
    data = {
        **summary,
        "samples": compare_mod.samples(report),
        "out": str(out_file) if out_file else None,
    }
    if fail_on_diff and not summary["equal"]:
        ctx.emit(False, data=data, error="the files differ (--fail-on-diff).")
        return
    ctx.emit(True, data=data)


@data_group.command("delete")
@click.argument("entity_set", shell_complete=_complete_entity_set_names)
@click.option(
    "--fetchxml",
    "fetch_xml",
    default=None,
    help="FetchXML <fetch> document selecting the records to delete.",
)
@click.option(
    "--fetchxml-file",
    type=click.File("r", encoding="utf-8-sig"),
    default=None,
    help="Read the FetchXML from a file instead of --fetchxml.",
)
@click.option(
    "--job-name",
    "job_name",
    default=None,
    help="Name for the bulk-delete system job (default derived from the entity).",
)
@click.option(
    "--wait",
    is_flag=True,
    default=False,
    help="Block until the job finishes, then report succeeded/failed counts.",
)
@click.option(
    "--timeout",
    type=int,
    default=None,
    help="Seconds to wait under --wait (default: the profile's async timeout).",
)
@_destructive_option
@pass_ctx
def data_delete(
    ctx: CLIContext, entity_set, fetch_xml, fetchxml_file, job_name, wait, timeout, yes
):
    """Submit a server-side BulkDelete job for records matching a FetchXML query.

    The Web API's BulkDelete action takes a QueryExpression, so the FetchXML is
    converted server-side before submission (raw OData $filter is not accepted).
    Returns the job's async-operation id; pass --wait to poll to completion.
    """
    if bool(fetch_xml) == bool(fetchxml_file):
        raise click.UsageError("Provide exactly one of --fetchxml or --fetchxml-file.")
    if fetchxml_file is not None:
        fetch_xml = fetchxml_file.read()
    _confirm_destructive(
        ctx,
        "records",
        entity_set,
        yes,
        message=(
            f"This submits a BulkDelete job that permanently deletes all "
            f"{entity_set} records matching the FetchXML query. Continue?"
        ),
    )
    with d365_errors(ctx):
        result = bulk_delete_mod.bulk_delete(
            ctx.backend(),
            entity_set,
            fetch_xml,
            job_name=job_name,
            wait=wait,
            timeout=timeout,
        )
    ctx.emit(True, data=result)
    _journal(ctx, entity_set, result)
