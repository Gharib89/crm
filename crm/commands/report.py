"""Report (reports entity) command group.

Registers custom reports headlessly: upload an SSRS RDL (``create --body-file``)
or a link report (``create --url``), file them under an area (``set-category``),
make them organization-wide (``create --org``), and list / get / delete. Reports
are solution-aware, so writes take ``--solution``.
"""
# pyright: basic
from __future__ import annotations

import os

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import (
    _emit_with_warning,
    _journal,
    _resolve_solution,
    _solution_option,
    d365_errors,
)
from crm.core import report as report_mod


@click.group("report")
def report_group() -> None:
    """Register and manage custom reports (SSRS RDL or link) headlessly.

    `create --body-file` uploads an RDL; `create --url` registers a link report.
    `create --org` makes a report organization-wide (otherwise it is personal).
    `set-category` files a report under sales/service/marketing/administrative.
    """


def _read_file(path: str) -> str:
    # click.Path(exists=True, readable=True) validates at parse time, but a
    # permission edge or a delete-after-check race can still fail the open —
    # surface it as a clean usage error (mirrors crm.commands.dashboard._read_file).
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise click.UsageError(f"cannot read {path}: {exc}") from exc


@report_group.command("list")
@pass_ctx
def report_list(ctx: CLIContext) -> None:
    """List all reports (summary columns only — use `report get` for the body)."""
    with d365_errors(ctx):
        reports = report_mod.list_reports(ctx.backend())
    headers = ["name", "reportid", "reporttypecode", "ispersonal"]
    rows = [
        [r["name"], r.get("reportid") or "", str(r.get("reporttypecode")),
         str(r["ispersonal"])]
        for r in reports
    ]
    ctx.emit(True, data=reports, table={"headers": headers, "rows": rows})


@report_group.command("get")
@click.argument("report_id")
@pass_ctx
def report_get(ctx: CLIContext, report_id: str) -> None:
    """Get a single report by REPORT_ID (its RDL body / link URL included)."""
    with d365_errors(ctx):
        info = report_mod.get_report(ctx.backend(), report_id)
    ctx.emit(True, data=info)


@report_group.command("delete")
@click.argument("report_id")
@pass_ctx
def report_delete(ctx: CLIContext, report_id: str) -> None:
    """Delete a report by REPORT_ID."""
    with d365_errors(ctx):
        info = report_mod.delete_report(ctx.backend(), report_id)
    ctx.emit(True, data=info)


@report_group.command("create")
@click.option("--name", required=True, help="Report display name.")
@click.option("--body-file", "body_file",
              type=click.Path(exists=True, dir_okay=False, readable=True),
              default=None,
              help="Path to an SSRS RDL file to upload (Reporting Services "
                   "report). Mutually exclusive with --url.")
@click.option("--url", default=None,
              help="External report URL (link report). Mutually exclusive with "
                   "--body-file.")
@click.option("--filename", default=None,
              help="Report file name. Defaults to the --body-file basename.")
@click.option("--description", default=None, help="Report description.")
@click.option("--org", is_flag=True, default=False,
              help="Make the report available to the organization "
                   "(ispersonal=false). Default: personal.")
@_solution_option
@pass_ctx
def report_create(
    ctx: CLIContext,
    name: str,
    body_file: str | None,
    url: str | None,
    filename: str | None,
    description: str | None,
    org: bool,
    solution: str | None,
    require_solution: bool,
) -> None:
    """Create a report from an RDL file (--body-file) or a link (--url)."""
    if bool(body_file) == bool(url):
        raise click.UsageError(
            "pass exactly one of --body-file (RDL upload) or --url (link report).")

    body = None
    if body_file:
        body = _read_file(body_file)
        if filename is None:
            filename = os.path.basename(body_file)

    solution, warning = _resolve_solution(ctx, solution, require_solution)
    with d365_errors(ctx):
        info = report_mod.create_report(
            ctx.backend(),
            name=name,
            body=body,
            filename=filename,
            url=url,
            description=description,
            org=org,
            solution=solution,
        )
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, name, info, solution=solution)


@report_group.command("set-category")
@click.argument("report_id")
@click.option("--category", required=True,
              type=click.Choice(sorted(report_mod.CATEGORY_CODES)),
              help="Report area: sales, service, marketing, or administrative.")
@_solution_option
@pass_ctx
def report_set_category(
    ctx: CLIContext,
    report_id: str,
    category: str,
    solution: str | None,
    require_solution: bool,
) -> None:
    """File REPORT_ID under a report area (creates a reportcategory record)."""
    solution, warning = _resolve_solution(ctx, solution, require_solution)
    with d365_errors(ctx):
        info = report_mod.set_category(
            ctx.backend(), report_id, category=category, solution=solution)
    _emit_with_warning(ctx, info, warning, meta=ctx.staged_meta())
    _journal(ctx, report_id, info, solution=solution)
