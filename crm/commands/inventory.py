"""`crm inventory`: extract a migration inventory (ADR 0032)."""

# pyright: basic
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import click

from crm import __version__
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import d365_errors
from crm.core import inventory_extract
from crm.core.webapi_inventory import WebApiSource
from crm.utils.d365_backend import PLATFORM_CRM2011


@click.group("inventory")
def inventory_group():
    """Extract a migration inventory: manifest v1 plus raw artifacts (ADR 0032)."""


@inventory_group.command("extract")
@click.option(
    "--out",
    "out_dir",
    required=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Folder to write manifest.json and artifacts/ into; a re-run resumes it.",
)
@click.option(
    "--solution",
    "solutions",
    multiple=True,
    metavar="UNIQUE_NAME",
    help="Narrow the inventory to this solution (repeatable); default: the whole org.",
)
@pass_ctx
def inventory_extract_cmd(ctx: CLIContext, out_dir: Path, solutions: tuple[str, ...]):
    """Extract the org's migration inventory into DIR: read-only.

    Runs on every profile: a crm2011 profile is read over the CRM 2011 SOAP
    endpoint, a v9 on-prem or Dataverse online profile over the Web API. It
    reads every v1 kind and never publishes or writes to the org, so a
    read-only profile runs it. --solution narrows the records to those
    solutions, their members and their entities' parts. Each finished read is
    checkpointed under DIR/.checkpoint, so re-running the same command after
    an interruption resumes, reusing the finished reads. A failed kind is
    recorded in the manifest's coverage and the run goes on (exit code 1); the
    checkpoint is removed once every read succeeds. Returns a short summary;
    the manifest holds the records, coverage and gaps.
    """
    saved = ctx.saved_profile()
    meta = None
    with d365_errors(ctx):
        try:
            inventory_extract.check_out(out_dir)
            if saved is not None and saved.platform == PLATFORM_CRM2011:
                client = ctx.legacy_client()
                source = inventory_extract.LegacySource(client)
                if ctx.json_mode:
                    meta = {"profile": client.profile.name, "url": client.endpoint}
            else:
                source = WebApiSource(ctx.backend())
            summary = inventory_extract.extract(
                source, out_dir, cli_version=__version__, solutions=solutions
            )
        except OSError as exc:
            ctx.emit(False, error=f"--out {out_dir}: {exc}")
            return
    data = asdict(summary)
    if summary.errors:
        ctx.emit(
            False,
            data=data,
            error=f"{len(summary.errors)} read(s) failed: {'; '.join(summary.errors)}",
        )
        return
    ctx.emit(True, data=data, meta=meta)
