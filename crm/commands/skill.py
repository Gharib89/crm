"""Skill install/uninstall commands."""
# pyright: basic
from __future__ import annotations
import shutil
from pathlib import Path
import click
from crm import __version__
from crm.cli import CLIContext, pass_ctx
from crm.commands._tty import _stdin_is_tty
from crm.commands import skill_registry
from crm.commands.skill_registry import bundled_skill_dir as _bundled_skill_dir


SKILL_TARGETS: dict[str, Path] = {
    "copilot": Path.home() / ".copilot" / "skills" / "crm",
    "claude": Path.home() / ".claude" / "skills" / "crm",
    "cursor": Path.home() / ".cursor" / "rules" / "crm",
}


def _resolve_skill_dest(target: str | None, dest: str | None) -> Path:
    if dest:
        return Path(dest).expanduser().resolve()
    return SKILL_TARGETS[target or "claude"]


@click.group("skill")
def skill_group():
    """Install the bundled agent skill (SKILL.md) for Copilot / Claude / Cursor."""


@skill_group.command("path")
@pass_ctx
def skill_path(ctx: CLIContext):
    """Show the path of the bundled skill directory inside the installed package."""
    src = _bundled_skill_dir()
    skill_md = src / "SKILL.md"
    ctx.emit(skill_md.exists(), data={"path": str(src), "exists": skill_md.exists()})


@skill_group.command("install")
@click.option(
    "--target",
    type=click.Choice(sorted(SKILL_TARGETS.keys())),
    default="claude",
    show_default=True,
    help="Where to install the skill. Ignored if --dest is given.",
)
@click.option(
    "--dest",
    type=click.Path(file_okay=False),
    default=None,
    help="Custom destination directory (overrides --target).",
)
@click.option("--force", is_flag=True, help="Overwrite an existing skill at the destination.")
@pass_ctx
def skill_install(ctx: CLIContext, target: str, dest: str | None, force: bool):
    """Copy the bundled skill tree (SKILL.md + reference/) into the agent's skill directory."""
    src_dir = _bundled_skill_dir()
    src_skill = src_dir / "SKILL.md"
    if not src_skill.exists():
        ctx.emit(False, error=f"Bundled SKILL.md not found at {src_skill}.")

    dest_dir = _resolve_skill_dest(target, dest)
    dest_file = dest_dir / "SKILL.md"

    overwrite = force
    if dest_file.exists() and not overwrite:
        # TTY-gated confirm: prompt only on an interactive human terminal. Under
        # --json / non-TTY (agent/CI) keep the clean "already exists" error — never
        # prompt (the no-prompt-under-json invariant). Decided on the gate directly,
        # not via _confirm_destructive (its non-TTY path emits "aborted by user").
        if _stdin_is_tty() and not ctx.json_mode:
            if not click.confirm(f"Skill already exists at {dest_dir}; overwrite?", default=False):
                ctx.emit(False, error="aborted by user")
                return
            overwrite = True
        else:
            ctx.emit(
                False,
                error=f"{dest_file} already exists. Use --force to overwrite.",
                meta={"target": target, "dest": str(dest_dir)},
            )

    try:
        skill_registry.install_tree(src_dir, dest_dir)
        target_label = "custom" if dest else target
        skill_registry.record_install(target_label, str(dest_dir), __version__)
    except (OSError, shutil.Error) as exc:
        ctx.emit(False, error=f"Failed to install skill to {dest_dir}: {exc}")
        return
    refs = sorted((dest_dir / "reference").glob("*.md")) if (dest_dir / "reference").is_dir() else []
    ctx.emit(
        True,
        data={
            "installed": True,
            "source": str(src_dir),
            "dest": str(dest_dir),
            "files": ["SKILL.md", *[f"reference/{p.name}" for p in refs]],
        },
        meta={"target": target if not dest else "custom"},
    )


@skill_group.command("uninstall")
@click.option(
    "--target",
    type=click.Choice(sorted(SKILL_TARGETS.keys())),
    default="claude",
    show_default=True,
)
@click.option("--dest", type=click.Path(file_okay=False), default=None)
@pass_ctx
def skill_uninstall(ctx: CLIContext, target: str, dest: str | None):
    """Remove the installed skill (SKILL.md + reference/, and the directory if empty)."""
    dest_dir = _resolve_skill_dest(target, dest)
    dest_file = dest_dir / "SKILL.md"
    try:
        if not dest_file.exists():
            # Already gone — prune any stale registry entry and report it.
            skill_registry.remove_install(str(dest_dir))
            ctx.emit(True, data={"removed": False, "reason": "not installed", "dest": str(dest_dir)})
            return
        ref_dir = dest_dir / "reference"
        if ref_dir.is_dir():
            shutil.rmtree(ref_dir)
        dest_file.unlink()
        try:
            dest_dir.rmdir()
        except OSError:
            pass
        # Drop the registry entry only after the files are gone, so a failed
        # delete leaves both the skill and its registry record intact.
        skill_registry.remove_install(str(dest_dir))
    except (OSError, shutil.Error) as exc:
        ctx.emit(False, error=f"Failed to uninstall skill at {dest_dir}: {exc}")
        return
    ctx.emit(True, data={"removed": True, "dest": str(dest_dir)})
