"""Installed-skill registry: ${CRM_HOME}/installed-skills.json.

Records where `crm skill install` copied the bundled skill tree, so `crm
self-update` can refresh exactly those dests after an upgrade (see ADR-0006).
Lives in the command layer, not crm/core, because core must not own profile/
config-style state writes (ADR-0002 split).

Format is a top-level object so future keys land without a break::

    {"skills": [{"target": "claude", "dest": "/abs/path", "installed_version": "2.10.0"}]}

Read tolerantly (missing/corrupt → empty list) and written atomically (temp +
os.replace), since CRM_HOME is shared across concurrent invocations.
"""
# pyright: basic
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any


def _crm_home() -> Path:
    root = Path(os.environ.get("CRM_HOME", str(Path.home() / ".crm"))).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def registry_path() -> Path:
    return _crm_home() / "installed-skills.json"


def read_skills() -> list[dict[str, Any]]:
    """The recorded install entries, or [] if the file is missing/corrupt.

    Tolerant only of *missing* (FileNotFoundError) and *corrupt* (bad JSON /
    decode) files — a genuine I/O fault (e.g. PermissionError) propagates so the
    caller surfaces a clean error instead of silently treating it as empty and
    clobbering the registry on the next write.
    """
    try:
        raw = json.loads(registry_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return []
    skills = raw.get("skills") if isinstance(raw, dict) else None
    return skills if isinstance(skills, list) else []


def _write_skills(skills: list[dict[str, Any]]) -> None:
    path = registry_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"skills": skills}, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def record_install(target: str, dest: str, installed_version: str) -> None:
    """Add or update the entry for `dest` (dedup by resolved dest path)."""
    skills = [s for s in read_skills() if s.get("dest") != dest]
    skills.append({"target": target, "dest": dest, "installed_version": installed_version})
    _write_skills(skills)


def remove_install(dest: str) -> None:
    """Drop the entry matching `dest`, if any."""
    _write_skills([s for s in read_skills() if s.get("dest") != dest])


# ── Skill tree copy + self-update refresh ───────────────────────────────


def bundled_skill_dir() -> Path:
    """The skill bundle shipped inside the *running* package (SKILL.md + reference/)."""
    import crm as _crm_pkg

    return Path(_crm_pkg.__file__).resolve().parent / "skills"


def install_tree(src_dir: Path, dest_dir: Path) -> None:
    """Copy the skill tree from `src_dir` into `dest_dir`, replacing any prior copy."""
    if dest_dir.exists():
        ref_dir = dest_dir / "reference"
        if ref_dir.is_dir():
            shutil.rmtree(ref_dir)
        skill_md = dest_dir / "SKILL.md"
        if skill_md.exists():
            skill_md.unlink()
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        src_dir, dest_dir,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def refresh_skills(target_version: str, src_dir: Path) -> list[dict[str, Any]]:
    """Re-sync recorded skill dests to `target_version`, copying from `src_dir`.

    Per recorded dest: in-sync → skipped (no copy); vanished folder → pruned
    (entry dropped, folder not recreated); otherwise re-copy → refreshed. A copy
    failure records ``error`` and keeps the (stale) entry so a later run retries;
    it never aborts the walk. Returns one ``{dest, from_version, to_version,
    status}`` per dest (order preserved).
    """
    results: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    changed = False
    for entry in read_skills():
        dest = entry.get("dest")
        from_v = entry.get("installed_version")
        if not isinstance(dest, str):
            kept.append(entry)
            continue
        if from_v == target_version:
            results.append({"dest": dest, "from_version": from_v,
                            "to_version": from_v, "status": "skipped"})
            kept.append(entry)
            continue
        if not Path(dest).exists():
            results.append({"dest": dest, "from_version": from_v,
                            "to_version": None, "status": "pruned"})
            changed = True
            continue  # drop the entry; do not recreate the folder
        try:
            install_tree(src_dir, Path(dest))
        except Exception:
            results.append({"dest": dest, "from_version": from_v,
                            "to_version": from_v, "status": "error"})
            kept.append(entry)
            continue
        kept.append({**entry, "installed_version": target_version})
        results.append({"dest": dest, "from_version": from_v,
                        "to_version": target_version, "status": "refreshed"})
        changed = True
    if changed:
        _write_skills(kept)
    return results
