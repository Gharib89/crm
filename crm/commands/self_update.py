"""`crm self-update` — passive update check + in-place upgrade for frozen builds.

PyInstaller installs can swap their own bundle from the R2 release layout
(download → SHA256 verify → atomic swap); pip/editable installs are directed to
`pip install -U crm` and never modify the filesystem. `--check` reports the
running vs. latest version in either case without changing anything.
"""
# pyright: basic
from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands import skill_registry
from crm.core import update as update_mod


def _frozen_skill_src(install_dir: Path) -> Path | None:
    """The new bundled skill tree under a freshly-swapped frozen install.

    The running process still executes the OLD code (and on posix its old
    package dir has been deleted by the swap), so the refresh must read the new
    tree off disk under `install_dir` rather than via the in-memory package.
    """
    hit = next(install_dir.glob("**/crm/skills/SKILL.md"), None)
    return hit.parent if hit else None


def _refresh_skills(target_version: str, src_dir: Path | None) -> list[dict[str, Any]]:
    """Re-sync recorded skills, never raising — a skill failure must not fail the
    binary update. A missing source tree → no refresh."""
    if src_dir is None or not (src_dir / "SKILL.md").exists():
        return []
    try:
        return skill_registry.refresh_skills(target_version, src_dir)
    except Exception:
        return []


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
        # pip/uv: the binary is not touched, but the running wheel may already be
        # newer than the last `skill install` — re-sync from the running package.
        ctx.emit(True, data={
            "updated": False,
            "current": update_mod.current_version(),
            "reason": "not a frozen install",
            "hint": "Run `pip install -U crm` to upgrade this installation.",
            "skills": _refresh_skills(
                update_mod.current_version(), skill_registry.bundled_skill_dir()
            ),
        })
        return

    target = update_mod.install_dir()
    update_mod.cleanup_stale_updates(target)
    try:
        result = update_mod.perform_update(install_dir=target)
    except update_mod.UpdateError as exc:
        ctx.emit(False, error=str(exc))
        return
    # After the bundle swap the new skill tree is on disk under the install dir;
    # the running process is still the old version, so refresh to `latest`.
    new_version = str(result.get("latest", "")).lstrip("vV")
    result["skills"] = _refresh_skills(new_version, _frozen_skill_src(target))
    ctx.emit(True, data=result)
