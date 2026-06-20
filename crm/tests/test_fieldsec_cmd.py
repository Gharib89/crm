"""CLI tests for the `crm fieldsec` command group."""
# pyright: basic
from __future__ import annotations

import json

import requests_mock as rm_module
from click.testing import CliRunner

from crm.cli import cli

_PROFILE_ID = "11112222-3333-4444-5555-666677778888"
_USER_ID = "aaaa1111-2222-3333-4444-555566667777"
_NEW_PERM_ID = "cccc1111-2222-3333-4444-555566667777"


def _profiles_url(backend) -> str:
    return backend.url_for("fieldsecurityprofiles")


def _perms_url(backend) -> str:
    return backend.url_for("fieldpermissions")


def _use_backend(backend, monkeypatch) -> None:
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)


def _entity_id_headers(backend, entity_set: str, rec_id: str) -> dict[str, str]:
    return {"OData-EntityId": backend.url_for(f"{entity_set}({rec_id})")}


class TestList:
    def test_renders_profiles(self, backend, monkeypatch):
        _use_backend(backend, monkeypatch)
        row = {"fieldsecurityprofileid": _PROFILE_ID, "name": "Comp", "description": "x"}
        with rm_module.Mocker() as m:
            m.get(_profiles_url(backend), json={"value": [row]})
            result = CliRunner().invoke(cli, ["--json", "fieldsec", "list"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"][0]["name"] == "Comp"


class TestGet:
    def test_includes_permissions(self, backend, monkeypatch):
        _use_backend(backend, monkeypatch)
        with rm_module.Mocker() as m:
            m.get(backend.url_for(f"fieldsecurityprofiles({_PROFILE_ID})"),
                  json={"fieldsecurityprofileid": _PROFILE_ID, "name": "Comp"})
            m.get(_perms_url(backend), json={"value": [
                {"fieldpermissionid": _NEW_PERM_ID, "entityname": "account",
                 "attributelogicalname": "creditlimit",
                 "canread": 4, "cancreate": 0, "canupdate": 0},
            ]})
            result = CliRunner().invoke(cli, ["--json", "fieldsec", "get", _PROFILE_ID])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["permissions"][0]["attributelogicalname"] == "creditlimit"


class TestCreateProfile:
    def test_creates_and_returns_id(self, backend, monkeypatch):
        _use_backend(backend, monkeypatch)
        with rm_module.Mocker() as m:
            m.post(_profiles_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "fieldsecurityprofiles", _PROFILE_ID))
            result = CliRunner().invoke(
                cli, ["--json", "fieldsec", "create-profile", "Comp", "--description", "x"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["created"] is True
        assert data["fieldsecurityprofileid"] == _PROFILE_ID

    def test_solution_flag_sets_header(self, backend, monkeypatch):
        _use_backend(backend, monkeypatch)
        with rm_module.Mocker() as m:
            m.post(_profiles_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "fieldsecurityprofiles", _PROFILE_ID))
            result = CliRunner().invoke(
                cli, ["--json", "fieldsec", "create-profile", "Comp", "--solution", "MySol"])
        assert result.exit_code == 0, result.output
        assert m.last_request.headers.get("MSCRM.SolutionUniqueName") == "MySol"

    def test_dry_run_does_not_post(self, dry_backend, monkeypatch):
        _use_backend(dry_backend, monkeypatch)
        with rm_module.Mocker():  # no POST registered → a real call would 404
            result = CliRunner().invoke(
                cli, ["--json", "--dry-run", "fieldsec", "create-profile", "Comp"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["_dry_run"] is True
        assert env["meta"]["dry_run"] is True


class TestAddPermission:
    def test_maps_grants(self, backend, monkeypatch):
        _use_backend(backend, monkeypatch)
        with rm_module.Mocker() as m:
            m.post(_perms_url(backend), status_code=204,
                   headers=_entity_id_headers(backend, "fieldpermissions", _NEW_PERM_ID))
            result = CliRunner().invoke(cli, [
                "--json", "fieldsec", "add-permission", _PROFILE_ID,
                "account", "creditlimit", "--read", "--update"])
        assert result.exit_code == 0, result.output
        body = m.last_request.json()
        assert body["canread"] == 4 and body["canupdate"] == 4 and body["cancreate"] == 0
        data = json.loads(result.output)["data"]
        assert data["fieldpermissionid"] == _NEW_PERM_ID

    def test_no_grant_is_error_envelope(self, backend, monkeypatch):
        _use_backend(backend, monkeypatch)
        with rm_module.Mocker():
            result = CliRunner().invoke(cli, [
                "--json", "fieldsec", "add-permission", _PROFILE_ID,
                "account", "creditlimit"])
        assert result.exit_code == 1
        assert json.loads(result.output)["ok"] is False


class TestAssign:
    def test_assign_user(self, backend, monkeypatch):
        _use_backend(backend, monkeypatch)
        ref_url = backend.url_for(
            f"fieldsecurityprofiles({_PROFILE_ID})/systemuserprofiles_association/$ref")
        with rm_module.Mocker() as m:
            m.post(ref_url, status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "fieldsec", "assign", _PROFILE_ID, "--user", _USER_ID])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["assigned"] is True and data["principal_type"] == "user"

    def test_both_user_and_team_is_usage_error(self, backend, monkeypatch):
        _use_backend(backend, monkeypatch)
        result = CliRunner().invoke(cli, [
            "--json", "fieldsec", "assign", _PROFILE_ID,
            "--user", _USER_ID, "--team", "x"])
        assert result.exit_code != 0
