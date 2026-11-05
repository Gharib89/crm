"""Skill install/uninstall commands."""

# pyright: basic
from __future__ import annotations

import shutil
from pathlib import Path

import click

from crm import __version__
from crm.cli import CLIContext, pass_ctx
from crm.commands import skill_registry
from crm.commands._tty import _stdin_is_tty
from crm.commands.skill_registry import bundled_skill_dir as _bundled_skill_dir

# Each target is a skills root; a skill installs into `<root>/<skill name>`.
SKILL_TARGETS: dict[str, Path] = {
    "copilot": Path.home() / ".copilot" / "skills",
    "claude": Path.home() / ".claude" / "skills",
    "cursor": Path.home() / ".cursor" / "rules",
}

_SKILL_OPTION = click.option(
    "--skill",
    "skills",
    type=click.Choice(sorted(skill_registry.SKILL_FOLDERS)),
    multiple=True,
    help="Which shipped skill (repeatable); default crm.",
)


def _resolve_skill_dest(target: str | None, dest: str | None, skill: str = "crm") -> Path:
    if dest:
        return Path(dest).expanduser().resolve()
    return SKILL_TARGETS[target or "claude"] / skill


def _chosen_skills(skills: tuple[str, ...], dest: str | None) -> list[str]:
    """The `--skill` values in order, deduplicated, default `crm`; `--dest` names
    one folder, so it takes one skill.
    """
    chosen = list(dict.fromkeys(skills)) or ["crm"]
    if dest and len(chosen) > 1:
        raise click.UsageError("--dest takes one --skill; install several skills by --target.")
    return chosen


@click.group("skill")
def skill_group():
    """Install the bundled agent skills (crm, crm-migration) for Copilot / Claude / Cursor."""


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
@_SKILL_OPTION
@click.option("--force", is_flag=True, help="Overwrite an existing skill at the destination.")
@pass_ctx
def skill_install(
    ctx: CLIContext, target: str, dest: str | None, skills: tuple[str, ...], force: bool
):
    """Copy each bundled skill tree (SKILL.md + reference/) into `<target>/<skill name>`."""
    chosen = _chosen_skills(skills, dest)
    plan = [
        (name, _bundled_skill_dir(name), _resolve_skill_dest(target, dest, name)) for name in chosen
    ]
    for _name, src_dir, _dest_dir in plan:
        src_skill = src_dir / "SKILL.md"
        if not src_skill.exists():
            ctx.emit(False, error=f"Bundled SKILL.md not found at {src_skill}.")

    # Settle every overwrite before copying anything, so a refusal installs nothing.
    for _name, _src_dir, dest_dir in plan:
        dest_file = dest_dir / "SKILL.md"
        if dest_file.exists() and not force:
            # TTY-gated confirm: prompt only on an interactive human terminal. Under
            # --json / non-TTY (agent/CI) keep the clean "already exists" error — never
            # prompt (the no-prompt-under-json invariant). Decided on the gate directly,
            # not via _confirm_destructive (its non-TTY path emits "aborted by user").
            if _stdin_is_tty() and not ctx.json_mode:
                if not click.confirm(
                    f"Skill already exists at {dest_dir}; overwrite?", default=False
                ):
                    ctx.emit(False, error="aborted by user")
                    return
            else:
                ctx.emit(
                    False,
                    error=f"{dest_file} already exists. Use --force to overwrite.",
                    meta={"target": target, "dest": str(dest_dir)},
                )

    target_label = "custom" if dest else target
    results = []
    for name, src_dir, dest_dir in plan:
        try:
            skill_registry.install_tree(src_dir, dest_dir)
            skill_registry.record_install(target_label, str(dest_dir), __version__, skill=name)
        except (OSError, shutil.Error) as exc:
            ctx.emit(
                False,
                error=f"Failed to install skill to {dest_dir}: {exc}"
                + _completed_note("already installed", results),
                meta={"target": target_label, "completed": results},
            )
            return
        ref_dir = dest_dir / "reference"
        refs = sorted(ref_dir.glob("*.md")) if ref_dir.is_dir() else []
        results.append(
            {
                "installed": True,
                "skill": name,
                "source": str(src_dir),
                "dest": str(dest_dir),
                "files": ["SKILL.md", *[f"reference/{p.name}" for p in refs]],
            }
        )
    data = results[0] if len(results) == 1 else {"installed": True, "skills": results}
    ctx.emit(True, data=data, meta={"target": target_label})


@skill_group.command("uninstall")
@click.option(
    "--target",
    type=click.Choice(sorted(SKILL_TARGETS.keys())),
    default="claude",
    show_default=True,
)
@click.option("--dest", type=click.Path(file_okay=False), default=None)
@_SKILL_OPTION
@pass_ctx
def skill_uninstall(ctx: CLIContext, target: str, dest: str | None, skills: tuple[str, ...]):
    """Remove each installed skill (SKILL.md + reference/, and the directory if empty)."""
    results = []
    for name in _chosen_skills(skills, dest):
        dest_dir = _resolve_skill_dest(target, dest, name)
        try:
            results.append({"skill": name, **_uninstall_one(dest_dir)})
        except (OSError, shutil.Error) as exc:
            ctx.emit(
                False,
                error=f"Failed to uninstall skill at {dest_dir}: {exc}"
                + _completed_note("already removed", results),
                meta={"completed": results},
            )
            return
    ctx.emit(True, data=results[0] if len(results) == 1 else {"skills": results})


def _completed_note(verb: str, results: list[dict[str, object]]) -> str:
    """Name the skills a multi-skill run finished before failing, since they stay done."""
    if not results:
        return ""
    return f" ({verb}: {', '.join(str(r['skill']) for r in results)})"


def _uninstall_one(dest_dir: Path) -> dict[str, object]:
    """Remove one installed skill folder and its registry entry; the result row."""
    dest_file = dest_dir / "SKILL.md"
    if not dest_file.exists():
        # Already gone — prune any stale registry entry and report it.
        skill_registry.remove_install(str(dest_dir))
        return {"removed": False, "reason": "not installed", "dest": str(dest_dir)}
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
    return {"removed": True, "dest": str(dest_dir)}
