"""Unit tests for crm.core.solutionpackager — offline SolutionPackager.exe bridge.

These are OFFLINE local-file transforms: no backend, no connection, no profile.
The external SolutionPackager process is the only collaborator, so the tests
fake `subprocess.run` (the process boundary) and assert argv passthrough,
exit-code propagation, the emitted envelope, timeout handling, and exe
resolution. Faking at the process boundary keeps the tests cross-platform
(CI runs pytest on both Linux and Windows).
"""
# pyright: basic

from __future__ import annotations

import json

import pytest

from click.testing import CliRunner

from crm.cli import cli
from crm.core import solutionpackager as sp
from crm.utils.d365_backend import D365Error


class _FakeCompleted:
    def __init__(self, args, returncode=0, stdout="", stderr=""):
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def fake_run(monkeypatch):
    """Capture every subprocess.run call; return a clean exit by default."""
    calls = []

    def _run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _FakeCompleted(argv, returncode=0, stdout="Processing... done\n")

    monkeypatch.setattr(sp.subprocess, "run", _run)
    return calls


@pytest.fixture
def exe(tmp_path):
    """A path that looks like a real SolutionPackager binary."""
    p = tmp_path / "SolutionPackager.exe"
    p.touch()
    return str(p)


def test_extract_builds_argv_and_returns_envelope(fake_run, exe):
    info = sp.extract_solution(
        zipfile="sol.zip", folder="src/sol", solutionpackager_path=exe,
    )
    argv = fake_run[-1][0]
    assert argv[0] == exe
    assert "/action:Extract" in argv
    assert "/zipfile:sol.zip" in argv
    assert "/folder:src/sol" in argv
    assert "/packagetype:Unmanaged" in argv
    assert info["action"] == "Extract"
    assert info["exit_code"] == 0
    assert info["folder"] == "src/sol"
    assert info["zipfile"] == "sol.zip"


def test_pack_builds_reverse_argv(fake_run, exe):
    info = sp.pack_solution(
        zipfile="out.zip", folder="src/sol", solutionpackager_path=exe,
    )
    argv = fake_run[-1][0]
    assert "/action:Pack" in argv
    assert "/zipfile:out.zip" in argv
    assert "/folder:src/sol" in argv
    assert info["action"] == "Pack"
    assert info["zipfile"] == "out.zip"


def test_exit_code_propagated_and_stdout_tailed(monkeypatch, exe):
    body = "\n".join(f"line{i}" for i in range(30)) + "\n"

    def _run(argv, **kw):
        return _FakeCompleted(argv, returncode=7, stdout=body)

    monkeypatch.setattr(sp.subprocess, "run", _run)
    info = sp.extract_solution(zipfile="s.zip", folder="f", solutionpackager_path=exe)

    assert info["exit_code"] == 7
    tail_lines = info["stdout_tail"].splitlines()
    assert len(tail_lines) <= 20            # only the tail, not all 30 lines
    assert tail_lines[-1] == "line29"       # ...ending at the real last line
    assert "line0" not in info["stdout_tail"]  # the head is dropped


def test_timeout_raises_d365error(monkeypatch, exe):
    def _run(argv, **kw):
        raise sp.subprocess.TimeoutExpired(cmd=argv, timeout=5)

    monkeypatch.setattr(sp.subprocess, "run", _run)
    with pytest.raises(D365Error, match="timed out"):
        sp.pack_solution(
            zipfile="s.zip", folder="f", solutionpackager_path=exe, timeout=5,
        )


# ── exe resolution: flag → CRM_SOLUTIONPACKAGER env → which → NuGet error ──────


def test_resolves_exe_from_env_when_no_flag(fake_run, monkeypatch, exe):
    monkeypatch.setenv("CRM_SOLUTIONPACKAGER", exe)
    sp.extract_solution(zipfile="s.zip", folder="f")
    assert fake_run[-1][0][0] == exe


def test_resolves_exe_from_which_when_no_flag_or_env(fake_run, monkeypatch, exe):
    monkeypatch.delenv("CRM_SOLUTIONPACKAGER", raising=False)
    monkeypatch.setattr(
        sp.shutil, "which", lambda name: exe if "SolutionPackager" in name else None,
    )
    sp.extract_solution(zipfile="s.zip", folder="f")
    assert fake_run[-1][0][0] == exe


def test_flag_takes_precedence_over_env(fake_run, monkeypatch, tmp_path):
    env_exe = tmp_path / "env.exe"
    env_exe.touch()
    flag_exe = tmp_path / "flag.exe"
    flag_exe.touch()
    monkeypatch.setenv("CRM_SOLUTIONPACKAGER", str(env_exe))
    sp.pack_solution(zipfile="s.zip", folder="f", solutionpackager_path=str(flag_exe))
    assert fake_run[-1][0][0] == str(flag_exe)


