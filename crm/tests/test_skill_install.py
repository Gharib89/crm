# crm/tests/test_skill_install.py
# pyright: basic
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from crm.cli import cli


def _runner(tmp_path: Path, monkeypatch) -> CliRunner:
    # Don't load the repo's real ./.env during a no-connection skill command.
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    return CliRunner()


def test_install_copies_tree(tmp_path: Path, monkeypatch):
    dest = tmp_path / "crm"
    result = _runner(tmp_path, monkeypatch).invoke(
        cli, ["--json", "skill", "install", "--dest", str(dest), "--force"]
    )
    assert result.exit_code == 0, result.output
    assert (dest / "SKILL.md").exists()
    assert (dest / "reference" / "records.md").exists()


def test_uninstall_removes_tree(tmp_path: Path, monkeypatch):
    dest = tmp_path / "crm"
    runner = _runner(tmp_path, monkeypatch)
    runner.invoke(cli, ["--json", "skill", "install", "--dest", str(dest), "--force"])
    result = runner.invoke(cli, ["--json", "skill", "uninstall", "--dest", str(dest)])
    assert result.exit_code == 0, result.output
    assert not (dest / "SKILL.md").exists()
    assert not (dest / "reference").exists()
