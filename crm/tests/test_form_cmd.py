"""Command-layer tests for `crm form` (list / clone / export)."""
# pyright: basic
from __future__ import annotations

import json

import requests_mock as rm_module

from click.testing import CliRunner
from crm.cli import cli
from crm.utils.d365_backend import D365Backend


# Form rows used across tests
_FORM_A = {
    "formid": "aaaaaaaa-0000-0000-0000-000000000001",
    "name": "Information",
    "objecttypecode": "new_project",
    "type": 2,
    "formxml": "<form><control entityname=\"new_project\" /></form>",
    "description": "Main form",
    "isdefault": True,
}

_FORM_B = {
    "formid": "bbbbbbbb-0000-0000-0000-000000000002",
    "name": "Quick View",
    "objecttypecode": "new_project",
    "type": 2,
    "formxml": "<form/>",
    "description": None,
    "isdefault": False,
}

# Second form with same name as _FORM_A — used to test ambiguous resolution
_FORM_A_DUP = {
    "formid": "cccccccc-0000-0000-0000-000000000003",
    "name": "Information",
    "objecttypecode": "new_project",
    "type": 2,
    "formxml": "<form/>",
    "description": None,
    "isdefault": False,
}

_CLONE_ENTITY_ID_URL = (
    "https://crm.contoso.local/contoso/api/data/v9.2/"
    "systemforms(dddddddd-1111-2222-3333-444444444444)"
)


def _forms_url(backend: D365Backend) -> str:
    return backend.url_for("systemforms")


# ---------------------------------------------------------------------------
# crm form list
# ---------------------------------------------------------------------------

class TestFormList:
    def test_list_renders_form_names(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_A, _FORM_B]})
            result = CliRunner().invoke(cli, ["--json", "form", "list", "new_project"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        names = [f["name"] for f in data["data"]]
        assert "Information" in names
        assert "Quick View" in names

    def test_list_renders_table_in_human_mode(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_A]})
            result = CliRunner().invoke(cli, ["form", "list", "new_project"])
        assert result.exit_code == 0, result.output
        assert "Information" in result.output

    def test_list_filters_to_queried_entity(self, backend, monkeypatch):
        """The GET request URL must include the entity logical name in the filter."""
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": []})
            CliRunner().invoke(cli, ["form", "list", "new_project"])
        assert "new_project" in m.last_request.url

    def test_list_empty_exits_ok(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": []})
            result = CliRunner().invoke(cli, ["--json", "form", "list", "new_project"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"] == []


# ---------------------------------------------------------------------------
# crm form clone
# ---------------------------------------------------------------------------

class TestFormClone:
    def test_clone_posts_retargeted_form(self, backend, monkeypatch):
        """Clone POSTs with target objecttypecode and retargeted formxml."""
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_A]})
            m.post(_forms_url(backend), status_code=204,
                   headers={"OData-EntityId": _CLONE_ENTITY_ID_URL})
            result = CliRunner().invoke(cli, [
                "--json", "form", "clone", "new_project", "Information",
                "--to", "cwx_ticketclone", "--no-publish",
            ])
        assert result.exit_code == 0, result.output
        body = m.last_request.json()
        assert body["objecttypecode"] == "cwx_ticketclone"
        assert 'entityname="cwx_ticketclone"' in body["formxml"]
        assert body["name"] == "Information"
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["created"] is True

    def test_clone_passes_solution_header(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_A]})
            m.post(_forms_url(backend), status_code=204,
                   headers={"OData-EntityId": _CLONE_ENTITY_ID_URL})
            result = CliRunner().invoke(cli, [
                "--json", "form", "clone", "new_project", "Information",
                "--to", "cwx_ticketclone", "--solution", "MySol", "--no-publish",
            ])
        assert result.exit_code == 0, result.output
        post_req = next(r for r in m.request_history if r.method == "POST")
        assert post_req.headers.get("MSCRM.SolutionUniqueName") == "MySol"

    def test_clone_no_publish_skips_publish_call(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_A]})
            m.post(_forms_url(backend), status_code=204,
                   headers={"OData-EntityId": _CLONE_ENTITY_ID_URL})
            result = CliRunner().invoke(cli, [
                "form", "clone", "new_project", "Information",
                "--to", "cwx_ticketclone", "--no-publish",
            ])
        assert result.exit_code == 0, result.output
        post_urls = [r.url for r in m.request_history if r.method == "POST"]
        assert not any("PublishAllXml" in u for u in post_urls)

    def test_clone_dry_run_resolves_source_form(self, profile, monkeypatch):
        """--dry-run must force a real GET to resolve the source form, then
        preview the POST. Regression: a dry-run backend's request returns a
        preview dict with no 'value', so the read would otherwise yield zero
        forms and the command would falsely error 'No form named ...'."""
        dry_backend = D365Backend(profile, password="pw", dry_run=True)
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: dry_backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(dry_backend), json={"value": [_FORM_A]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "clone", "new_project", "Information",
                "--to", "cwx_ticketclone", "--no-publish",
            ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        # No real POST issued under dry-run
        assert not any(r.method == "POST" for r in m.request_history)
        # dry_run stays set throughout — reads execute, only the POST is previewed
        assert dry_backend.dry_run is True

    def test_clone_unknown_form_errors_no_post(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_A, _FORM_B]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "clone", "new_project", "NoSuchForm",
                "--to", "cwx_ticketclone",
            ])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "NoSuchForm" in data["error"]
        assert not any(r.method == "POST" for r in m.request_history)

    def test_clone_ambiguous_name_errors_no_post(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_A, _FORM_A_DUP]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "clone", "new_project", "Information",
                "--to", "cwx_ticketclone",
            ])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False
        # Error must list colliding formids so user can disambiguate
        assert _FORM_A["formid"] in data["error"]
        assert _FORM_A_DUP["formid"] in data["error"]
        assert not any(r.method == "POST" for r in m.request_history)


# ---------------------------------------------------------------------------
# crm form export
# ---------------------------------------------------------------------------

class TestFormExport:
    def test_export_prints_formxml_to_stdout(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_A]})
            result = CliRunner().invoke(cli, ["form", "export", "new_project", "Information"])
        assert result.exit_code == 0, result.output
        assert "<form>" in result.output

    def test_export_writes_to_file(self, backend, monkeypatch, tmp_path):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        out_file = tmp_path / "form.xml"
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_A]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "export", "new_project", "Information",
                "--output", str(out_file),
            ])
        assert result.exit_code == 0, result.output
        assert out_file.exists()
        assert "<form>" in out_file.read_text(encoding="utf-8")
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["entity"] == "new_project"
        assert data["data"]["form"] == "Information"

    def test_export_unknown_form_errors(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_A]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "export", "new_project", "NoSuchForm",
            ])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False
        assert "NoSuchForm" in data["error"]

    def test_export_ambiguous_name_errors(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_A, _FORM_A_DUP]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "export", "new_project", "Information",
            ])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["ok"] is False
        assert _FORM_A["formid"] in data["error"]
        assert _FORM_A_DUP["formid"] in data["error"]
