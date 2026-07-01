"""CLI-layer tests for `crm connectionrole`."""
# pyright: basic
from __future__ import annotations

import json

import requests_mock as rm_module
from click.testing import CliRunner

from crm.cli import cli

_ROLE_A = "11112222-3333-4444-5555-666677778888"
_ROLE_B = "99990000-1111-2222-3333-444455556666"
_OTC_ID = "aaaa1111-2222-3333-4444-555566667777"


def _use_backend(monkeypatch, backend):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)


def _entity_id_headers(backend, entity_set, rec_id):
    return {"OData-EntityId": backend.url_for(f"{entity_set}({rec_id})")}


class TestCreate:
    def test_create_role(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.post(backend.url_for("connectionroles"), status_code=204,
                   headers=_entity_id_headers(backend, "connectionroles", _ROLE_A))
            result = CliRunner().invoke(cli, [
                "--json", "connectionrole", "create",
                "--name", "Stakeholder", "--category", "stakeholder",
                "--solution", "TestSol"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["created"] is True
        assert data["connectionroleid"] == _ROLE_A
        assert m.last_request.json()["category"] == 1000

    def test_invalid_category_is_usage_error(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "--json", "connectionrole", "create", "--name", "X", "--category", "bogus"])
        # click.Choice rejects an invalid value at parse time (exit 2).
        assert result.exit_code == 2, result.output

    def test_dry_run(self, dry_backend, monkeypatch):
        _use_backend(monkeypatch, dry_backend)
        with rm_module.Mocker():
            result = CliRunner().invoke(cli, [
                "--json", "--dry-run", "connectionrole", "create", "--name", "X",
                "--solution", "TestSol"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"]["_dry_run"] is True


class TestScope:
    def test_scope_role_to_entity(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.post(backend.url_for("connectionroleobjecttypecodes"), status_code=204,
                   headers=_entity_id_headers(
                       backend, "connectionroleobjecttypecodes", _OTC_ID))
            result = CliRunner().invoke(cli, [
                "--json", "connectionrole", "scope", _ROLE_A, "--entity", "account",
                "--solution", "TestSol"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["created"] is True
        assert m.last_request.json()["associatedobjecttypecode"] == "account"

    def test_dry_run(self, dry_backend, monkeypatch):
        _use_backend(monkeypatch, dry_backend)
        with rm_module.Mocker():
            result = CliRunner().invoke(cli, [
                "--json", "--dry-run", "connectionrole", "scope", _ROLE_A,
                "--entity", "account", "--solution", "TestSol"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"]["_dry_run"] is True


class TestMatch:
    def test_match_roles(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        ref_url = backend.url_for(
            f"connectionroles({_ROLE_A})/connectionroleassociation_association/$ref")
        with rm_module.Mocker() as m:
            m.post(ref_url, status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "connectionrole", "match", _ROLE_A, _ROLE_B])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["matched"] is True
        assert data["role_b"] == _ROLE_B

    def test_dry_run(self, dry_backend, monkeypatch):
        _use_backend(monkeypatch, dry_backend)
        with rm_module.Mocker():
            result = CliRunner().invoke(cli, [
                "--json", "--dry-run", "connectionrole", "match", _ROLE_A, _ROLE_B])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"]["_dry_run"] is True
