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
    binary update. A missing source tree → no refresh. A genuine failure (e.g. an
    unreadable registry) is surfaced as an error entry in `data.skills` rather than
    silently dropped, so the reported outcome never falsely reads as 'nothing to do'."""
    if src_dir is None or not (src_dir / "SKILL.md").exists():
        return []
    try:
        return skill_registry.refresh_skills(target_version, src_dir)
    except Exception as exc:
        return [{"dest": None, "from_version": None, "to_version": target_version,
                 "status": "error", "error": str(exc)}]


def _emit_skills(ctx: CLIContext, skills: list[dict[str, Any]]) -> None:
    """Print skill refresh results as individual status lines (human mode only)."""
    for s in skills:
        dest = s.get("dest") or "?"
        name = Path(dest).name if dest != "?" else "?"
        status = s.get("status", "?")
        frm = s.get("from_version") or "?"
        to = s.get("to_version") or "?"
        if status == "error":
            ctx.skin.warning(f"skill {name}: {s.get('error', 'unknown error')}")
        else:
            ctx.skin.status(f"  skill {name}", f"{frm} → {to} ({status})")


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
        skills = _refresh_skills(
            update_mod.current_version(), skill_registry.bundled_skill_dir()
        )
        data: dict[str, Any] = {
            "updated": False,
            "current": update_mod.current_version(),
            "reason": "not a frozen install",
            "hint": "Run `pip install -U crm` to upgrade this installation.",
        }
        if ctx.json_mode:
            data["skills"] = skills
        ctx.emit(True, data=data)
        if not ctx.json_mode:
            _emit_skills(ctx, skills)
        return

    progress_cb = (lambda msg: click.echo(msg)) if not ctx.json_mode else None
    target = update_mod.install_dir()
    update_mod.cleanup_stale_updates(target)
    try:
        result = update_mod.perform_update(install_dir=target, progress=progress_cb)
    except update_mod.UpdateError as exc:
        ctx.emit(False, error=str(exc))
        return
    # After the bundle swap the new skill tree is on disk under the install dir;
    # the running process is still the old version, so refresh to `to_version`.
    # Fall back to current_version() when already up-to-date (no `to_version` key).
    new_version = str(result.get("to_version") or update_mod.current_version())
    skills = _refresh_skills(new_version, _frozen_skill_src(target))
    if ctx.json_mode:
        result["skills"] = skills
    ctx.emit(True, data=result)
    if not ctx.json_mode:
        _emit_skills(ctx, skills)