def test_absent_binary_raises_actionable_nuget_error(monkeypatch):
    monkeypatch.delenv("CRM_SOLUTIONPACKAGER", raising=False)
    monkeypatch.setattr(sp.shutil, "which", lambda name: None)
    with pytest.raises(D365Error, match="Microsoft.CrmSdk.CoreTools"):
        sp.extract_solution(zipfile="s.zip", folder="f")


# ── package type: Unmanaged | Managed | Both (case-insensitive) ───────────────


@pytest.mark.parametrize(
    "given,expected",
    [("Managed", "Managed"), ("both", "Both"), ("UNMANAGED", "Unmanaged")],
)
def test_package_type_normalized_and_passed(fake_run, exe, given, expected):
    sp.extract_solution(
        zipfile="s.zip", folder="f", package_type=given, solutionpackager_path=exe,
    )
    assert f"/packagetype:{expected}" in fake_run[-1][0]


def test_invalid_package_type_rejected_before_shelling_out(monkeypatch, exe):
    def _boom(*a, **k):
        raise AssertionError("subprocess.run must not be called on a bad package type")

    monkeypatch.setattr(sp.subprocess, "run", _boom)
    with pytest.raises(D365Error, match="package.?type|Unmanaged"):
        sp.pack_solution(
            zipfile="s.zip", folder="f", package_type="Bogus", solutionpackager_path=exe,
        )


# ── command wiring: `crm solution extract` / `pack` must run OFFLINE ───────────


def _backend_forbidden(self):
    raise AssertionError("extract/pack are offline; they must not open a backend")


class TestPackagerCommands:
    def test_extract_command_runs_offline_and_wires_core(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "crm.core.solutionpackager.extract_solution",
            lambda **kw: captured.update(kw) or {"action": "Extract", "exit_code": 0},
        )
        # Any backend/connection bootstrap would explode the test → proves offline.
        monkeypatch.setattr("crm.cli.CLIContext.backend", _backend_forbidden)
        result = CliRunner().invoke(cli, [
            "--json", "solution", "extract",
            "--zipfile", "sol.zip", "--folder", "src/sol",
            "--package-type", "Both",
            "--solutionpackager-path", "/x/SolutionPackager.exe",
            "--timeout", "30",
        ])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        assert envelope["data"]["action"] == "Extract"
        assert captured["zipfile"] == "sol.zip"
        assert captured["folder"] == "src/sol"
        assert captured["package_type"] == "Both"
        assert captured["solutionpackager_path"] == "/x/SolutionPackager.exe"
        assert captured["timeout"] == 30

    def test_pack_command_runs_offline_and_wires_core(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            "crm.core.solutionpackager.pack_solution",
            lambda **kw: captured.update(kw) or {"action": "Pack", "exit_code": 0},
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", _backend_forbidden)
        result = CliRunner().invoke(cli, [
            "--json", "solution", "pack",
            "--zipfile", "out.zip", "--folder", "src/sol",
        ])
        assert result.exit_code == 0, result.output
        envelope = json.loads(result.output)
        assert envelope["ok"] is True
        assert envelope["data"]["action"] == "Pack"
        assert captured["zipfile"] == "out.zip"
        assert captured["folder"] == "src/sol"
        assert captured["package_type"] == "Unmanaged"   # default

    def test_command_propagates_nonzero_solutionpackager_exit(self, monkeypatch):
        # A SolutionPackager failure must surface as a CLI failure (ADR 0001),
        # while still showing the exit code + stdout_tail for diagnosis.
        monkeypatch.setattr(
            "crm.core.solutionpackager.pack_solution",
            lambda **kw: {"action": "Pack", "exit_code": 2,
                          "folder": "f", "zipfile": "z", "stdout_tail": "boom"},
        )
        monkeypatch.setattr("crm.cli.CLIContext.backend", _backend_forbidden)
        result = CliRunner().invoke(cli, [
            "--json", "solution", "pack", "--zipfile", "z", "--folder", "f",
        ])
        assert result.exit_code == 1, result.output
        envelope = json.loads(result.output)
        assert envelope["ok"] is False
        assert envelope["data"]["exit_code"] == 2
        assert envelope["data"]["stdout_tail"] == "boom"

    def test_extract_command_absent_binary_exits_1(self, monkeypatch):
        def _raise(**kw):
            raise D365Error("SolutionPackager executable not found on PATH. "
                            "Install it from the Microsoft.CrmSdk.CoreTools NuGet package.")

        monkeypatch.setattr("crm.core.solutionpackager.extract_solution", _raise)
        monkeypatch.setattr("crm.cli.CLIContext.backend", _backend_forbidden)
        result = CliRunner().invoke(cli, [
            "--json", "solution", "extract",
            "--zipfile", "sol.zip", "--folder", "src/sol",
        ])
        assert result.exit_code == 1, result.output
        envelope = json.loads(result.output)
        assert envelope["ok"] is False
        assert "Microsoft.CrmSdk.CoreTools" in envelope["error"]
