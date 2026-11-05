# crm/tests/test_skill_install.py
# pyright: basic
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from crm.cli import cli


def _runner(tmp_path: Path, monkeypatch) -> CliRunner:
    # Don't load the repo's real ./.env during a no-connection skill command.
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    # Isolate the install registry from the real ~/.crm.
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    return CliRunner()


def test_default_target_is_claude():
    # No --target/--dest → resolves to the claude skills dir, not copilot.
    import crm.commands.skill as skill_mod

    assert skill_mod._resolve_skill_dest(None, None) == skill_mod.SKILL_TARGETS["claude"] / "crm"


def test_install_copies_tree(tmp_path: Path, monkeypatch):
    dest = tmp_path / "crm"
    result = _runner(tmp_path, monkeypatch).invoke(
        cli, ["--json", "skill", "install", "--dest", str(dest), "--force"]
    )
    assert result.exit_code == 0, result.output
    assert (dest / "SKILL.md").exists()
    assert (dest / "reference" / "records.md").exists()


def test_existing_skill_json_mode_errors_without_prompt(tmp_path: Path, monkeypatch):
    # --json + existing skill + no --force → unchanged "already exists" error, no prompt.
    dest = tmp_path / "crm"
    runner = _runner(tmp_path, monkeypatch)
    runner.invoke(cli, ["--json", "skill", "install", "--dest", str(dest), "--force"])
    result = runner.invoke(cli, ["--json", "skill", "install", "--dest", str(dest)])
    assert result.exit_code == 1, result.output
    import json

    assert "already exists" in json.loads(result.output)["error"]


def test_existing_skill_tty_confirm_accept_overwrites(tmp_path: Path, monkeypatch):
    dest = tmp_path / "crm"
    runner = _runner(tmp_path, monkeypatch)
    runner.invoke(cli, ["skill", "install", "--dest", str(dest), "--force"])
    import crm.commands.skill as skill_mod

    monkeypatch.setattr(skill_mod, "_stdin_is_tty", lambda: True)
    result = runner.invoke(cli, ["skill", "install", "--dest", str(dest)], input="y\n")
    assert result.exit_code == 0, result.output
    assert (dest / "SKILL.md").exists()


def test_existing_skill_tty_confirm_decline_aborts(tmp_path: Path, monkeypatch):
    dest = tmp_path / "crm"
    runner = _runner(tmp_path, monkeypatch)
    runner.invoke(cli, ["skill", "install", "--dest", str(dest), "--force"])
    import crm.commands.skill as skill_mod

    monkeypatch.setattr(skill_mod, "_stdin_is_tty", lambda: True)
    result = runner.invoke(cli, ["skill", "install", "--dest", str(dest)], input="n\n")
    assert result.exit_code == 1, result.output
    assert "aborted by user" in result.stderr


def test_install_records_in_registry(tmp_path: Path, monkeypatch):
    from crm.commands import skill_registry as reg

    dest = tmp_path / "crm"
    _runner(tmp_path, monkeypatch).invoke(
        cli, ["--json", "skill", "install", "--dest", str(dest), "--force"]
    )
    entries = reg.read_skills()
    assert len(entries) == 1
    assert entries[0]["dest"] == str(dest.resolve())
    assert entries[0]["target"] == "custom"


def test_uninstall_removes_registry_entry(tmp_path: Path, monkeypatch):
    from crm.commands import skill_registry as reg

    dest = tmp_path / "crm"
    runner = _runner(tmp_path, monkeypatch)
    runner.invoke(cli, ["--json", "skill", "install", "--dest", str(dest), "--force"])
    runner.invoke(cli, ["--json", "skill", "uninstall", "--dest", str(dest)])
    assert reg.read_skills() == []


def test_install_io_error_is_clean_envelope(tmp_path: Path, monkeypatch):
    import json

    import crm.commands.skill as skill_mod

    def boom(*a, **k):
        raise PermissionError("read-only")

    monkeypatch.setattr(skill_mod.skill_registry, "install_tree", boom)
    result = _runner(tmp_path, monkeypatch).invoke(
        cli, ["--json", "skill", "install", "--dest", str(tmp_path / "crm"), "--force"]
    )
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["ok"] is False


