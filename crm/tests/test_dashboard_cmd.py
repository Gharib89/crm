"""Command-layer tests for `crm dashboard` (list / get / create / delete)."""
# pyright: basic
from __future__ import annotations

import json

import requests_mock as rm_module

from click.testing import CliRunner
from crm.cli import cli
from crm.utils.d365_backend import D365Backend

_DASH = {
    "formid": "11112222-3333-4444-5555-666677778888",
    "name": "Sales Overview",
    "objecttypecode": "none",
    "description": "Org sales dashboard",
    "isdefault": False,
    "formxml": "<form><tabs/></form>",
}
_NEW_ID = "99998888-7777-6666-5555-444433332222"


def _forms_url(backend: D365Backend) -> str:
    return backend.url_for("systemforms")


def _use_backend(monkeypatch, backend):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)


class TestDashboardList:
    def test_list(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_DASH]})
            result = CliRunner().invoke(cli, ["--json", "dashboard", "list"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        row = env["data"][0]
        assert row["formid"] == _DASH["formid"]
        # list returns list columns only — formxml is fetched via `dashboard get`
        assert "formxml" not in row


class TestDashboardGet:
    def test_get_includes_formxml(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        did = _DASH["formid"]
        with rm_module.Mocker() as m:
            m.get(backend.url_for(f"systemforms({did})"), json=_DASH)
            result = CliRunner().invoke(cli, ["--json", "dashboard", "get", did])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["formxml"] == "<form><tabs/></form>"


class TestDashboardDelete:
    def test_delete(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        did = _DASH["formid"]
        with rm_module.Mocker() as m:
            m.delete(backend.url_for(f"systemforms({did})"), status_code=204)
            result = CliRunner().invoke(cli, ["--json", "dashboard", "delete", did])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"] == {"deleted": True, "formid": did}


class TestDashboardCreate:
    def _post_mock(self, m, backend):
        m.post(_forms_url(backend), status_code=204,
               headers={"OData-EntityId": backend.url_for(f"systemforms({_NEW_ID})")})

    def _formxml_file(self, tmp_path):
        f = tmp_path / "dash.xml"
        f.write_text("<form><tabs/></form>", encoding="utf-8")
        return str(f)

    def test_create_from_formxml(self, backend, monkeypatch, tmp_path):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            self._post_mock(m, backend)
            result = CliRunner().invoke(cli, [
                "--json", "dashboard", "create",
                "--name", "Sales", "--formxml", self._formxml_file(tmp_path),
                "--no-publish"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["created"] is True
        assert env["data"]["formid"] == _NEW_ID

    def test_create_rejects_interactive(self, backend, monkeypatch, tmp_path):
        _use_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "--json", "dashboard", "create",
            "--name", "X", "--formxml", self._formxml_file(tmp_path),
            "--interactive", "--no-publish"])
        assert result.exit_code != 0
        assert "type-10" in result.output or "interactive" in result.output.lower()

    def test_create_requires_formxml(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "--json", "dashboard", "create", "--name", "X", "--no-publish"])
        assert result.exit_code != 0

    def test_create_dry_run_previews(self, dry_backend, monkeypatch, tmp_path):
        _use_backend(monkeypatch, dry_backend)
        result = CliRunner().invoke(cli, [
            "--json", "dashboard", "create",
            "--name", "Sales", "--formxml", self._formxml_file(tmp_path),
            "--no-publish"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["_dry_run"] is True
        assert env["data"]["would_create"]["entity_set"] == "systemforms"
