# crm/tests/test_skill_registry.py
# pyright: basic
"""Unit tests for the installed-skill registry (${CRM_HOME}/installed-skills.json)."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))


def test_record_then_read():
    from crm.commands import skill_registry as reg

    reg.record_install("claude", "/abs/path", "2.10.0")
    skills = reg.read_skills()
    assert skills == [{"target": "claude", "dest": "/abs/path", "installed_version": "2.10.0"}]


def test_reinstall_same_dest_updates_in_place():
    from crm.commands import skill_registry as reg

    reg.record_install("claude", "/abs/path", "2.10.0")
    reg.record_install("claude", "/abs/path", "2.11.0")
    skills = reg.read_skills()
    assert len(skills) == 1
    assert skills[0]["installed_version"] == "2.11.0"


def test_record_dedups_by_resolved_path():
    from crm.commands import skill_registry as reg

    reg.record_install("claude", "/a/b", "2.10.0")
    reg.record_install("claude", "/a/./b", "2.11.0")  # same dir, different spelling
    skills = reg.read_skills()
    assert len(skills) == 1
    assert skills[0]["installed_version"] == "2.11.0"
    assert skills[0]["dest"] == "/a/b"  # stored normalized


def test_remove_matches_unresolved_spelling():
    from crm.commands import skill_registry as reg

    reg.record_install("claude", "/a/b", "2.10.0")
    reg.remove_install("/a/./b")  # different spelling still removes
    assert reg.read_skills() == []


def test_distinct_dests_accumulate():
    from crm.commands import skill_registry as reg

    reg.record_install("claude", "/a", "2.10.0")
    reg.record_install("copilot", "/b", "2.10.0")
    assert {s["dest"] for s in reg.read_skills()} == {"/a", "/b"}


def test_remove_install_drops_matching_dest():
    from crm.commands import skill_registry as reg

    reg.record_install("claude", "/a", "2.10.0")
    reg.record_install("copilot", "/b", "2.10.0")
    reg.remove_install("/a")
    assert [s["dest"] for s in reg.read_skills()] == ["/b"]


def test_missing_file_reads_as_empty():
    from crm.commands import skill_registry as reg

    assert reg.read_skills() == []


def test_corrupt_file_reads_as_empty(tmp_path):
    from crm.commands import skill_registry as reg

    reg.registry_path().write_text("{ not json", encoding="utf-8")
    assert reg.read_skills() == []


def test_read_propagates_unexpected_io_error(monkeypatch):
    # A real I/O fault (e.g. PermissionError) is NOT corruption — it must surface,
    # not be silently swallowed as an empty registry (would clobber state on write).
    from crm.commands import skill_registry as reg
    from pathlib import Path

    def boom(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", boom)
    with pytest.raises(PermissionError):
        reg.read_skills()


def _make_src(tmp_path):
    src = tmp_path / "src"
    (src / "reference").mkdir(parents=True)
    (src / "SKILL.md").write_text("NEW", encoding="utf-8")
    (src / "reference" / "x.md").write_text("ref", encoding="utf-8")
    return src


def test_refresh_stale_dest_is_refreshed(tmp_path):
    from crm.commands import skill_registry as reg

    src = _make_src(tmp_path)
    dest = tmp_path / "stale"
    dest.mkdir()
    reg.record_install("claude", str(dest), "1.0.0")

    results = reg.refresh_skills("2.0.0", src)

    assert results == [{"dest": str(dest), "from_version": "1.0.0",
                        "to_version": "2.0.0", "status": "refreshed"}]
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "NEW"
    assert reg.read_skills()[0]["installed_version"] == "2.0.0"


def test_refresh_insync_dest_is_skipped(tmp_path):
    from crm.commands import skill_registry as reg

    src = _make_src(tmp_path)
    dest = tmp_path / "sync"
    dest.mkdir()
    reg.record_install("claude", str(dest), "2.0.0")

    results = reg.refresh_skills("2.0.0", src)

    assert results[0]["status"] == "skipped"
    assert not (dest / "SKILL.md").exists()  # no copy happened


def test_refresh_vanished_dest_is_pruned(tmp_path):
    from crm.commands import skill_registry as reg

    src = _make_src(tmp_path)
    gone = tmp_path / "gone"  # never created
    reg.record_install("claude", str(gone), "1.0.0")

    results = reg.refresh_skills("2.0.0", src)

    assert results[0]["status"] == "pruned"
    assert reg.read_skills() == []  # entry dropped, folder not recreated
    assert not gone.exists()


def test_refresh_error_continues_and_keeps_entry(tmp_path):
    from crm.commands import skill_registry as reg

    src = _make_src(tmp_path)
    bad = tmp_path / "bad"
    bad.write_text("i am a file, not a dir", encoding="utf-8")  # mkdir will fail
    good = tmp_path / "good"
    good.mkdir()
    reg.record_install("claude", str(bad), "1.0.0")
    reg.record_install("claude", str(good), "1.0.0")

    results = reg.refresh_skills("2.0.0", src)

    by_dest = {r["dest"]: r for r in results}
    assert by_dest[str(bad)]["status"] == "error"
    # error reports the intended target so callers see what it tried to reach.
    assert by_dest[str(bad)]["to_version"] == "2.0.0"
    assert by_dest[str(good)]["status"] == "refreshed"
    # The failed entry is kept (still stale) so a later run retries it.
    versions = {s["dest"]: s["installed_version"] for s in reg.read_skills()}
    assert versions[str(bad)] == "1.0.0"
    assert versions[str(good)] == "2.0.0"
