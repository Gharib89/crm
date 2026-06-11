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


def test_uninstall_removes_tree(tmp_path: Path, monkeypatch):
    dest = tmp_path / "crm"
    runner = _runner(tmp_path, monkeypatch)
    runner.invoke(cli, ["--json", "skill", "install", "--dest", str(dest), "--force"])
    result = runner.invoke(cli, ["--json", "skill", "uninstall", "--dest", str(dest)])
    assert result.exit_code == 0, result.output
    assert not (dest / "SKILL.md").exists()
    assert not (dest / "reference").exists()
