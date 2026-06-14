"""Batch and service-document commands."""
# pyright: basic
from __future__ import annotations
import json
from pathlib import Path
import click
from crm.core import batch as batch_mod
from crm.core import solution as sol_mod
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import d365_errors, _journal


@click.command("service-document")
@pass_ctx
def service_document_cmd(ctx: CLIContext):
    """GET the root service document — lists every entity set the server exposes."""
    with d365_errors(ctx):
        result = sol_mod.service_document(ctx.backend())
    if ctx.json_mode:
        ctx.emit(True, data=result, meta={"count": len((result or {}).get("value", []))})
        return
    sets = (result or {}).get("value", [])
    headers = ["name", "url", "kind"]
    rows = [[s.get("name", ""), s.get("url", ""), s.get("kind", "")] for s in sets[:200]]
    ctx.emit(True, table={"headers": headers, "rows": rows}, meta={"count": len(sets)})


@click.command("batch")
@click.argument("file_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--no-transaction", is_flag=True, default=False,
              help="Send each op as a top-level operation; no changeset wrapping.")
@click.option("--continue-on-error", is_flag=True, default=False,
              help="Send Prefer: odata.continue-on-error (requires --no-transaction).")
@click.option("--output", "output_path", type=click.Path(dir_okay=False), default=None,
              help="Write BatchResult[] JSON to this path.")
@click.option("--timeout", type=int, default=None,
              help="Override request timeout (seconds) for the batch call.")
@pass_ctx
def batch_cmd(ctx: CLIContext, file_path, no_transaction, continue_on_error, output_path, timeout):
    """Execute a $batch from a JSON file."""
    if continue_on_error and not no_transaction:
        raise click.UsageError(
            "--continue-on-error requires --no-transaction; "
            "Prefer: odata.continue-on-error is meaningless inside a changeset."
        )
    with d365_errors(ctx):
        ops = batch_mod.parse_batch_file(file_path)
        results = ctx.backend().batch(
            ops,  # type: ignore[arg-type]
            transactional=not no_transaction,
            continue_on_error=continue_on_error,
            timeout=timeout,
        )

    if output_path:
        try:
            Path(output_path).write_text(
                json.dumps(results, indent=2, default=str), encoding="utf-8"
            )
        except OSError as exc:
            ctx.emit(False, error=f"Could not write {output_path}: {exc}")
            return
        data = {"written": output_path, **batch_mod.render_batch_summary(results)}  # type: ignore[arg-type]
        ctx.emit(True, data=data)
        _journal(ctx, file_path, data)
    else:
        ctx.emit(True, data=results, meta=batch_mod.render_batch_summary(results))  # type: ignore[arg-type]
        _journal(ctx, file_path, results if isinstance(results, dict) else {"count": len(results)})
