"""CLI tests for `crm security` command group."""
# pyright: basic
from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


# ── fixtures ──────────────────────────────────────────────────────────────


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


@pytest.fixture(autouse=True)
def _isolate_dotenv():
    saved = dict(os.environ)
    os.environ["CRM_DOTENV"] = "/dev/null"
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


# ── helpers ───────────────────────────────────────────────────────────────


def _stub_backend(monkeypatch, backend):
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)


_ROLES = [
    {"name": "System Administrator", "roleid": "role-1111", "_businessunitid_value": "bu-aaaa"},
    {"name": "Sales Manager", "roleid": "role-2222", "_businessunitid_value": "bu-bbbb"},
]

_USER_ROLES = [
    {"name": "System Administrator", "roleid": "role-1111"},
]

_TEAM_ROLES = [
    {"name": "Sales Manager", "roleid": "role-2222"},
]

_ASSIGN_OK = {"associated": True}


# ── list-roles ────────────────────────────────────────────────────────────


class TestListRoles:
    def test_human_shows_table(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "crm.commands.security.security_mod.list_roles",
            lambda *args, **kwargs: _ROLES,
        )
        result = CliRunner().invoke(cli, ["security", "list-roles"])
        assert result.exit_code == 0, result.output
        assert "System Administrator" in result.output
        assert "Sales Manager" in result.output
        assert "bu-aaaa" in result.output

    def test_json_envelope(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "crm.commands.security.security_mod.list_roles",
            lambda *args, **kwargs: _ROLES,
        )
        result = CliRunner().invoke(cli, ["--json", "security", "list-roles"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.stdout)
        assert env["ok"] is True
        assert len(env["data"]) == 2
        assert env["meta"]["count"] == 2

    def test_without_business_unit_passes_none(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        calls = []

        def _fake_list_roles(b, *, business_unit=None):
            calls.append(business_unit)
            return _ROLES

        monkeypatch.setattr(
            "crm.commands.security.security_mod.list_roles", _fake_list_roles
        )
        CliRunner().invoke(cli, ["security", "list-roles"])
        assert calls == [None]

    def test_with_business_unit_forwarded(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        calls = []

        def _fake_list_roles(b, *, business_unit=None):
            calls.append(business_unit)
            return []

        monkeypatch.setattr(
            "crm.commands.security.security_mod.list_roles", _fake_list_roles
        )
        CliRunner().invoke(cli, ["security", "list-roles", "--business-unit", "bu-aaaa"])
        assert calls == ["bu-aaaa"]

    def test_d365_error_clean_envelope(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "crm.commands.security.security_mod.list_roles",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                D365Error("Forbidden", status=403, code="0x80040220")
            ),
        )
        result = CliRunner().invoke(cli, ["--json", "security", "list-roles"])
        assert result.exit_code == 1, result.output
        env = json.loads(result.stdout)
        assert env["ok"] is False
        assert "Forbidden" in env["error"]


# ── list-user-roles ───────────────────────────────────────────────────────


class TestListUserRoles:
    def test_human_shows_role_name(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "crm.commands.security.security_mod.list_user_roles",
            lambda b, user_id: _USER_ROLES,
        )
        result = CliRunner().invoke(cli, ["security", "list-user-roles", "user-guid-1"])
        assert result.exit_code == 0, result.output
        assert "System Administrator" in result.output

    def test_json_envelope(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "crm.commands.security.security_mod.list_user_roles",
            lambda b, user_id: _USER_ROLES,
        )
        result = CliRunner().invoke(
            cli, ["--json", "security", "list-user-roles", "user-guid-1"]
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.stdout)
        assert env["ok"] is True
        assert env["meta"]["count"] == 1


# ── list-team-roles ───────────────────────────────────────────────────────


class TestListTeamRoles:
    def test_human_shows_role_name(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "crm.commands.security.security_mod.list_team_roles",
            lambda b, team_id: _TEAM_ROLES,
        )
        result = CliRunner().invoke(cli, ["security", "list-team-roles", "team-guid-1"])
        assert result.exit_code == 0, result.output
        assert "Sales Manager" in result.output

    def test_json_envelope(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "crm.commands.security.security_mod.list_team_roles",
            lambda b, team_id: _TEAM_ROLES,
        )
        result = CliRunner().invoke(
            cli, ["--json", "security", "list-team-roles", "team-guid-1"]
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.stdout)
        assert env["ok"] is True
        assert env["meta"]["count"] == 1


# ── assign-role ───────────────────────────────────────────────────────────


class TestAssignRole:
    def test_neither_flag_is_usage_error(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, ["security", "assign-role", "role-1111"])
        assert result.exit_code == 2

    def test_both_flags_is_usage_error(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "security", "assign-role", "role-1111",
            "--to-user", "user-guid-1", "--to-team", "team-guid-1",
        ])
        assert result.exit_code == 2

    def test_assign_to_user_with_yes_succeeds(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        calls = []

        def _fake_assign_user(b, user_id, role_id, **kw):
            calls.append((user_id, role_id))
            return _ASSIGN_OK

        monkeypatch.setattr(
            "crm.commands.security.security_mod.assign_role_to_user",
            _fake_assign_user,
        )
        result = CliRunner().invoke(cli, [
            "--json", "security", "assign-role", "role-1111",
            "--to-user", "user-guid-1", "--yes",
        ])
        assert result.exit_code == 0, result.output
        assert calls == [("user-guid-1", "role-1111")]
        env = json.loads(result.stdout)
        assert env["ok"] is True
        assert env["data"]["associated"] is True

    def test_assign_to_team_with_yes_succeeds(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        calls = []

        def _fake_assign_team(b, team_id, role_id, **kw):
            calls.append((team_id, role_id))
            return _ASSIGN_OK

        monkeypatch.setattr(
            "crm.commands.security.security_mod.assign_role_to_team",
            _fake_assign_team,
        )
        result = CliRunner().invoke(cli, [
            "--json", "security", "assign-role", "role-2222",
            "--to-team", "team-guid-1", "--yes",
        ])
        assert result.exit_code == 0, result.output
        assert calls == [("team-guid-1", "role-2222")]
        env = json.loads(result.stdout)
        assert env["ok"] is True

    def test_assign_without_yes_non_interactive_aborts(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "crm.commands.security.security_mod.assign_role_to_user",
            lambda b, user_id, role_id, **kw: _ASSIGN_OK,
        )
        result = CliRunner().invoke(cli, [
            "--json", "security", "assign-role", "role-1111",
            "--to-user", "user-guid-1",
        ], input="")  # EOF on stdin -> Abort
        assert result.exit_code == 1, result.output
        assert "user-guid-1" in result.output
        # CliRunner mixes the confirm prompt into stdout before the JSON envelope;
        # strip prompt prefix and parse the trailing JSON object.
        json_start = result.stdout.rfind("{")
        env = json.loads(result.stdout[json_start:])
        assert env["ok"] is False
        assert env["error"] == "aborted by user"

    def test_assign_d365_error_clean_envelope(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "crm.commands.security.security_mod.assign_role_to_user",
            lambda b, user_id, role_id, **kw: (_ for _ in ()).throw(
                D365Error("Forbidden", status=403, code="0x80040220")
            ),
        )
        result = CliRunner().invoke(cli, [
            "--json", "security", "assign-role", "role-1111",
            "--to-user", "user-guid-1", "--yes",
        ])
        assert result.exit_code == 1, result.output
        env = json.loads(result.stdout)
        assert env["ok"] is False
        assert "Forbidden" in env["error"]
        assert env["meta"]["status"] == 403
