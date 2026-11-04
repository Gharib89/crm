"""`crm inventory`: extract a migration inventory (ADR 0032)."""

# pyright: basic
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import click

from crm import __version__
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import d365_errors
from crm.core import legacy_inventory
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
@pass_ctx
def inventory_extract(ctx: CLIContext, out_dir: Path):
    """Extract the org's migration inventory into DIR: read-only.

    Runs on a crm2011 profile for now: it reads every v1 kind over the CRM 2011
    SOAP endpoint and never publishes or writes to the org. Each finished read
    is checkpointed under DIR/.checkpoint, so re-running the same command after
    an interruption resumes, reusing the finished reads. A failed kind is
    recorded in the manifest's coverage and the run goes on (exit code 1); the
    checkpoint is removed once every read succeeds. Returns a short summary;
    the manifest holds the records, coverage and gaps.
    """
    if ctx.profile_platform() != PLATFORM_CRM2011:
        ctx.emit(
            False,
            error="`crm inventory extract` supports only crm2011 profiles so far; "
            "the v9 on-prem and cloud path is not available yet.",
        )
        return
    with d365_errors(ctx):
        client = ctx.legacy_client()
        summary = legacy_inventory.extract(client, out_dir, cli_version=__version__)
    data = asdict(summary)
    if summary.errors:
        ctx.emit(
            False,
            data=data,
            error=f"{len(summary.errors)} read(s) failed: {'; '.join(summary.errors)}",
        )
        return
    ctx.emit(True, data=data)