def test_uninstall_io_error_is_clean_envelope(tmp_path: Path, monkeypatch):
    import json

    import crm.commands.skill as skill_mod

    dest = tmp_path / "crm"
    runner = _runner(tmp_path, monkeypatch)
    runner.invoke(cli, ["--json", "skill", "install", "--dest", str(dest), "--force"])

    def boom(*a, **k):
        raise PermissionError("read-only")

    monkeypatch.setattr(skill_mod.skill_registry, "remove_install", boom)
    result = runner.invoke(cli, ["--json", "skill", "uninstall", "--dest", str(dest)])
    assert result.exit_code == 1, result.output
    assert json.loads(result.output)["ok"] is False


def test_uninstall_keeps_registry_when_file_delete_fails(tmp_path: Path, monkeypatch):
    # If the filesystem removal fails, the registry entry must NOT be pruned —
    # else self-update would stop refreshing a skill that is still on disk.
    import crm.commands.skill as skill_mod
    from crm.commands import skill_registry as reg

    dest = tmp_path / "crm"
    runner = _runner(tmp_path, monkeypatch)
    runner.invoke(cli, ["--json", "skill", "install", "--dest", str(dest), "--force"])

    def boom(*a, **k):
        raise PermissionError("locked")

    monkeypatch.setattr(skill_mod.shutil, "rmtree", boom)
    result = runner.invoke(cli, ["--json", "skill", "uninstall", "--dest", str(dest)])
    assert result.exit_code == 1, result.output
    assert len(reg.read_skills()) == 1  # entry preserved


def test_uninstall_removes_tree(tmp_path: Path, monkeypatch):
    dest = tmp_path / "crm"
    runner = _runner(tmp_path, monkeypatch)
    runner.invoke(cli, ["--json", "skill", "install", "--dest", str(dest), "--force"])
    result = runner.invoke(cli, ["--json", "skill", "uninstall", "--dest", str(dest)])
    assert result.exit_code == 0, result.output
    assert not (dest / "SKILL.md").exists()
    assert not (dest / "reference").exists()


def test_uninstall_dest_echoes_directory_on_success_and_noop(tmp_path: Path, monkeypatch):
    # `dest` must echo the destination directory the user passed, identically on
    # a successful removal and on a subsequent no-op — not drift to the SKILL.md
    # marker path when nothing is installed.
    import json

    dest = tmp_path / "crm"
    runner = _runner(tmp_path, monkeypatch)
    runner.invoke(cli, ["--json", "skill", "install", "--dest", str(dest), "--force"])

    removed_result = runner.invoke(cli, ["--json", "skill", "uninstall", "--dest", str(dest)])
    noop_result = runner.invoke(cli, ["--json", "skill", "uninstall", "--dest", str(dest)])
    assert removed_result.exit_code == 0, removed_result.output
    assert noop_result.exit_code == 0, noop_result.output
    removed = json.loads(removed_result.output)
    noop = json.loads(noop_result.output)

    assert removed["data"]["removed"] is True
    assert noop["data"]["removed"] is False
    assert removed["data"]["dest"] == str(dest.resolve())
    assert noop["data"]["dest"] == str(dest.resolve())


# ── #1062: a second shipped skill, `crm-migration`, beside `crm` ─────────────
_PKG = Path(__file__).resolve().parent.parent


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under `root`, keyed by its relative POSIX path."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def _with_targets(monkeypatch, base: Path) -> None:
    import crm.commands.skill as skill_mod

    monkeypatch.setattr(skill_mod, "SKILL_TARGETS", {"claude": base})


def test_target_folders_are_unchanged_for_the_crm_skill():
    # A bare install resolves to the same folder per target as before #1062.
    import crm.commands.skill as skill_mod

    home = Path.home()
    assert skill_mod._resolve_skill_dest("claude", None, "crm") == home / ".claude/skills/crm"
    assert skill_mod._resolve_skill_dest("copilot", None, "crm") == home / ".copilot/skills/crm"
    assert skill_mod._resolve_skill_dest("cursor", None, "crm") == home / ".cursor/rules/crm"


