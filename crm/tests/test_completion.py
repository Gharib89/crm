# pyright: basic
"""Command + registry tests for `crm completion`."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.commands import completion_registry as reg


@pytest.fixture(autouse=True)
def _no_update_check(isolated_home, monkeypatch):
    # isolated_home handles CRM_HOME / CRM_DOTENV; suppress the auto update
    # check so command runs are deterministic (cf. crm/cli.py).
    monkeypatch.setenv("CRM_NO_UPDATE_CHECK", "1")


class TestGenerate:
    def test_zsh_source_is_compdef(self):
        src = reg.generate_source("zsh")
        assert src.splitlines()[0] == "#compdef crm"

    def test_bash_and_fish_generate(self):
        assert "_crm_completion" in reg.generate_source("bash")
        assert "_crm_completion" in reg.generate_source("fish")

    def test_via_binary_empty_output_raises(self, monkeypatch):
        # A binary that exits 0 but emits nothing (not a real `crm` completion
        # invocation) must raise, so the frozen refresh never writes a blank script.
        # Stub subprocess.run so the test is portable (no reliance on a real binary).
        import subprocess

        monkeypatch.setattr(
            "crm.commands.completion_registry.subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="  \n", stderr="boom"),
        )
        with pytest.raises(RuntimeError, match="no completion output"):
            reg.generate_via_binary("zsh", "any-binary")


class TestShow:
    def test_human_prints_script(self):
        result = CliRunner().invoke(cli, ["completion", "show", "--shell", "zsh"])
        assert result.exit_code == 0
        assert result.output.splitlines()[0] == "#compdef crm"

    def test_json_shape(self):
        result = CliRunner().invoke(cli, ["--json", "completion", "show", "--shell", "bash"])
        assert result.exit_code == 0
        data = json.loads(result.output)["data"]
        assert data["shell"] == "bash"
        assert data["installed"] is False
        assert data["script_path"] is None
        assert data["rc_line"] is None
        assert "_crm_completion" in data["script"]

    def test_does_not_write_anything(self):
        CliRunner().invoke(cli, ["completion", "show", "--shell", "zsh"])
        assert reg.read_marker() is None  # show is read-only


class TestInstall:
    def test_writes_script_and_marker_and_prints_rc_line(self):
        result = CliRunner().invoke(cli, ["completion", "install", "--shell", "zsh"])
        assert result.exit_code == 0
        marker = reg.read_marker()
        assert marker is not None
        script_path = Path(marker["script_path"])
        assert script_path.read_text(encoding="utf-8").splitlines()[0] == "#compdef crm"
        assert marker["shell"] == "zsh"
        assert f"source {script_path}" in result.output
        # Human mode prints the copy-paste line, not the JSON key/value dump.
        assert "rc_line" not in result.output

    def test_idempotent_second_run(self):
        r1 = CliRunner().invoke(cli, ["--json", "completion", "install", "--shell", "zsh"])
        path1 = json.loads(r1.output)["data"]["script_path"]
        r2 = CliRunner().invoke(cli, ["--json", "completion", "install", "--shell", "zsh"])
        path2 = json.loads(r2.output)["data"]["script_path"]
        assert path1 == path2
        # Exactly one marker, no duplicate side-state.
        marker = reg.read_marker()
        assert marker is not None and marker["script_path"] == path1

    def test_json_shape(self):
        result = CliRunner().invoke(cli, ["--json", "completion", "install", "--shell", "fish"])
        data = json.loads(result.output)["data"]
        assert data["shell"] == "fish"
        assert data["installed"] is True
        assert data["script_path"].endswith("crm.fish")
        assert data["rc_line"] == f"source {data['script_path']}"

    def test_custom_path(self, tmp_path):
        target = tmp_path / "custom" / "crm.zsh"
        result = CliRunner().invoke(
            cli, ["--json", "completion", "install", "--shell", "zsh", "--path", str(target)]
        )
        assert result.exit_code == 0
        assert Path(json.loads(result.output)["data"]["script_path"]) == target.resolve()
        assert target.exists()

    def test_does_not_edit_rc_file(self, tmp_path, monkeypatch):
        # install must only write the cached script + marker, never a shell rc.
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        rc = fake_home / ".zshrc"
        rc.write_text("# original\n", encoding="utf-8")
        monkeypatch.setenv("HOME", str(fake_home))
        CliRunner().invoke(cli, ["completion", "install", "--shell", "zsh"])
        assert rc.read_text(encoding="utf-8") == "# original\n"

    def test_invalid_shell_rejected(self):
        result = CliRunner().invoke(cli, ["completion", "install", "--shell", "powershell"])
        assert result.exit_code == 2  # Click usage error (bad --shell choice)


class TestShellAutodetect:
    def test_uses_shell_env(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/usr/bin/fish")
        result = CliRunner().invoke(cli, ["--json", "completion", "show"])
        assert json.loads(result.output)["data"]["shell"] == "fish"

    def test_undetectable_errors(self, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/tcsh")
        result = CliRunner().invoke(cli, ["--json", "completion", "show"])
        # Undetectable shell is a usage error: exit 2 + the standard JSON envelope.
        assert result.exit_code == 2
        assert json.loads(result.output)["ok"] is False


class TestMarker:
    def test_roundtrip(self):
        reg.write_marker("zsh", "/abs/crm.zsh", "1.2.3")
        m = reg.read_marker()
        assert m == {"shell": "zsh", "script_path": "/abs/crm.zsh", "installed_version": "1.2.3"}

    def test_missing_is_none(self):
        assert reg.read_marker() is None

    def test_corrupt_is_none(self):
        reg.marker_path().write_text("{not json", encoding="utf-8")
        assert reg.read_marker() is None
