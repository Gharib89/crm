"""Skill install/uninstall commands."""
# pyright: basic
from __future__ import annotations
import shutil
from pathlib import Path
import click
from crm.cli import CLIContext, pass_ctx


SKILL_TARGETS: dict[str, Path] = {
    "copilot": Path.home() / ".copilot" / "skills" / "crm",
    "claude": Path.home() / ".claude" / "skills" / "crm",
    "cursor": Path.home() / ".cursor" / "rules" / "crm",
}


def _bundled_skill_path() -> Path:
    """Return path to the SKILL.md shipped inside the installed crm package."""
    import crm as _crm_pkg
    return Path(_crm_pkg.__file__).resolve().parent / "skills" / "SKILL.md"


def _resolve_skill_dest(target: str | None, dest: str | None) -> Path:
    if dest:
        return Path(dest).expanduser().resolve()
    return SKILL_TARGETS[target or "copilot"]


@click.group("skill")
def skill_group():
    """Install the bundled agent skill (SKILL.md) for Copilot / Claude / Cursor."""


@skill_group.command("path")
@pass_ctx
def skill_path(ctx: CLIContext):
    """Show the path of the bundled SKILL.md inside the installed package."""
    src = _bundled_skill_path()
    ctx.emit(src.exists(), data={"path": str(src), "exists": src.exists()})


@skill_group.command("install")
@click.option(
    "--target",
    type=click.Choice(sorted(SKILL_TARGETS.keys())),
    default="copilot",
    show_default=True,
    help="Where to install the skill. Ignored if --dest is given.",
)
@click.option(
    "--dest",
    type=click.Path(file_okay=False),
    default=None,
    help="Custom destination directory (overrides --target).",
)
@click.option("--force", is_flag=True, help="Overwrite an existing SKILL.md at the destination.")
@pass_ctx
def skill_install(ctx: CLIContext, target: str, dest: str | None, force: bool):
    """Copy the bundled SKILL.md into the agent's skill directory."""
    src = _bundled_skill_path()
    if not src.exists():
        ctx.emit(False, error=f"Bundled SKILL.md not found at {src}.")

    dest_dir = _resolve_skill_dest(target, dest)
    dest_file = dest_dir / "SKILL.md"

    if dest_file.exists() and not force:
        ctx.emit(
            False,
            error=f"{dest_file} already exists. Use --force to overwrite.",
            meta={"target": target, "dest": str(dest_dir)},
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest_file)
    ctx.emit(
        True,
        data={"installed": True, "source": str(src), "dest": str(dest_file)},
        meta={"target": target if not dest else "custom"},
    )


@skill_group.command("uninstall")
@click.option(
    "--target",
    type=click.Choice(sorted(SKILL_TARGETS.keys())),
    default="copilot",
    show_default=True,
)
@click.option("--dest", type=click.Path(file_okay=False), default=None)
@pass_ctx
def skill_uninstall(ctx: CLIContext, target: str, dest: str | None):
    """Remove the installed SKILL.md (and its directory if empty)."""
    dest_dir = _resolve_skill_dest(target, dest)
    dest_file = dest_dir / "SKILL.md"
    if not dest_file.exists():
        ctx.emit(True, data={"removed": False, "reason": "not installed", "dest": str(dest_file)})
        return
    dest_file.unlink()
    try:
        dest_dir.rmdir()
    except OSError:
        pass
    ctx.emit(True, data={"removed": True, "dest": str(dest_file)})
