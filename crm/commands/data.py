"""Data bulk import/export commands."""
# pyright: basic
from __future__ import annotations
import click
from crm.core import export as export_mod
from crm.core import data_import as import_mod
from crm.core import entity as entity_mod
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import d365_errors, _journal


@click.group("data")
def data_group():
    """Bulk CSV/JSON dataset import/export."""


@data_group.command("export")
@click.argument("entity_set")
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False))
@click.option("--select", multiple=True)
@click.option("--filter", "filter_", help="OData $filter.")
@click.option("--page-size", type=int, default=500)
@click.option("--max-records", type=int, default=None)
@click.option("--format", "fmt", type=click.Choice(["csv", "json"]))
@pass_ctx
def data_export(ctx: CLIContext, entity_set, output, select, filter_, page_size, max_records, fmt):
    select_list: list[str] = []
    for s in select:
        select_list.extend(part.strip() for part in s.split(",") if part.strip())
    with d365_errors(ctx):
        info = export_mod.export_records(
            ctx.backend(), entity_set, output,
            select=select_list or None,
            filter_=filter_,
            page_size=page_size,
            max_records=max_records,
            fmt=fmt,
        )
    ctx.emit(True, data=info)


@data_group.command("import")
@click.argument("entity_set")
@click.argument("input_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--format", "fmt", type=click.Choice(["jsonl", "csv"]), default=None,
              help="Input format; inferred from suffix when omitted (.csv→csv, else jsonl).")
@click.option("--mode", type=click.Choice(["create", "upsert"]), default="create",
              help="create=POST new records; upsert=PATCH by GUID (--id-column) "
                   "or by alternate key (--key).")
@click.option("--id-column", default=None,
              help="Column/key holding the record GUID (for --mode upsert; "
                   "mutually exclusive with --key).")
@click.option("--key", "alt_key", default=None, metavar="ATTR[,ATTR...]",
              help="Upsert by an alternate key instead of the primary GUID: one "
                   "attribute, or a comma-separated composite key. Values are read "
                   "from each row. Mutually exclusive with --id-column.")
@click.option("--chunk-size", type=int, default=100,
              help="Records per $batch call (each chunk is one transactional changeset by default).")
@click.option("--no-transaction", is_flag=True, default=False,
              help="Send each op as a top-level operation; no changeset wrapping.")
@click.option("--continue-on-error", is_flag=True, default=False,
              help="Send Prefer: odata.continue-on-error (requires --no-transaction).")
@pass_ctx
def data_import(ctx: CLIContext, entity_set, input_file, fmt, mode, id_column, alt_key,
                chunk_size, no_transaction, continue_on_error):
    """Bulk-import records from a JSONL/CSV file via $batch."""
    if continue_on_error and not no_transaction:
        raise click.UsageError(
            "--continue-on-error requires --no-transaction; "
            "Prefer: odata.continue-on-error is meaningless inside a changeset."
        )
    if mode == "upsert":
        if id_column and alt_key:
            raise click.UsageError(
                "--id-column and --key are mutually exclusive: upsert by the "
                "primary GUID OR by an alternate key, not both."
            )
        if not id_column and not alt_key:
            raise click.UsageError(
                "--mode upsert requires --id-column (the GUID column) or "
                "--key (an alternate key)."
            )
    elif alt_key:
        raise click.UsageError("--key applies only to --mode upsert.")
    requested_key = [a.strip() for a in alt_key.split(",") if a.strip()] if alt_key else []
    if alt_key and not requested_key:
        raise click.UsageError("--key must name at least one attribute.")
    with d365_errors(ctx):
        alt_key_attrs = (
            entity_mod.resolve_alternate_key(ctx.backend(), entity_set, requested_key)
            if requested_key else None
        )
        info = import_mod.import_records(
            ctx.backend(), entity_set, input_file,
            fmt=fmt, mode=mode, id_column=id_column, alt_key=alt_key_attrs,
            chunk_size=chunk_size,
            transactional=not no_transaction,
            continue_on_error=continue_on_error,
            # When-to-pay gate: only json mode surfaces the alt-key hint, so only
            # it pays for the enrichment metadata lookup (human render drops it).
            enrich_alt_key=ctx.json_mode,
        )
    failures = info.get("failures") or []
    warnings: list[str] | None = None
    if failures:
        warnings = [
            f"{info['failed']} record(s) failed to import "
            f"({info['imported']} succeeded across {info['chunks']} chunk(s))."
        ]
        if not ctx.json_mode:
            # Human mode: spell out each failed row (JSON mode carries the same
            # detail in data.failures, so don't duplicate it into warnings there).
            for f in failures:
                # Always lead with the input row (the reliable locator back to the
                # source file); append the record id for upserts where it's known.
                where = f"row {f['index']}"
                if "id" in f:
                    where += f" (id {f['id']})"
                warnings.append(f"  {where}: HTTP {f['status']} — {f['error']}")
    data = info
    if not ctx.json_mode:
        # The failures list renders as an unreadable truncated repr in the human
        # status dump; we surface it via the warnings above instead.
        data = {k: v for k, v in info.items() if k != "failures"}
    ctx.emit(True, data=data, warnings=warnings)
    _journal(ctx, entity_set, info)