def test_bare_install_writes_the_crm_bundle_to_the_crm_folder(tmp_path: Path, monkeypatch):
    import json

    runner = _runner(tmp_path, monkeypatch)
    _with_targets(monkeypatch, tmp_path / "skills")
    result = runner.invoke(cli, ["--json", "skill", "install"])
    assert result.exit_code == 0, result.output
    assert _tree(tmp_path / "skills" / "crm") == _tree(_PKG / "skills")
    assert sorted(p.name for p in (tmp_path / "skills").iterdir()) == ["crm"]
    data = json.loads(result.output)["data"]
    assert data["skill"] == "crm"
    assert data["dest"] == str(tmp_path / "skills" / "crm")


def test_migration_skill_installs_into_its_own_folder(tmp_path: Path, monkeypatch):
    import json

    runner = _runner(tmp_path, monkeypatch)
    _with_targets(monkeypatch, tmp_path / "skills")
    result = runner.invoke(cli, ["--json", "skill", "install", "--skill", "crm-migration"])
    assert result.exit_code == 0, result.output
    assert _tree(tmp_path / "skills" / "crm-migration") == _tree(_PKG / "migration-skill")
    assert json.loads(result.output)["data"]["skill"] == "crm-migration"


def test_installing_one_skill_leaves_the_other_byte_identical(tmp_path: Path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    base = tmp_path / "skills"
    _with_targets(monkeypatch, base)
    assert runner.invoke(cli, ["skill", "install"]).exit_code == 0
    crm_before = _tree(base / "crm")
    assert runner.invoke(cli, ["skill", "install", "--skill", "crm-migration"]).exit_code == 0
    assert _tree(base / "crm") == crm_before
    mig_before = _tree(base / "crm-migration")
    assert runner.invoke(cli, ["skill", "install", "--force"]).exit_code == 0
    assert _tree(base / "crm-migration") == mig_before


def test_repeated_skill_installs_both(tmp_path: Path, monkeypatch):
    import json

    runner = _runner(tmp_path, monkeypatch)
    _with_targets(monkeypatch, tmp_path / "skills")
    result = runner.invoke(
        cli, ["--json", "skill", "install", "--skill", "crm", "--skill", "crm-migration"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)["data"]
    assert [s["skill"] for s in data["skills"]] == ["crm", "crm-migration"]
    assert (tmp_path / "skills" / "crm" / "SKILL.md").is_file()
    assert (tmp_path / "skills" / "crm-migration" / "SKILL.md").is_file()


def test_dest_with_several_skills_is_a_usage_error(tmp_path: Path, monkeypatch):
    runner = _runner(tmp_path, monkeypatch)
    for verb in ("install", "uninstall"):
        result = runner.invoke(
            cli,
            ["skill", verb, "--dest", str(tmp_path / "x"), "--skill", "crm"]
            + ["--skill", "crm-migration"],
        )
        assert result.exit_code == 2, (verb, result.output)
    assert not (tmp_path / "x").exists()


def test_install_records_the_skill_name(tmp_path: Path, monkeypatch):
    from crm.commands import skill_registry as reg

    runner = _runner(tmp_path, monkeypatch)
    _with_targets(monkeypatch, tmp_path / "skills")
    runner.invoke(cli, ["skill", "install", "--skill", "crm-migration"])
    assert [e["skill"] for e in reg.read_skills()] == ["crm-migration"]


def test_uninstall_one_skill_keeps_the_other(tmp_path: Path, monkeypatch):
    from crm.commands import skill_registry as reg

    runner = _runner(tmp_path, monkeypatch)
    base = tmp_path / "skills"
    _with_targets(monkeypatch, base)
    runner.invoke(cli, ["skill", "install", "--skill", "crm", "--skill", "crm-migration"])
    crm_before = _tree(base / "crm")
    result = runner.invoke(cli, ["--json", "skill", "uninstall", "--skill", "crm-migration"])
    assert result.exit_code == 0, result.output
    assert not (base / "crm-migration").exists()
    assert _tree(base / "crm") == crm_before
    assert [e["skill"] for e in reg.read_skills()] == ["crm"]
