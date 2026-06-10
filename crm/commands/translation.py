"""`crm translation` — solution display-label translation export/import."""
# pyright: basic
from __future__ import annotations

import click

from crm.cli import CLIContext, pass_ctx
from crm.core import translation as translation_mod
from crm.utils.d365_backend import D365Error

from crm.commands._helpers import (
    _confirm_destructive,
    _handle_d365_error,
    _journal,
    _no_retry_scope,
)


@click.group("translation")
def translation_group():
    """Export / import localizable display labels for a solution."""


@translation_group.command("export")
@click.option("--solution", required=True,
              help="Unique name of the solution whose labels to export.")
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False))
@click.option("--timeout", type=int, default=None,
              help="Read timeout in seconds for the export request. "
                   "Overrides profile.async_timeout.")
@click.option("--no-retry", is_flag=True,
              help="Disable the 429/5xx retry loop for this invocation.")
@pass_ctx
def translation_export_cmd(ctx: CLIContext, solution, output, timeout, no_retry):
    """Export all translations for SOLUTION to a zip (CrmTranslations.xml)."""
    with _no_retry_scope(ctx, no_retry):
        try:
            info = translation_mod.export_translation(
                ctx.backend(), solution, output, timeout=timeout,
            )
        except D365Error as exc:
            _handle_d365_error(ctx, exc)
            return
        ctx.emit(True, data=info)


@translation_group.command("import")
@click.argument("zip_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--timeout", type=int, default=None,
              help="Read timeout in seconds for the import request. "
                   "Overrides profile.async_timeout.")
@click.option("--no-retry", is_flag=True,
              help="Disable the 429/5xx retry loop for this invocation.")
@click.option("--yes", is_flag=True, help="Skip the overwrite confirmation prompt.")
@pass_ctx
def translation_import_cmd(ctx: CLIContext, zip_path, timeout, no_retry, yes):
    """Import a translations zip; labels surface only after publishing."""
    if not _confirm_destructive(
        "translations", zip_path, yes,
        message=(f"Importing {zip_path!r} will OVERWRITE localized labels "
                 f"in the target org. Continue?"),
    ):
        ctx.emit(False, error="aborted by user")
        return
    with _no_retry_scope(ctx, no_retry):
        try:
            info = translation_mod.import_translation(
                ctx.backend(), zip_path, timeout=timeout,
            )
        except D365Error as exc:
            _handle_d365_error(ctx, exc)
            return
        ctx.emit(True, data=info, warnings=[
            "Imported labels do not surface until published — run "
            "`crm solution publish-all`."
        ])
        _journal(ctx, "translation import", zip_path, info)
