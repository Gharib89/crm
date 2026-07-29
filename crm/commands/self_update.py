"""`crm self-update` — passive update check + install-method-aware upgrade.

`self-update` is the single upgrade entry point for every install channel. It
detects how `crm` was installed and does the right thing:

- **frozen** (PyInstaller) — swap the bundle in place from the R2 release layout
  (download → SHA256 verify → atomic swap).
- **uv-tool / pipx** — build the correct force-reinstall command pinned to the
  latest release tag and, once consented (a TTY prompt, or `--yes`), run it; then
  re-sync recorded skills/completion via the freshly installed binary.
- **editable / pip-git / unknown** — print the correct git-based upgrade command
  and never auto-run (we don't own or safely know that environment).

`--check` reports the running vs. latest version on every install type without
changing anything. `crm` is not published to PyPI, so all non-frozen upgrade
paths are git-based (`git+https://github.com/Gharib89/crm@vX.Y.Z`).
"""

# pyright: basic
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from crm.cli import CLIContext, pass_ctx
from crm.commands import completion_registry, skill_registry
from crm.commands._tty import _stdin_is_tty
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
    silently dropped, so the reported outcome never falsely reads as 'nothing to do'.
    """
    if src_dir is None or not (src_dir / "SKILL.md").exists():
        return []
    try:
        return skill_registry.refresh_skills(target_version, src_dir)
    except Exception as exc:
        return [
            {
                "dest": None,
                "from_version": None,
                "to_version": target_version,
                "status": "error",
                "error": str(exc),
            }
        ]


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
    `_refresh_skills`) rather than aborting the command or being silently dropped.
    """
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
        return {
            "shell": marker.get("shell"),
            "script_path": marker.get("script_path"),
            "from_version": marker.get("installed_version"),
            "to_version": target_version,
            "status": "error",
            "error": str(exc),
        }


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


