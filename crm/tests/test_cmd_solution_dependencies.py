# pyright: basic
"""CLI tests for `solution dependencies <unique-name>` command (#116).

Mirrors test_cmd_metadata_dependencies.py — uninstall-blocker read via
RetrieveDependenciesForUninstall(SolutionUniqueName='<name>').
"""
from __future__ import annotations

import json

from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.utils.d365_backend import D365Error


# ── fixtures ─────────────────────────────────────────────────────────────────


# ── canned return values ──────────────────────────────────────────────────────

_NO_BLOCKERS = {
    "solution": "MySolution",
    "blockers": [],
    "count": 0,
}

_WITH_BLOCKERS = {
    "solution": "MySolution",
    "blockers": [
        {
            "dependent_type": "Attribute",
            "dependent_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "dependent_parent_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "required_type": "Entity",
            "dependency_type": 1,
        }
    ],
    "count": 1,
}


# ── helpers ───────────────────────────────────────────────────────────────────


def _stub_backend(monkeypatch, backend):
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)


def _stub_retrieve(monkeypatch, return_value):
    monkeypatch.setattr(
        "crm.commands.solution.dep_mod.retrieve_dependencies_for_uninstall",
        lambda *args, **kwargs: return_value,
    )


# ── tests ─────────────────────────────────────────────────────────────────────


class TestJsonMode:
    def test_json_envelope_has_data_and_meta(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        _stub_retrieve(monkeypatch, _NO_BLOCKERS)
        result = CliRunner().invoke(cli, ["--json", "solution", "dependencies", "MySolution"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["solution"] == "MySolution"
        assert env["data"]["blockers"] == []
        assert env["data"]["count"] == 0
        assert env["meta"]["blockers"] == 0

    def test_json_with_blockers(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        _stub_retrieve(monkeypatch, _WITH_BLOCKERS)
        result = CliRunner().invoke(cli, ["--json", "solution", "dependencies", "MySolution"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert len(env["data"]["blockers"]) == 1
        assert env["data"]["count"] == 1
        assert env["meta"]["blockers"] == 1


class TestHumanModeWithBlockers:
    def test_table_shows_headers_and_blocker_fields(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        _stub_retrieve(monkeypatch, _WITH_BLOCKERS)
        result = CliRunner().invoke(cli, ["solution", "dependencies", "MySolution"])
        assert result.exit_code == 0, result.output
        assert "Dependent Type" in result.output
        assert "Dependent Id" in result.output
        assert "Attribute" in result.output
        assert "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" in result.output

    def test_human_meta_shows_blocker_count(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        _stub_retrieve(monkeypatch, _WITH_BLOCKERS)
        result = CliRunner().invoke(cli, ["solution", "dependencies", "MySolution"])
        assert result.exit_code == 0, result.output
        # meta renders as status lines in human mode
        assert "blockers" in result.output


class TestHumanModeNoBlockers:
    def test_clear_no_blockers_indication(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        _stub_retrieve(monkeypatch, _NO_BLOCKERS)
        result = CliRunner().invoke(cli, ["solution", "dependencies", "MySolution"])
        assert result.exit_code == 0, result.output
        # meta carries the count; data carries the zeroed blocker indication.
        # Assert the empty branch was taken (no blocker table) and the solution
        # name surfaces — distinguishes this from the table branch.
        assert "blockers" in result.output
        assert "Dependent Type" not in result.output
        assert "MySolution" in result.output


class TestUsageErrors:
    def test_empty_name_exits_2(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        _stub_retrieve(monkeypatch, _NO_BLOCKERS)
        result = CliRunner().invoke(cli, ["solution", "dependencies", ""])
        assert result.exit_code == 2

    def test_missing_arg_exits_2(self):
        # Click's required-argument enforcement (no backend touched).
        result = CliRunner().invoke(cli, ["solution", "dependencies"])
        assert result.exit_code == 2


class TestErrorPath:
    def test_404_yields_ok_false_envelope(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "crm.commands.solution.dep_mod.retrieve_dependencies_for_uninstall",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                D365Error("not found", status=404, code="0x80040217")
            ),
        )
        result = CliRunner().invoke(
            cli, ["--json", "solution", "dependencies", "no_such"]
        )
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False

    def test_help_available(self):
        result = CliRunner().invoke(cli, ["solution", "dependencies", "--help"])
        assert result.exit_code == 0
        assert "uninstall" in result.output.lower()
        assert "blockers" in result.output.lower()
