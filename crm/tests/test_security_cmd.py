"""CLI tests for `crm security` command group."""
# pyright: basic
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.utils.d365_backend import D365Error

pytestmark = pytest.mark.usefixtures("isolated_home")


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


# ── user-privileges ───────────────────────────────────────────────────────


_USER_PRIVILEGES = [
    {"PrivilegeName": "prvReadAccount", "Depth": "Global", "PrivilegeId": "priv-1111"},
]


class TestUserPrivileges:
    def test_human_shows_privilege_name_and_depth(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "crm.commands.security.security_mod.list_user_privileges",
            lambda b, user_id: _USER_PRIVILEGES,
        )
        result = CliRunner().invoke(cli, ["security", "user-privileges", "user-guid-1"])
        assert result.exit_code == 0, result.output
        assert "prvReadAccount" in result.output
        assert "Global" in result.output

    def test_json_envelope(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "crm.commands.security.security_mod.list_user_privileges",
            lambda b, user_id: _USER_PRIVILEGES,
        )
        result = CliRunner().invoke(
            cli, ["--json", "security", "user-privileges", "user-guid-1"]
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.stdout)
        assert env["ok"] is True
        assert env["meta"]["count"] == 1
        assert env["data"][0]["PrivilegeName"] == "prvReadAccount"


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


# ── grant ───────────────────────────────────────────────────────────────────


class TestGrant:
    def test_forwards_parsed_principal_and_rights(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        calls = []

        def _fake_grant(b, entity_set, record_id, *, principal_type, principal_id, rights):
            calls.append((entity_set, record_id, principal_type, principal_id, rights))
            return {"granted": True}

        monkeypatch.setattr(
            "crm.commands.security.security_mod.grant_access", _fake_grant
        )
        result = CliRunner().invoke(cli, [
            "--json", "security", "grant", "accounts", "rec-1",
            "--to", "user:user-1", "--rights", "Read,Write", "--yes",
        ])
        assert result.exit_code == 0, result.output
        assert calls == [("accounts", "rec-1", "user", "user-1", "Read,Write")]
        env = json.loads(result.stdout)
        assert env["ok"] is True
        assert env["data"]["granted"] is True

    def test_malformed_principal_is_usage_error(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "security", "grant", "accounts", "rec-1",
            "--to", "user-1", "--rights", "Read", "--yes",
        ])
        assert result.exit_code == 2

    def test_missing_rights_is_usage_error(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "security", "grant", "accounts", "rec-1", "--to", "user:user-1", "--yes",
        ])
        assert result.exit_code == 2

    def test_without_yes_non_interactive_aborts(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "crm.commands.security.security_mod.grant_access",
            lambda *a, **k: {"granted": True},
        )
        result = CliRunner().invoke(cli, [
            "--json", "security", "grant", "accounts", "rec-1",
            "--to", "user:user-1", "--rights", "Read",
        ], input="")
        assert result.exit_code == 1, result.output
        json_start = result.stdout.rfind("{")
        env = json.loads(result.stdout[json_start:])
        assert env["ok"] is False
        assert env["error"] == "aborted by user"


# ── revoke ──────────────────────────────────────────────────────────────────


class TestRevoke:
    def test_forwards_parsed_principal(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        calls = []

        def _fake_revoke(b, entity_set, record_id, *, principal_type, principal_id):
            calls.append((entity_set, record_id, principal_type, principal_id))
            return {"revoked": True}

        monkeypatch.setattr(
            "crm.commands.security.security_mod.revoke_access", _fake_revoke
        )
        result = CliRunner().invoke(cli, [
            "--json", "security", "revoke", "accounts", "rec-1",
            "--from", "team:team-1", "--yes",
        ])
        assert result.exit_code == 0, result.output
        assert calls == [("accounts", "rec-1", "team", "team-1")]
        env = json.loads(result.stdout)
        assert env["ok"] is True
        assert env["data"]["revoked"] is True


# ── list-access ───────────────────────────────────────────────────────────


_SHARES = [
    {"principalType": "systemuser", "principalId": "user-1", "accessMask": "ReadAccess"},
    {"principalType": "team", "principalId": "team-1", "accessMask": "ReadAccess, WriteAccess"},
]


class TestListAccess:
    def test_human_shows_principals_and_mask(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "crm.commands.security.security_mod.list_access",
            lambda b, entity_set, record_id: _SHARES,
        )
        result = CliRunner().invoke(cli, ["security", "list-access", "accounts", "rec-1"])
        assert result.exit_code == 0, result.output
        assert "systemuser" in result.output
        assert "WriteAccess" in result.output

    def test_json_envelope(self, monkeypatch, backend):
        _stub_backend(monkeypatch, backend)
        monkeypatch.setattr(
            "crm.commands.security.security_mod.list_access",
            lambda b, entity_set, record_id: _SHARES,
        )
        result = CliRunner().invoke(
            cli, ["--json", "security", "list-access", "accounts", "rec-1"]
        )
        assert result.exit_code == 0, result.output
        env = json.loads(result.stdout)
        assert env["ok"] is True
        assert env["meta"]["count"] == 2
        assert env["data"][0]["principalType"] == "systemuser"
