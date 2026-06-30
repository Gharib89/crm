"""Command-layer tests for `crm report` (list / get / create / set-category / delete)."""
# pyright: basic
from __future__ import annotations

import json

import requests_mock as rm_module

from click.testing import CliRunner
from crm.cli import cli
from crm.utils.d365_backend import D365Backend

_REPORT = {
    "reportid": "11112222-3333-4444-5555-666677778888",
    "name": "Quarterly Sales",
    "filename": "sales.rdl",
    "reporttypecode": 1,
    "ispersonal": True,
    "description": "Q sales",
    "bodyurl": None,
    "bodytext": "<Report/>",
}
_NEW_ID = "99998888-7777-6666-5555-444433332222"
_RC_ID = "aaaabbbb-cccc-dddd-eeee-ffff00001111"


def _reports_url(backend: D365Backend) -> str:
    return backend.url_for("reports")


def _use_backend(monkeypatch, backend):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)


class TestReportList:
    def test_list(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.get(_reports_url(backend), json={"value": [_REPORT]})
            result = CliRunner().invoke(cli, ["--json", "report", "list"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        row = env["data"][0]
        assert row["reportid"] == _REPORT["reportid"]
        assert "bodytext" not in row


class TestReportGet:
    def test_get_includes_body(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        rid = _REPORT["reportid"]
        with rm_module.Mocker() as m:
            m.get(backend.url_for(f"reports({rid})"), json=_REPORT)
            result = CliRunner().invoke(cli, ["--json", "report", "get", rid])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["bodytext"] == "<Report/>"


class TestReportCreate:
    def _post_mock(self, m, backend):
        m.post(_reports_url(backend), status_code=204,
               headers={"OData-EntityId": backend.url_for(f"reports({_NEW_ID})")})

    def _rdl_file(self, tmp_path):
        f = tmp_path / "sales.rdl"
        f.write_text("<Report/>", encoding="utf-8")
        return str(f)

    def test_create_from_rdl(self, backend, monkeypatch, tmp_path):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            self._post_mock(m, backend)
            result = CliRunner().invoke(cli, [
                "--json", "report", "create",
                "--name", "Sales", "--body-file", self._rdl_file(tmp_path),
                "--solution", "MySol"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["created"] is True
        assert env["data"]["reportid"] == _NEW_ID
        # filename defaults to the RDL basename
        assert m.last_request.json()["filename"] == "sales.rdl"

    def test_create_from_url_with_org(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            self._post_mock(m, backend)
            result = CliRunner().invoke(cli, [
                "--json", "report", "create",
                "--name", "Link", "--url", "https://example.com/r", "--org",
                "--solution", "MySol"])
        assert result.exit_code == 0, result.output
        body = m.last_request.json()
        assert body["bodyurl"] == "https://example.com/r"
        assert body["ispersonal"] is False

    def test_create_rejects_both_sources(self, backend, monkeypatch, tmp_path):
        _use_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "--json", "report", "create", "--name", "X",
            "--body-file", self._rdl_file(tmp_path), "--url", "https://e.com/r"])
        assert result.exit_code != 0

    def test_create_requires_a_source(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "--json", "report", "create", "--name", "X"])
        assert result.exit_code != 0

    def test_create_dry_run_previews(self, dry_backend, monkeypatch):
        _use_backend(monkeypatch, dry_backend)
        result = CliRunner().invoke(cli, [
            "--json", "report", "create",
            "--name", "Link", "--url", "https://example.com/r",
            "--solution", "MySol"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["_dry_run"] is True
        assert env["data"]["would_create"]["entity_set"] == "reports"


class TestReportSetCategory:
    def test_set_category(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        rid = _REPORT["reportid"]
        with rm_module.Mocker() as m:
            m.post(backend.url_for("reportcategories"), status_code=204,
                   headers={"OData-EntityId":
                            backend.url_for(f"reportcategories({_RC_ID})")})
            result = CliRunner().invoke(cli, [
                "--json", "report", "set-category", rid, "--category", "sales",
                "--solution", "MySol"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["categorycode"] == 1
        assert m.last_request.json()["reportid@odata.bind"] == f"/reports({rid})"

    def test_rejects_bad_category(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        rid = _REPORT["reportid"]
        result = CliRunner().invoke(cli, [
            "--json", "report", "set-category", rid, "--category", "finance"])
        # click.Choice rejects an unlisted area at parse time
        assert result.exit_code != 0


class TestReportDelete:
    def test_delete(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        rid = _REPORT["reportid"]
        with rm_module.Mocker() as m:
            m.delete(backend.url_for(f"reports({rid})"), status_code=204)
            result = CliRunner().invoke(cli, ["--json", "report", "delete", rid])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"] == {"deleted": True, "reportid": rid}
