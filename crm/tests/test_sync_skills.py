# pyright: basic
"""Unit tests for scripts/sync-skills.py: a skill the skills CLI installed
(recorded in skills-lock.json) is never vendored over, as a dependency or by
a direct SYNC entry (#980).
"""

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "sync-skills.py"

_spec = importlib.util.spec_from_file_location("sync_skills", SCRIPT)
assert _spec and _spec.loader
sync_skills = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_skills)  # pyright: ignore[reportAttributeAccessIssue]


def _skill(root: Path, name: str, body: str) -> None:
    (root / name).mkdir()
    (root / name / "SKILL.md").write_text(body, encoding="utf-8")


def test_resolve_closure_skips_lock_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _skill(tmp_path, "alpha", "uses `/tdd`")
    _skill(tmp_path, "tdd", "personal copy")
    monkeypatch.setattr(sync_skills, "SRC", tmp_path)
    monkeypatch.setattr(sync_skills, "LOCKED", {"tdd"})

    wanted, auto = sync_skills.resolve_closure({"alpha": False}, {"alpha", "tdd"})

    assert set(wanted) == {"alpha"}
    assert auto == set()


def test_locked_reads_skills_lock_keys():
    lock = json.loads((REPO_ROOT / "skills-lock.json").read_text(encoding="utf-8"))
    assert sync_skills.LOCKED == set(lock["skills"])


def test_sync_names_no_lock_recorded_skill():
    # A direct SYNC entry for a locked skill would trip main()'s clash guard.
    assert not {e["name"] for e in sync_skills.SYNC} & sync_skills.LOCKED
