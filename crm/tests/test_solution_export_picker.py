"""CLI-seam tests for the no-arg interactive solution picker on `solution export` (#656).

Network-backed picker pilot: `crm solution export -o out.zip` with no solution
name, on a TTY in human mode, lists the org's solutions and lets the user pick.
Under --json / non-TTY the pre-#656 required-argument behavior (usage error,
exit 2) is preserved. The org fetch and the export core call are stubbed so these
tests pin the command seam (gating, ordering, envelopes), not the transport.
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from crm.cli import cli
from crm.utils.d365_backend import D365Error


_SOLUTIONS = [
    {"uniquename": "zmanaged", "friendlyname": "Z Managed", "version": "1.0.0.0", "ismanaged": True},
    {"uniquename": "myorgsln", "friendlyname": "My Org", "version": "2.3.0.0", "ismanaged": False},
    {"uniquename": "another", "friendlyname": "Another", "version": "1.1.0.0", "ismanaged": False},
]


def _stub(monkeypatch, backend, *, solutions=None, exported=None):
    """Wire a fake backend + a canned solution list + a no-op export."""
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
    monkeypatch.setattr(
        "crm.commands.solution.sol_mod.list_solutions",
        lambda *a, **k: list(_SOLUTIONS if solutions is None else solutions),
    )
    calls = {}

    def _fake_export(backend_, unique_name, output, **kw):
        calls["unique_name"] = unique_name
        calls["output"] = str(output)
        return {"action": "ExportSolution", "bytes": 42, "path": str(output)}

    monkeypatch.setattr(
        "crm.commands.solution.sol_mod.export_solution", _fake_export)
    return calls


def test_no_arg_tty_shows_picker_and_exports(monkeypatch, backend, tmp_path):
    calls = _stub(monkeypatch, backend)
    monkeypatch.setattr("crm.commands.solution._stdin_is_tty", lambda: True)
    monkeypatch.setattr("crm.commands.solution.select_one", lambda *a, **k: "myorgsln")
    out = tmp_path / "out.zip"
    res = CliRunner().invoke(cli, ["solution", "export", "-o", str(out)])
    assert res.exit_code == 0, res.output
    assert calls["unique_name"] == "myorgsln"


def test_explicit_name_bypasses_picker(monkeypatch, backend, tmp_path):
    calls = _stub(monkeypatch, backend)

    def _boom(*a, **k):
        raise AssertionError("picker must not run when a name is given")

    monkeypatch.setattr("crm.commands.solution.select_one", _boom)
    monkeypatch.setattr("crm.commands.solution.sol_mod.list_solutions", _boom)
    out = tmp_path / "out.zip"
    res = CliRunner().invoke(cli, ["solution", "export", "myorgsln", "-o", str(out)])
    assert res.exit_code == 0, res.output
    assert calls["unique_name"] == "myorgsln"


def test_no_arg_json_errors_exit_2(monkeypatch, backend, tmp_path):
    # --json with no name keeps the pre-#656 required-argument behavior: usage
    # error (exit 2), never the picker, even on a TTY.
    calls = _stub(monkeypatch, backend)
    monkeypatch.setattr("crm.commands.solution._stdin_is_tty", lambda: True)
    out = tmp_path / "out.zip"
    res = CliRunner().invoke(cli, ["--json", "solution", "export", "-o", str(out)])
    assert res.exit_code == 2, res.output
    assert "unique_name" not in calls


def test_no_arg_no_tty_errors_exit_2(monkeypatch, backend, tmp_path):
    calls = _stub(monkeypatch, backend)
    monkeypatch.setattr("crm.commands.solution._stdin_is_tty", lambda: False)
    out = tmp_path / "out.zip"
    res = CliRunner().invoke(cli, ["solution", "export", "-o", str(out)])
    assert res.exit_code == 2, res.output
    assert "unique_name" not in calls


def test_picker_cancel_emits_clean_error(monkeypatch, backend, tmp_path):
    calls = _stub(monkeypatch, backend)
    monkeypatch.setattr("crm.commands.solution._stdin_is_tty", lambda: True)
    monkeypatch.setattr("crm.commands.solution.select_one", lambda *a, **k: None)
    out = tmp_path / "out.zip"
    res = CliRunner().invoke(cli, ["--json", "solution", "export", "-o", str(out)])
    # --json + no name is exit 2 before the picker; force the picker path via a
    # human-mode run instead.
    res = CliRunner().invoke(cli, ["solution", "export", "-o", str(out)])
    assert res.exit_code == 1, res.output
    assert "unique_name" not in calls


def test_empty_solution_list_clean_error(monkeypatch, backend, tmp_path):
    calls = _stub(monkeypatch, backend, solutions=[])
    monkeypatch.setattr("crm.commands.solution._stdin_is_tty", lambda: True)
    monkeypatch.setattr("crm.commands.solution.select_one", lambda *a, **k: "x")
    out = tmp_path / "out.zip"
    res = CliRunner().invoke(cli, ["solution", "export", "-o", str(out)])
    assert res.exit_code == 1, res.output
    assert "unique_name" not in calls


def test_fetch_failure_is_operational_envelope(monkeypatch, backend, tmp_path):
    # A backend failure while listing solutions surfaces as the standard
    # operational-failure envelope (exit 1), not a picker/traceback crash.
    _stub(monkeypatch, backend)

    def _raise(*a, **k):
        raise D365Error("boom", status=500)

    monkeypatch.setattr("crm.commands.solution.sol_mod.list_solutions", _raise)
    monkeypatch.setattr("crm.commands.solution._stdin_is_tty", lambda: True)
    monkeypatch.setattr("crm.commands.solution.select_one", lambda *a, **k: "x")
    out = tmp_path / "out.zip"
    res = CliRunner().invoke(cli, ["--json", "solution", "export", "-o", str(out)])
    # --json is exit 2 pre-picker; the operational-failure path is human mode.
    res = CliRunner().invoke(cli, ["solution", "export", "-o", str(out)])
    assert res.exit_code == 1, res.output


def test_picker_lists_unmanaged_first_with_labels(monkeypatch, backend, tmp_path):
    _stub(monkeypatch, backend)
    seen = {}

    def _capture(title, items, default=None):
        seen["items"] = items
        return items[0][0]

    monkeypatch.setattr("crm.commands.solution._stdin_is_tty", lambda: True)
    monkeypatch.setattr("crm.commands.solution.select_one", _capture)
    out = tmp_path / "out.zip"
    res = CliRunner().invoke(cli, ["solution", "export", "-o", str(out)])
    assert res.exit_code == 0, res.output
    values = [v for v, _ in seen["items"]]
    # Unmanaged (name-sorted) before managed.
    assert values == ["another", "myorgsln", "zmanaged"]
    labels = {v: label for v, label in seen["items"]}
    assert "My Org" in labels["myorgsln"] and "v2.3.0.0" in labels["myorgsln"]
    assert "(managed)" in labels["zmanaged"]
    assert "(managed)" not in labels["myorgsln"]
