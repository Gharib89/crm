# pyright: basic
"""Command + registry tests for `crm completion`."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.shell_completion import get_completion_class
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

    def test_powershell_source(self):
        # PowerShell uses Click's add_completion_class hook + a Register-ArgumentCompleter
        # shim; the script must key off the same _CRM_COMPLETE var the binary derives
        # from prog_name "crm".
        src = reg.generate_source("powershell")
        assert "Register-ArgumentCompleter -Native -CommandName crm" in src
        assert "_CRM_COMPLETE" in src

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

    def test_powershell_writes_ps1_and_dot_source_line(self):
        result = CliRunner().invoke(cli, ["completion", "install", "--shell", "powershell"])
        assert result.exit_code == 0
        marker = reg.read_marker()
        assert marker is not None
        script_path = Path(marker["script_path"])
        assert script_path.name == "crm.ps1"
        assert marker["shell"] == "powershell"
        assert "Register-ArgumentCompleter" in script_path.read_text(encoding="utf-8")
        # PowerShell dot-sources from $PROFILE — the line is `. <path>`, not `source`.
        assert f". {script_path}" in result.output
        assert f"source {script_path}" not in result.output

    def test_powershell_json_shape(self):
        result = CliRunner().invoke(cli, ["--json", "completion", "install", "--shell", "powershell"])
        data = json.loads(result.output)["data"]
        assert data["shell"] == "powershell"
        assert data["installed"] is True
        assert data["script_path"].endswith("crm.ps1")
        assert data["rc_line"] == f". {data['script_path']}"

    def test_invalid_shell_rejected(self):
        # An unsupported shell (powershell is now valid) is a Click usage error.
        result = CliRunner().invoke(cli, ["completion", "install", "--shell", "nushell"])
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


class TestPowerShellCompletion:
    """The PowerShellComplete class itself — the arg split + the completion path."""

    def _comp(self):
        from crm.cli import cli  # import triggers eager add_completion_class
        from crm.commands.completion_registry import PowerShellComplete
        return PowerShellComplete(cli, {}, "crm", reg._COMPLETE_VAR)

    def test_args_split_pops_trailing_partial(self, monkeypatch):
        # `crm entity cr<TAB>`: full line in COMP_WORDS, partial word in COMP_CWORD.
        monkeypatch.setenv("COMP_WORDS", "crm entity cr")
        monkeypatch.setenv("COMP_CWORD", "cr")
        args, incomplete = self._comp().get_completion_args()
        assert args == ["entity"]  # the partial `cr` is popped, not treated as an arg
        assert incomplete == "cr"

    def test_args_trailing_space_keeps_all(self, monkeypatch):
        # `crm entity <TAB>`: empty partial, nothing to pop.
        monkeypatch.setenv("COMP_WORDS", "crm entity")
        monkeypatch.setenv("COMP_CWORD", "")
        args, incomplete = self._comp().get_completion_args()
        assert args == ["entity"]
        assert incomplete == ""

    def test_complete_lists_top_level_commands(self, monkeypatch):
        # End-to-end: `crm <TAB>` yields top-level command names, tab-formatted as
        # `plain\t<value>\t<help>` (what the Register-ArgumentCompleter shim parses).
        monkeypatch.setenv("COMP_WORDS", "crm ")
        monkeypatch.setenv("COMP_CWORD", "")
        out = self._comp().complete()
        assert "\tentity\t" in out
        assert out.splitlines()[0].startswith("plain\t")


class TestEntryWiring:
    """The two non-obvious Windows fixes: eager registration + prog_name pin."""

    def test_powershell_registered_eagerly(self):
        # Importing crm.cli (the always-loaded entry module) must register the
        # PowerShell class on Click's completion hot-path. Command modules are
        # lazy-loaded, so a completion request never imports completion_registry on
        # its own — without eager registration get_completion_class returns None and
        # completion silently emits nothing. Regression for that failure.
        assert get_completion_class("powershell") is not None

    def test_main_pins_prog_name_so_exe_basename_does_not_break_completion(
        self, monkeypatch, capsysbinary
    ):
        # On Windows the binary is `crm.exe`; without the prog_name pin Click derives
        # the completion var from the basename -> `_CRM_EXE_COMPLETE`, but the script
        # sets `_CRM_COMPLETE` -> mismatch -> no completion. main() pins
        # prog_name="crm" so the var is `_CRM_COMPLETE` regardless of argv[0].
        from crm.cli import main
        monkeypatch.setattr(sys, "argv", ["crm.exe"])
        monkeypatch.setenv(reg._COMPLETE_VAR, "powershell_source")  # _CRM_COMPLETE
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
        out = capsysbinary.readouterr().out
        assert b"Register-ArgumentCompleter -Native -CommandName crm" in out


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