def _inprocess_refresh(
    ctx: CLIContext, data: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Re-sync recorded skills/completion from the RUNNING package.

    Correct whenever no bundle swap happened — the running code IS the current
    install (pip/uv without an upgrade, a declined upgrade, an up-to-date check).
    Attaches results to `data` under `--json`; returns them for human-mode emit.
    """
    version = update_mod.current_version()
    skills = _refresh_skills(version, skill_registry.bundled_skill_dir())
    completion = _refresh_completion(version, completion_registry.generate_source)
    if ctx.json_mode:
        data["skills"] = skills
        if completion is not None:
            data["completion"] = completion
    return skills, completion


def _post_upgrade_refresh() -> dict[str, Any] | None:
    """Re-invoke the freshly-installed `crm` to re-sync recorded skills/completion.

    After a `uv tool install --force` / `pipx install --force`, the running
    process is still the pre-reinstall package (stale, and on some layouts already
    removed), so an in-process refresh would copy the OLD skill tree. We shell out
    to the new binary on PATH with the guarded `--refresh-only` entry (which never
    re-enters the upgrade path, so there is no reinstall loop) and return its
    `data`. Never raises — a refresh failure must not fail a successful upgrade;
    a missing binary or unreadable output degrades to ``None``.
    """
    binary = shutil.which("crm")
    if binary is None:
        return None
    try:
        out = subprocess.run(
            [binary, "--json", "self-update", "--refresh-only"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if out.returncode != 0:
            return None
        payload = json.loads(out.stdout)
    except Exception:
        return None
    # Only a genuine ok:true envelope carries a trustworthy refresh payload; a
    # non-zero exit or a non-ok body (or empty stdout) degrades to None.
    if not (isinstance(payload, dict) and payload.get("ok") is True):
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def _finisher_refresh(to_version: str, install: Path) -> list[str]:
    """Re-sync recorded skills/completion from the just-installed bundle (#937).

    Runs in the detached finisher, which is the NEW build — so completion renders
    in-process rather than by shelling out to a binary, and the skill tree is read
    off the install it has just put in place. Returns the warnings the next `crm`
    run should print; an empty list means everything re-synced cleanly.
    """
    warnings: list[str] = []
    for entry in _refresh_skills(to_version, _frozen_skill_src(install)):
        if entry.get("status") == "error":
            warnings.append(
                f"Could not refresh the installed crm agent skill: {entry.get('error')}"
            )
    completion = _refresh_completion(to_version, completion_registry.generate_source)
    if completion is not None and completion.get("status") == "error":
        warnings.append(f"Could not refresh shell completion: {completion.get('error')}")
    return warnings


def _frozen_update(ctx: CLIContext) -> None:
    """Frozen (PyInstaller) install: download → verify → swap the bundle in place."""
    progress_cb = (lambda msg: click.echo(msg)) if not ctx.json_mode else None
    target = update_mod.install_dir()
    update_mod.cleanup_stale_updates(target)
    try:
        result = update_mod.perform_update(install_dir=target, progress=progress_cb)
    except update_mod.UpdateError as exc:
        ctx.emit(False, error=str(exc))
        return
    if result.get("pending"):
        # Windows: the swap happens in a detached finisher once this process exits,
        # because a running bundle cannot replace itself (#937). Nothing is installed
        # yet, so there is no new skill tree to refresh from and no outcome to
        # confirm — the finisher does both, and the next run reports them.
        ctx.emit(True, data=result)
        if not ctx.json_mode:
            ctx.skin.info(
                f"Staged crm {result['to_version']}. It is applied as crm exits; "
                "the next run reports the outcome."
            )
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
        # Confirm the outcome in a sentence, as the non-frozen paths do — the raw
        # payload fields alone leave the landing of an upgrade unannounced. The
        # arrow is ASCII as a belt-and-braces: `force_utf8_output` already
        # reconfigures stdout, but it deliberately no-ops on a stream with no
        # working `reconfigure`, and this line prints once the bundle has already
        # been replaced — the worst moment to risk an encode error.
        if result.get("updated"):
            ctx.skin.success(f"Updated crm {result['from_version']} -> {result['to_version']}.")
        else:
            ctx.skin.success(f"crm is up to date ({result['current']}).")
        _emit_skills(ctx, skills)
        _emit_completion(ctx, completion)


def _finish_without_upgrade(ctx: CLIContext, data: dict[str, Any]) -> None:
    """Emit a non-executing outcome (up-to-date / manual method / declined) and
    re-sync skills from the running package (which is the current install).
    """
    skills, completion = _inprocess_refresh(ctx, data)
    ctx.emit(True, data=data)
    if ctx.json_mode:
        return
    if data.get("reason") == "up-to-date":
        ctx.skin.success(f"crm is up to date ({data['current']}).")
    else:
        ctx.skin.info(f"To upgrade, run: {data['command']}")
    _emit_skills(ctx, skills)
    _emit_completion(ctx, completion)


def _method_aware_update(ctx: CLIContext, method: str, yes: bool) -> None:
    """Non-frozen upgrade: build the git-based command for `method`, auto-run it
    for uv/pipx (consent-gated), print guidance for the rest.
    """
    try:
        info = update_mod.check_for_update()
    except update_mod.UpdateError as exc:
        ctx.emit(False, error=str(exc))
        return
    latest = info["latest"]
    command = update_mod.upgrade_command(method, latest)
    argv = update_mod.upgrade_argv(method, latest)
    data: dict[str, Any] = {
        "install_method": method,
        "current": info["current"],
        "latest": latest,
        "update_available": info["update_available"],
        "command": command,
        "executed": False,
    }

    # Nothing to run: already current, or a method we never auto-run (editable /
    # pip-git / unknown — print guidance only).
    if not info["update_available"]:
        data["reason"] = "up-to-date"
        _finish_without_upgrade(ctx, data)
        return
    if argv is None:
        data["reason"] = "manual-install-method"
        _finish_without_upgrade(ctx, data)
        return

    # uv-tool / pipx: mutate the user's environment only with explicit consent.
    interactive = (not ctx.json_mode) and _stdin_is_tty()
    if yes:
        consented = True
    elif interactive:
        consented = click.confirm(f"Run: {command}\nProceed?", default=False)
        if not consented:
            data["reason"] = "declined"
    else:
        consented = False
        data["reason"] = "no-tty-without-yes"
    if not consented:
        _finish_without_upgrade(ctx, data)
        return

    try:
        status = update_mod.run_upgrade(argv)
    except FileNotFoundError:
        # The uv/pipx binary is not on PATH — fall back to printing the command.
        data["reason"] = "tool-not-on-path"
        _finish_without_upgrade(ctx, data)
        return

    data["executed"] = True
    data["exit_status"] = status
    if status != 0:
        # Keep the full execution payload on the error envelope so a scripter still
        # sees install_method/command/executed/exit_status; ok:false + exit 1 signal
        # the failed upgrade to CI.
        ctx.emit(False, data=data, error=f"Upgrade command exited with status {status}: {command}")
        return
    refresh = _post_upgrade_refresh()
    warnings: list[str] = []
    if refresh is not None:
        if "skills" in refresh:
            data["skills"] = refresh["skills"]
        if "completion" in refresh:
            data["completion"] = refresh["completion"]
    else:
        # The reinstall succeeded but the new binary couldn't refresh recorded
        # skills/completion — surface it so a stale skill tree isn't silent.
        warnings.append(
            "Upgraded, but could not refresh installed agent skills/completion via the "
            "new binary. Run `crm self-update --refresh-only` to retry."
        )
    ctx.emit(True, data=data, warnings=warnings or None)
    if not ctx.json_mode:
        ctx.skin.success(f"Upgraded crm to {latest} via {method}.")


@click.command("self-update")
@click.option(
    "--check",
    "check_only",
    is_flag=True,
    help="Report current vs latest version and exit without modifying anything.",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Run the upgrade non-interactively (uv/pipx installs; skips the confirm prompt).",
)
@click.option(
    "--refresh-only",
    "refresh_only",
    is_flag=True,
    hidden=True,
    help="Internal: re-sync recorded skills/completion from the running package (no upgrade).",
)
@click.option(
    "--finish-update",
    "finish_update",
    type=click.Path(dir_okay=False, path_type=Path),
    hidden=True,
    help="Internal: apply a staged update described by a handoff file, then exit.",
)
@pass_ctx
def self_update_cmd(
    ctx: CLIContext,
    check_only: bool,
    yes: bool,
    refresh_only: bool,
    finish_update: Path | None,
) -> None:
    """Update the crm CLI (method-aware) or report available updates."""
    if finish_update is not None:
        # The detached finisher: this process is the staged build, launched by the
        # `crm` being replaced so the swap happens after that one exits (#937).
        # Checked first, and never touching the upgrade path, so it cannot recurse.
        record = update_mod.finish_deferred_swap(finish_update, refresh=_finisher_refresh)
        if record.get("ok"):
            ctx.emit(True, data=record)
        else:
            ctx.emit(False, data=record, error=str(record.get("error") or "unknown error"))
        return

    if check_only:
        try:
            result = update_mod.check_for_update()
        except update_mod.UpdateError as exc:
            ctx.emit(False, error=str(exc))
            return
        ctx.emit(True, data=result)
        return

    method = update_mod.detect_install_method()

    if refresh_only:
        # Guarded internal entry: the post-upgrade re-invocation runs the freshly
        # installed binary here to re-sync skills/completion, never re-entering the
        # upgrade path (no reinstall loop). Checked before any method dispatch so it
        # can never trigger an upgrade, whatever `method` resolves to.
        data: dict[str, Any] = {
            "refreshed": True,
            "install_method": method,
            "current": update_mod.current_version(),
        }
        skills, completion = _inprocess_refresh(ctx, data)
        ctx.emit(True, data=data)
        if not ctx.json_mode:
            _emit_skills(ctx, skills)
            _emit_completion(ctx, completion)
        return

    if method == "frozen":
        _frozen_update(ctx)
        return

    _method_aware_update(ctx, method, yes)
