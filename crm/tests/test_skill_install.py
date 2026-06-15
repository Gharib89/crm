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

    assert skill_mod._resolve_skill_dest(None, None) == skill_mod.SKILL_TARGETS["claude"]


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
    from crm.commands import skill_registry as reg
    import crm.commands.skill as skill_mod

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
