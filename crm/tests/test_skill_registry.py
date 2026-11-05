# crm/tests/test_skill_registry.py
# pyright: basic
"""Unit tests for the installed-skill registry (${CRM_HOME}/installed-skills.json)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("isolated_home")


def _norm(p: str) -> str:
    # The registry stores the resolved absolute path; on Windows "/a" → "D:\\a",
    # so expected values must be normalized the same way to stay OS-portable.
    return str(Path(p).resolve())


def test_record_then_read():
    from crm.commands import skill_registry as reg

    reg.record_install("claude", "/abs/path", "2.10.0")
    skills = reg.read_skills()
    assert skills == [
        {
            "target": "claude",
            "dest": _norm("/abs/path"),
            "installed_version": "2.10.0",
            "skill": "crm",
        }
    ]


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
    assert skills[0]["dest"] == _norm("/a/b")  # stored normalized


def test_remove_matches_unresolved_spelling():
    from crm.commands import skill_registry as reg

    reg.record_install("claude", "/a/b", "2.10.0")
    reg.remove_install("/a/./b")  # different spelling still removes
    assert reg.read_skills() == []


def test_distinct_dests_accumulate():
    from crm.commands import skill_registry as reg

    reg.record_install("claude", "/a", "2.10.0")
    reg.record_install("copilot", "/b", "2.10.0")
    assert {s["dest"] for s in reg.read_skills()} == {_norm("/a"), _norm("/b")}


def test_remove_install_drops_matching_dest():
    from crm.commands import skill_registry as reg

    reg.record_install("claude", "/a", "2.10.0")
    reg.record_install("copilot", "/b", "2.10.0")
    reg.remove_install("/a")
    assert [s["dest"] for s in reg.read_skills()] == [_norm("/b")]


def test_missing_file_reads_as_empty():
    from crm.commands import skill_registry as reg

    assert reg.read_skills() == []


def test_corrupt_file_reads_as_empty(tmp_path):
    from crm.commands import skill_registry as reg

    reg.registry_path().parent.mkdir(parents=True, exist_ok=True)
    reg.registry_path().write_text("{ not json", encoding="utf-8")
    assert reg.read_skills() == []


def test_read_propagates_unexpected_io_error(monkeypatch):
    # A real I/O fault (e.g. PermissionError) is NOT corruption — it must surface,
    # not be silently swallowed as an empty registry (would clobber state on write).
    from pathlib import Path

    from crm.commands import skill_registry as reg

    def boom(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", boom)
    with pytest.raises(PermissionError):
        reg.read_skills()


def _make_src(tmp_path):
    """A package dir holding both skill bundles, like the installed `crm/` package."""
    src = tmp_path / "src"
    for folder, body in (("skills", "NEW"), ("migration-skill", "NEW-MIGRATION")):
        (src / folder / "reference").mkdir(parents=True)
        (src / folder / "SKILL.md").write_text(body, encoding="utf-8")
        (src / folder / "reference" / "x.md").write_text("ref", encoding="utf-8")
    return src


def test_refresh_stale_dest_is_refreshed(tmp_path):
    from crm.commands import skill_registry as reg

    src = _make_src(tmp_path)
    dest = tmp_path / "stale"
    dest.mkdir()
    reg.record_install("claude", str(dest), "1.0.0")

    results = reg.refresh_skills("2.0.0", src)

    assert results == [
        {"dest": str(dest), "from_version": "1.0.0", "to_version": "2.0.0", "status": "refreshed"}
    ]
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


def test_refresh_reads_an_entry_without_a_skill_field_as_crm(tmp_path):
    # A registry written before #1062 carries no `skill` field.
    from crm.commands import skill_registry as reg

    src = _make_src(tmp_path)
    dest = tmp_path / "old"
    dest.mkdir()
    reg._write_skills([{"target": "claude", "dest": str(dest), "installed_version": "1.0.0"}])

    results = reg.refresh_skills("2.0.0", src)

    assert results[0]["status"] == "refreshed"
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "NEW"


def test_refresh_copies_a_migration_entry_from_the_migration_bundle(tmp_path):
    from crm.commands import skill_registry as reg

    src = _make_src(tmp_path)
    dest = tmp_path / "crm-migration"
    dest.mkdir()
    reg.record_install("claude", str(dest), "1.0.0", skill="crm-migration")

    results = reg.refresh_skills("2.0.0", src)

    assert results[0]["status"] == "refreshed"
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "NEW-MIGRATION"
    assert reg.read_skills()[0]["skill"] == "crm-migration"


def test_refresh_leaves_a_skill_alone_when_the_bundle_lacks_its_folder(tmp_path):
    """A bundle without `migration-skill/` (an older release) must not wipe the
    installed crm-migration files before failing (PR #1081 review).
    """
    import shutil

    from crm.commands import skill_registry as reg

    src = _make_src(tmp_path)
    shutil.rmtree(src / "migration-skill")
    dest = tmp_path / "crm-migration"
    (dest / "reference").mkdir(parents=True)
    (dest / "SKILL.md").write_text("OLD-MIGRATION", encoding="utf-8")
    (dest / "reference" / "x.md").write_text("old ref", encoding="utf-8")
    reg.record_install("claude", str(dest), "1.0.0", skill="crm-migration")

    results = reg.refresh_skills("2.0.0", src)

    assert results[0]["status"] == "error"
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "OLD-MIGRATION"
    assert (dest / "reference" / "x.md").read_text(encoding="utf-8") == "old ref"
    assert reg.read_skills()[0]["installed_version"] == "1.0.0"
