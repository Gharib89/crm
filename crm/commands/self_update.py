"""`crm self-update` — passive update check + in-place upgrade for frozen builds.

PyInstaller installs can swap their own bundle from the R2 release layout
(download → SHA256 verify → atomic swap); pip/editable installs are directed to
`pip install -U crm` and never modify the filesystem. `--check` reports the
running vs. latest version in either case without changing anything.
"""
# pyright: basic
from __future__ import annotations

import click

from crm.cli import CLIContext, pass_ctx
from crm.core import update as update_mod


@click.command("self-update")
@click.option("--check", "check_only", is_flag=True,
              help="Report current vs latest version and exit without modifying anything.")
@pass_ctx
def self_update_cmd(ctx: CLIContext, check_only: bool) -> None:
    """Update the crm CLI in place (frozen builds) or report available updates."""
    if check_only:
        try:
            result = update_mod.check_for_update()
        except update_mod.UpdateError as exc:
            ctx.emit(False, error=str(exc))
            return
        ctx.emit(True, data=result)
        return

    if not update_mod.is_frozen():
        ctx.emit(True, data={
            "updated": False,
            "current": update_mod.current_version(),
            "reason": "not a frozen install",
            "hint": "Run `pip install -U crm` to upgrade this installation.",
        })
        return

    target = update_mod.install_dir()
    update_mod.cleanup_stale_updates(target)
    try:
        result = update_mod.perform_update(install_dir=target)
    except update_mod.UpdateError as exc:
        ctx.emit(False, error=str(exc))
        return
    ctx.emit(True, data=result)
