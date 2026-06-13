"""`crm self-update` — passive update check + in-place upgrade for frozen builds.

PyInstaller installs can swap their own bundle from the R2 release layout
(download → SHA256 verify → atomic swap); pip/editable installs are directed to
`pip install -U crm` and never modify the filesystem. `--check` reports the
running vs. latest version in either case without changing anything.
"""
# pyright: basic
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands import completion_registry, skill_registry
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


def _refresh_completion(
    target_version: str, generate_fn: Callable[[str], str]
) -> dict[str, Any] | None:
    """Re-sync a CLI-installed completion script, never raising — a completion
    failure must not fail the binary update. No marker → ``None`` (nothing to do).
    A render/write failure is surfaced as an ``error`` status (mirrors
    `_refresh_skills`) rather than aborting the command or being silently dropped."""
    try:
        return completion_registry.refresh_completion(target_version, generate_fn)
    except Exception as exc:
        # The original failure may itself have been an unreadable marker
        # (read_marker lets genuine I/O faults propagate); re-reading it for the
        # error report must not re-raise and break the never-raise guarantee.
        try:
            marker = completion_registry.read_marker() or {}
        except Exception:
            marker = {}
        return {"shell": marker.get("shell"), "script_path": marker.get("script_path"),
                "from_version": marker.get("installed_version"), "to_version": target_version,
                "status": "error", "error": str(exc)}


def _emit_completion(ctx: CLIContext, comp: dict[str, Any] | None) -> None:
    """Print the completion refresh result as a status line (human mode only)."""
    if comp is None:
        return
    # script_path comes from a user-editable marker (completion.json); a malformed,
    # non-string value must not crash Path() and break self-update's never-raise.
    sp = comp.get("script_path")
    name = Path(sp).name if isinstance(sp, str) else "?"
    if comp.get("status") == "error":
        ctx.skin.warning(f"completion {name}: {comp.get('error', 'unknown error')}")
    else:
        frm = comp.get("from_version") or "?"
        to = comp.get("to_version") or "?"
        ctx.skin.status(f"  completion {name}", f"{frm} → {to} ({comp.get('status', '?')})")


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
        # In-process render is correct here: pip never swaps the binary, so the
        # running code IS the current install.
        completion = _refresh_completion(
            update_mod.current_version(), completion_registry.generate_source
        )
        data: dict[str, Any] = {
            "updated": False,
            "current": update_mod.current_version(),
            "reason": "not a frozen install",
            "hint": "Run `pip install -U crm` to upgrade this installation.",
        }
        if ctx.json_mode:
            data["skills"] = skills
            if completion is not None:
                data["completion"] = completion
        ctx.emit(True, data=data)
        if not ctx.json_mode:
            _emit_skills(ctx, skills)
            _emit_completion(ctx, completion)
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
    # The completion *content* comes from invoking the binary on PATH, which after
    # the swap is the NEW build (sys.executable's file was replaced in place) — so
    # we shell out rather than render in-process (still the old code post-swap).
    completion = _refresh_completion(
        new_version, lambda shell: completion_registry.generate_via_binary(shell, sys.executable)
    )
    if ctx.json_mode:
        result["skills"] = skills
        if completion is not None:
            result["completion"] = completion
    ctx.emit(True, data=result)
    if not ctx.json_mode:
        _emit_skills(ctx, skills)
        _emit_completion(ctx, completion)
