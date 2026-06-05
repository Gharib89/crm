# pyright: basic
"""CLI tests for `metadata dependencies <target>` command (#81)."""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile: ConnectionProfile) -> D365Backend:
    return D365Backend(profile, password="pw", dry_run=False)


# ── canned return values ──────────────────────────────────────────────────────

_NO_BLOCKERS = {
    "can_delete": True,
    "blockers": [],
    "metadata_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "component_type": 1,
    "kind": "entity",
    "for": "delete",
}

_WITH_BLOCKERS = {
    "can_delete": False,
    "blockers": [
        {
            "dependent_type": "Attribute",
            "dependent_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "dependent_parent_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "required_type": "Entity",
            "dependency_type": 1,
        }
    ],
    "metadata_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    "component_type": 1,
    "kind": "entity",
    "for": "delete",
}


# ── helpers ───────────────────────────────────────────────────────────────────


def _stub_backend(monkeypatch, backend):
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)


def _stub_retrieve(monkeypatch, return_value):
    monkeypatch.setattr(
        "crm.commands.metadata.dep_mod.retrieve_dependencies",
        lambda *args, **kwargs: return_value,
    )


# ── tests ─────────────────────────────────────────────────────────────────────


class TestJsonMode:
    def test_json_envelope_has_data_and_meta(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        _stub_retrieve(monkeypatch, _NO_BLOCKERS)
        result = CliRunner().invoke(cli, ["--json", "metadata", "dependencies", "new_widget"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["can_delete"] is True
        assert env["data"]["blockers"] == []
        assert env["meta"]["can_delete"] is True
        assert env["meta"]["blockers"] == 0

    def test_json_with_blockers(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        _stub_retrieve(monkeypatch, _WITH_BLOCKERS)
        result = CliRunner().invoke(cli, ["--json", "metadata", "dependencies", "new_widget"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["can_delete"] is False
        assert len(env["data"]["blockers"]) == 1
        assert env["meta"]["can_delete"] is False
        assert env["meta"]["blockers"] == 1


class TestHumanModeWithBlockers:
    def test_table_shows_headers_and_blocker_fields(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        _stub_retrieve(monkeypatch, _WITH_BLOCKERS)
        result = CliRunner().invoke(cli, ["metadata", "dependencies", "new_widget"])
        assert result.exit_code == 0, result.output
        assert "Dependent Type" in result.output
        assert "Dependent Id" in result.output
        assert "Attribute" in result.output
        assert "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" in result.output

    def test_human_meta_shows_can_delete(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        _stub_retrieve(monkeypatch, _WITH_BLOCKERS)
        result = CliRunner().invoke(cli, ["metadata", "dependencies", "new_widget"])
        assert result.exit_code == 0, result.output
        # meta renders as status lines in human mode
        assert "can_delete" in result.output


class TestHumanModeNoBlockers:
    def test_can_delete_shown_when_no_blockers(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        _stub_retrieve(monkeypatch, _NO_BLOCKERS)
        result = CliRunner().invoke(cli, ["metadata", "dependencies", "new_widget"])
        assert result.exit_code == 0, result.output
        assert "can_delete" in result.output


class TestOptionsForwarded:
    def test_kind_and_for_forwarded(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        captured = {}

        def _fake_retrieve(backend, kind, target, *, for_="delete"):
            captured["kind"] = kind
            captured["target"] = target
            captured["for_"] = for_
            return {**_NO_BLOCKERS, "kind": kind, "for": for_}

        monkeypatch.setattr(
            "crm.commands.metadata.dep_mod.retrieve_dependencies",
            _fake_retrieve,
        )
        result = CliRunner().invoke(
            cli,
            ["--json", "metadata", "dependencies", "new_widget.new_amount",
             "--kind", "attribute", "--for", "dependents"],
        )
        assert result.exit_code == 0, result.output
        assert captured["kind"] == "attribute"
        assert captured["target"] == "new_widget.new_amount"
        assert captured["for_"] == "dependents"


class TestErrorPath:
    def test_d365_error_yields_non_zero_exit(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "crm.commands.metadata.dep_mod.retrieve_dependencies",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                D365Error("not found", status=404, code="0x80040217")
            ),
        )
        result = CliRunner().invoke(
            cli, ["--json", "metadata", "dependencies", "no_such"]
        )
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False

    def test_help_available(self):
        result = CliRunner().invoke(cli, ["metadata", "dependencies", "--help"])
        assert result.exit_code == 0
        assert "dependencies" in result.output.lower()
        assert "blockers" in result.output.lower()
