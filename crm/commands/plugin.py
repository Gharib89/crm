"""Plug-in assembly (pluginassemblies) commands.

The command passes the file `path` straight to the core, which reads the bytes
and derives identity — so the base64/identity/dry-run logic lives in one place.
"""
# pyright: basic
from __future__ import annotations
import click
from crm.core import plugin as plugin_mod
from crm.utils.d365_backend import D365Error
from crm.cli import CLIContext, pass_ctx
from crm.commands._helpers import _handle_d365_error, _emit_with_warning


@click.group("plugin")
def plugin_group():
    """Register and manage plug-in assemblies."""


@plugin_group.command("register-assembly")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--name", default=None,
              help="Assembly name; defaults to the file name stem.")
@click.option("--version", default=None,
              help="Assembly version; defaults to 1.0.0.0.")
@click.option("--culture", default=None,
              help="Culture code; defaults to 'neutral'.")
@click.option("--public-key-token", "public_key_token", default=None,
              help="Public key token; defaults to 'null' (unsigned).")
@click.option("--isolation-mode", "isolation_mode",
              type=click.Choice(["sandbox", "none"]), default="sandbox",
              help="Isolation mode (sandbox=2, none=1). Default: sandbox.")
@click.option("--description", default=None, help="Assembly description.")
@click.option("--solution", default=None,
              help="Target solution uniquename (MSCRM.SolutionUniqueName).")
@click.option("--update", is_flag=True, default=False,
              help="PATCH the content of an existing assembly (resolved by name).")
@pass_ctx
def register_assembly_cmd(ctx: CLIContext, path, name, version, culture,
                          public_key_token, isolation_mode, description,
                          solution, update):
    """Register a plug-in assembly from a .dll file (uploads its bytes)."""
    try:
        info = plugin_mod.register_assembly(
            ctx.backend(), path=path, name=name, version=version,
            culture=culture, public_key_token=public_key_token,
            isolation_mode=isolation_mode, description=description,
            solution=solution, update=update)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, None,
                       meta={"staged": True} if ctx.stage_only else None)
