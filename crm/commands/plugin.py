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
    warning = _ignored_update_flags_warning(update, version, culture,
                                             public_key_token, description)
    try:
        info = plugin_mod.register_assembly(
            ctx.backend(), path=path, name=name, version=version,
            culture=culture, public_key_token=public_key_token,
            isolation_mode=isolation_mode, description=description,
            solution=solution, update=update)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, warning,
                       meta={"staged": True} if ctx.stage_only else None)


@plugin_group.command("list-types")
@click.option("--assembly", default=None,
              help="Filter to plug-in types of this assembly (by name).")
@pass_ctx
def list_types_cmd(ctx: CLIContext, assembly):
    """List platform-generated plug-in types (optionally for one assembly)."""
    try:
        result = plugin_mod.list_types(ctx.backend(), assembly=assembly)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    items = result["value"]
    if ctx.json_mode:
        ctx.emit(True, data=items, meta={"count": len(items)})
        return
    headers = ["typename", "friendlyname", "plugintypeid"]
    rows = [[it.get(h, "") for h in headers] for it in items]
    ctx.emit(True, table={"headers": headers, "rows": rows},
             meta={"count": len(items)})


@plugin_group.command("register-step")
@click.option("--message", required=True,
              help="SDK message name (e.g. Create, Update, Delete).")
@click.option("--plugin-type", "plugin_type", required=True,
              help="Plug-in type name (the fully qualified typename).")
@click.option("--entity", default=None,
              help="Primary entity logical name (primaryobjecttypecode). "
                   "Omit for a message-level step (all entities).")
@click.option("--stage",
              type=click.Choice(["prevalidation", "preoperation",
                                  "postoperation"]),
              default="postoperation",
              help="Pipeline stage (prevalidation=10, preoperation=20, "
                   "postoperation=40). Default: postoperation.")
@click.option("--mode", type=click.Choice(["sync", "async"]), default="sync",
              help="Execution mode (sync=0, async=1). Default: sync.")
@click.option("--rank", type=int, default=1,
              help="Execution order within the stage. Default: 1.")
@click.option("--filtering-attributes", "filtering_attributes", default=None,
              help="Comma-separated attributes that trigger the step (Update).")
@click.option("--name", default=None,
              help="Step name; defaults to a derived label.")
@click.option("--assembly", default=None,
              help="Scope the plug-in type lookup to this assembly (by name).")
@pass_ctx
def register_step_cmd(ctx: CLIContext, message, plugin_type, entity, stage,
                      mode, rank, filtering_attributes, name, assembly):
    """Register a plug-in step (sdkmessageprocessingstep)."""
    try:
        info = plugin_mod.register_step(
            ctx.backend(), message=message, plugin_type=plugin_type,
            entity=entity, stage=stage, mode=mode, rank=rank,
            filtering_attributes=filtering_attributes, name=name,
            assembly=assembly)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    _emit_with_warning(ctx, info, None,
                       meta={"staged": True} if ctx.stage_only else None)


def _ignored_update_flags_warning(
    update, version, culture, public_key_token, description,
) -> str | None:
    """Build a warning naming identity flags --update silently ignores.

    --update re-uploads content only (and honors --solution); the identity
    flags below are dropped. Returns None when not updating or none were passed.
    """
    if not update:
        return None
    ignored: list[str] = []
    if version is not None:
        ignored.append("--version")
    if culture is not None:
        ignored.append("--culture")
    if public_key_token is not None:
        ignored.append("--public-key-token")
    if description is not None:
        ignored.append("--description")
    # --isolation-mode defaults to "sandbox", so None can't flag it; consult
    # Click's parameter source to see whether the user actually passed it.
    source = click.get_current_context().get_parameter_source("isolation_mode")
    if source == click.core.ParameterSource.COMMANDLINE:
        ignored.append("--isolation-mode")
    if not ignored:
        return None
    return f"--update re-uploads content only; ignored: {', '.join(ignored)}"
