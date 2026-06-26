"""Command-layer tests for `crm form` (list / clone / export)."""
# pyright: basic
from __future__ import annotations

import json

import pytest
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


def _has_clause(url: str, clause: str) -> bool:
    """Whether ``url`` contains an OData clause, tolerating either space encoding
    (``+`` or ``%20``) — the encoder choice is not part of the behavior."""
    return clause.replace(" ", "+") in url or clause.replace(" ", "%20") in url


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

    def test_list_default_restricts_to_main(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": []})
            CliRunner().invoke(cli, ["form", "list", "new_project"])
        assert _has_clause(m.last_request.url, "type eq 2")

    def test_list_type_filters_to_that_type(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": []})
            CliRunner().invoke(cli, ["form", "list", "new_project", "--type", "quickcreate"])
        assert _has_clause(m.last_request.url, "type eq 7")
        assert not _has_clause(m.last_request.url, "type eq 2")

    def test_list_type_is_repeatable(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": []})
            CliRunner().invoke(
                cli, ["form", "list", "new_project",
                      "--type", "main", "--type", "card"])
        url = m.last_request.url
        assert _has_clause(url, "type eq 2") and _has_clause(url, "type eq 11")

    def test_list_all_omits_the_type_filter(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": []})
            CliRunner().invoke(cli, ["form", "list", "new_project", "--all"])
        url = m.last_request.url
        assert "objecttypecode" in url and not _has_clause(url, "type eq")

    def test_list_type_is_case_insensitive(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": []})
            CliRunner().invoke(cli, ["form", "list", "new_project", "--type", "QuickCreate"])
        assert _has_clause(m.last_request.url, "type eq 7")

    def test_list_type_and_all_are_mutually_exclusive(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        result = CliRunner().invoke(
            cli, ["--json", "form", "list", "new_project", "--all", "--type", "main"])
        # House rule: mutually-exclusive flags raise click.UsageError → exit 2.
        assert result.exit_code == 2, result.output
        assert "mutually exclusive" in result.output.lower()


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

    def test_export_json_no_output_emits_envelope(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_A]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "export", "new_project", "Information",
            ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["data"]["entity"] == "new_project"
        assert data["data"]["form"] == "Information"
        assert data["data"]["formxml"] == _FORM_A["formxml"]

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


# ---------------------------------------------------------------------------
# crm form add-field / remove-field / set-field  (#326)
# ---------------------------------------------------------------------------

# A form with a real (tab/section/rows) layout so the field transforms have
# somewhere to splice. Carries one bound field (new_name).
_LAYOUT_XML = (
    '<form><tabs>'
    '<tab name="general" id="{aaaa1111-0000-0000-0000-000000000001}">'
    '<columns><column width="100%"><sections>'
    '<section name="summary" id="{bbbb2222-0000-0000-0000-000000000002}">'
    '<rows><row><cell id="{cccc3333-0000-0000-0000-000000000003}">'
    '<labels><label description="Name" languagecode="1033" /></labels>'
    '<control id="new_name" classid="{4273EDBD-AC1D-40D3-9FB2-095C621B552D}" '
    'datafieldname="new_name" /></cell></row></rows>'
    '</section></sections></column></columns></tab>'
    '<tab name="details" id="{dddd4444-0000-0000-0000-000000000004}">'
    '<columns><column width="100%"><sections>'
    '<section name="extra" id="{eeee5555-0000-0000-0000-000000000005}">'
    '<rows></rows></section></sections></column></columns></tab>'
    '</tabs></form>'
)
_FORM_LAYOUT = {
    "formid": "aaaaaaaa-0000-0000-0000-000000000001",
    "name": "Information", "objecttypecode": "new_project", "type": 2,
    "formxml": _LAYOUT_XML, "description": "Main", "isdefault": True,
}


def _attr_url(backend, entity, attr):
    return backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Attributes(LogicalName='{attr}')")


def _form_pk_url(backend):
    return backend.url_for("systemforms(aaaaaaaa-0000-0000-0000-000000000001)")


class TestFormAddField:
    def test_add_field_patches_with_resolved_classid(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_attr_url(backend, "new_project", "new_owner"), json={
                "AttributeType": "Lookup",
                "DisplayName": {"UserLocalizedLabel": {"Label": "Owner"}}})
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "add-field", "new_project", "new_owner",
                "--no-publish"])
        assert result.exit_code == 0, result.output
        body = m.last_request.json()
        assert 'datafieldname="new_owner"' in body["formxml"]
        assert "{270BD3DB-D9AF-4782-9025-509E298DEC0A}" in body["formxml"]
        data = json.loads(result.output)
        assert data["data"]["updated"] is True
        assert data["data"]["classid"] == "{270BD3DB-D9AF-4782-9025-509E298DEC0A}"

    def test_add_field_dry_run_does_not_write(self, dry_backend, monkeypatch):
        backend = dry_backend
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_attr_url(backend, "new_project", "new_owner"), json={
                "AttributeType": "Lookup",
                "DisplayName": {"UserLocalizedLabel": {"Label": "Owner"}}})
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            patched = m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "--dry-run", "form", "add-field",
                "new_project", "new_owner"])
        assert result.exit_code == 0, result.output
        assert patched.call_count == 0  # no write under dry-run
        data = json.loads(result.output)
        assert data["data"]["would_add"] is True

    def test_add_field_unmapped_type_errors(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_attr_url(backend, "new_project", "new_tags"), json={
                "AttributeType": "MultiSelectPicklist",
                "DisplayName": {"UserLocalizedLabel": {"Label": "Tags"}}})
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "add-field", "new_project", "new_tags",
                "--no-publish"])
        assert result.exit_code != 0
        assert "MultiSelectPicklist" in result.output


class TestFormRemoveField:
    def test_remove_field_patches_without_field(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "remove-field", "new_project", "new_name",
                "--no-publish"])
        assert result.exit_code == 0, result.output
        body = m.last_request.json()
        assert 'datafieldname="new_name"' not in body["formxml"]

    def test_remove_absent_field_errors(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "remove-field", "new_project", "nope",
                "--no-publish"])
        assert result.exit_code != 0


class TestFormSetField:
    def test_set_field_moves_to_target_section(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "set-field", "new_project", "new_name",
                "--tab", "details", "--section", "extra", "--no-publish"])
        assert result.exit_code == 0, result.output
        body = m.last_request.json()
        assert body["formxml"].index('name="details"') < body["formxml"].index("new_name")

    def test_set_absent_field_suggests_add(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "set-field", "new_project", "nope",
                "--tab", "details", "--no-publish"])
        assert result.exit_code != 0
        assert "add-field" in result.output


class TestFormSetFieldProps:
    def test_disabled_patches_control_attribute(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "set-field-props", "new_project", "new_name",
                "--disabled", "--no-publish"])
        assert result.exit_code == 0, result.output
        body = m.last_request.json()
        assert 'datafieldname="new_name"' in body["formxml"]
        assert 'disabled="true"' in body["formxml"]
        data = json.loads(result.output)["data"]
        assert data["updated"] is True
        assert data["disabled"] is True

    def test_multiple_toggles_in_one_call(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "set-field-props", "new_project", "new_name",
                "--locked", "--hidden", "--no-show-label", "--no-publish"])
        assert result.exit_code == 0, result.output
        xml = m.last_request.json()["formxml"]
        assert 'locklevel="1"' in xml
        assert 'visible="false"' in xml
        assert 'showlabel="false"' in xml

    def test_dry_run_does_not_write(self, dry_backend, monkeypatch):
        backend = dry_backend
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            patched = m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "--dry-run", "form", "set-field-props",
                "new_project", "new_name", "--disabled"])
        assert result.exit_code == 0, result.output
        assert patched.call_count == 0
        data = json.loads(result.output)["data"]
        assert data["would_update"] is True

    def test_no_props_is_error(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "set-field-props", "new_project", "new_name",
                "--no-publish"])
        assert result.exit_code != 0
        assert "at least one" in result.output.lower()

    def test_required_routes_to_attribute_metadata(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            patched = m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "set-field-props", "new_project", "new_name",
                "--required", "ApplicationRequired", "--no-publish"])
        assert result.exit_code != 0
        assert patched.call_count == 0  # never touches the form layer
        # Routes the user to the attribute-metadata command, not a silent no-op.
        assert "update-attribute" in result.output

    def test_absent_field_suggests_add(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "set-field-props", "new_project", "nope",
                "--disabled", "--no-publish"])
        assert result.exit_code != 0
        assert "add-field" in result.output


class TestFormFieldFormSelection:
    def test_ambiguous_forms_require_form_flag(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        second = dict(_FORM_LAYOUT, formid="ffffffff-0000-0000-0000-000000000099",
                      name="Information 2")
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT, second]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "remove-field", "new_project", "new_name",
                "--no-publish"])
        assert result.exit_code != 0
        assert "--form" in result.output


# --- event-handler & library wiring (issue #459) --------------------------------

def _webresource_url(backend):
    return backend.url_for("webresourceset")


_WR_OK = {"value": [{"webresourceid": "99990000-0000-0000-0000-000000000001",
                     "name": "new_lib.js", "webresourcetype": 3}]}


class TestFormAddLibrary:
    def test_registers_library(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_webresource_url(backend), json=_WR_OK)
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "add-library", "new_project",
                "--library", "new_lib.js", "--no-publish"])
        assert result.exit_code == 0, result.output
        body = m.last_request.json()
        assert '<Library name="new_lib.js"' in body["formxml"]
        assert "libraryUniqueId" in body["formxml"]

    def test_missing_web_resource_errors(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_webresource_url(backend), json={"value": []})
            result = CliRunner().invoke(cli, [
                "--json", "form", "add-library", "new_project",
                "--library", "nope.js", "--no-publish"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_dry_run_does_not_write(self, dry_backend, monkeypatch):
        backend = dry_backend
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_webresource_url(backend), json=_WR_OK)
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            patched = m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "--dry-run", "form", "add-library", "new_project",
                "--library", "new_lib.js"])
        assert result.exit_code == 0, result.output
        assert patched.call_count == 0
        assert json.loads(result.output)["data"]["would_add_library"] is True


class TestFormAddHandler:
    def test_wires_onload_handler(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_webresource_url(backend), json=_WR_OK)
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "add-handler", "new_project",
                "--event", "onload", "--library", "new_lib.js",
                "--function", "App.onLoad", "--no-publish"])
        assert result.exit_code == 0, result.output
        body = m.last_request.json()["formxml"]
        assert '<event name="onload"' in body
        assert "<Handlers>" in body and "InternalHandlers" not in body
        assert 'functionName="App.onLoad"' in body
        assert '<Library name="new_lib.js"' in body

    def test_onchange_requires_field(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_webresource_url(backend), json=_WR_OK)
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "add-handler", "new_project",
                "--event", "onchange", "--library", "new_lib.js",
                "--function", "App.c", "--no-publish"])
        assert result.exit_code != 0
        assert "onchange" in result.output

    def test_onchange_field_must_be_on_form(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_webresource_url(backend), json=_WR_OK)
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "add-handler", "new_project",
                "--event", "onchange", "--field", "ghost",
                "--library", "new_lib.js", "--function", "App.c", "--no-publish"])
        assert result.exit_code != 0
        assert "not on the form" in result.output

    def test_missing_web_resource_errors(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_webresource_url(backend), json={"value": []})
            result = CliRunner().invoke(cli, [
                "--json", "form", "add-handler", "new_project",
                "--event", "onload", "--library", "nope.js",
                "--function", "App.x", "--no-publish"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_dry_run_does_not_write(self, dry_backend, monkeypatch):
        backend = dry_backend
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_webresource_url(backend), json=_WR_OK)
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            patched = m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "--dry-run", "form", "add-handler", "new_project",
                "--event", "onload", "--library", "new_lib.js",
                "--function", "App.onLoad"])
        assert result.exit_code == 0, result.output
        assert patched.call_count == 0
        assert json.loads(result.output)["data"]["would_add_handler"] is True


_LAYOUT_WITH_HANDLER = (
    _LAYOUT_XML.replace(
        "</tabs></form>",
        '</tabs><events><event name="onload"><Handlers>'
        '<Handler functionName="App.onLoad" libraryName="new_lib.js" '
        'handlerUniqueId="{12345678-0000-0000-0000-000000000001}" '
        'enabled="true" passExecutionContext="true" /></Handlers></event></events>'
        '<formLibraries><Library name="new_lib.js" '
        'libraryUniqueId="{22345678-0000-0000-0000-000000000002}" />'
        '</formLibraries></form>'))
_FORM_WITH_HANDLER = dict(_FORM_LAYOUT, formxml=_LAYOUT_WITH_HANDLER)


class TestFormRemoveHandler:
    def test_removes_handler(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_WITH_HANDLER]})
            m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "remove-handler", "new_project",
                "--event", "onload", "--function", "App.onLoad", "--no-publish"])
        assert result.exit_code == 0, result.output
        assert 'functionName="App.onLoad"' not in m.last_request.json()["formxml"]

    def test_absent_handler_errors(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "remove-handler", "new_project",
                "--event", "onload", "--function", "App.nope", "--no-publish"])
        assert result.exit_code != 0


class TestFormListHandlers:
    def test_lists_wired_handler(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_WITH_HANDLER]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "list-handlers", "new_project"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        handlers = env["data"]  # ADR 0008: bare array in data
        assert len(handlers) == 1
        assert handlers[0]["function"] == "App.onLoad"
        assert handlers[0]["library"] == "new_lib.js"
        assert env["meta"]["form"] == "Information"

    def test_empty_when_none(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            result = CliRunner().invoke(cli, [
                "--json", "form", "list-handlers", "new_project"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"] == []


# ---------------------------------------------------------------------------
# crm form add/remove/rename/move-tab and -section  (#460)
# ---------------------------------------------------------------------------


class TestFormTabCommands:
    def test_add_tab_patches_with_new_tab(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "add-tab", "new_project", "new_tab",
                "--label", "New Tab", "--no-publish"])
        assert result.exit_code == 0, result.output
        body = m.last_request.json()
        assert 'name="new_tab"' in body["formxml"]
        assert 'IsUserDefined="1"' in body["formxml"]
        assert json.loads(result.output)["data"]["updated"] is True

    def test_remove_tab_refuses_orphaning_without_force(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            patched = m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "remove-tab", "new_project", "general",
                "--no-publish"])
        assert result.exit_code != 0
        assert "new_name" in result.output  # the orphaned field is named
        assert patched.call_count == 0  # no write on refusal

    def test_remove_tab_force_surfaces_orphans(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "remove-tab", "new_project", "general",
                "--force", "--no-publish"])
        assert result.exit_code == 0, result.output
        body = m.last_request.json()
        assert 'name="general"' not in body["formxml"]
        assert json.loads(result.output)["data"]["orphaned"] == ["new_name"]

    def test_remove_only_tab_refused(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        one_tab = dict(_FORM_LAYOUT, formxml=(
            '<form><tabs><tab name="solo" id="{aaaa1111-0000-0000-0000-'
            '000000000001}"><columns><column><sections><section name="s" '
            'id="{bbbb2222-0000-0000-0000-000000000002}"><rows/></section>'
            '</sections></column></columns></tab></tabs></form>'))
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [one_tab]})
            patched = m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "remove-tab", "new_project", "solo",
                "--no-publish"])
        assert result.exit_code != 0
        assert "only tab" in result.output
        assert patched.call_count == 0

    def test_rename_tab_sets_label(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "rename-tab", "new_project", "general",
                "--label", "Overview", "--no-publish"])
        assert result.exit_code == 0, result.output
        assert 'description="Overview"' in m.last_request.json()["formxml"]

    def test_columns_out_of_range_rejected(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            patched = m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "add-tab", "new_project", "t",
                "--columns", "7", "--no-publish"])
        assert result.exit_code != 0
        assert patched.call_count == 0


class TestFormSectionCommands:
    def test_add_section_patches_into_tab(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "add-section", "new_project", "new_sec",
                "--tab", "details", "--no-publish"])
        assert result.exit_code == 0, result.output
        assert 'name="new_sec"' in m.last_request.json()["formxml"]

    def test_remove_section_refuses_orphaning_without_force(self, backend, monkeypatch):
        monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)
        with rm_module.Mocker() as m:
            m.get(_forms_url(backend), json={"value": [_FORM_LAYOUT]})
            patched = m.patch(_form_pk_url(backend), status_code=204)
            result = CliRunner().invoke(cli, [
                "--json", "form", "remove-section", "new_project", "summary",
                "--tab", "general", "--no-publish"])
        assert result.exit_code != 0
        assert "new_name" in result.output
        assert patched.call_count == 0


# Each new verb honors --dry-run: the form GET runs for real, but no PATCH/write
# fires and the response carries the would_* flag (issue #460 AC: "--dry-run
# (would_*, zero HTTP)" — i.e. zero *write* traffic; the read still happens).
_DRY_RUN_CASES = [
    (["form", "add-tab", "new_project", "new_tab"], "would_add"),
    (["form", "remove-tab", "new_project", "details"], "would_remove"),
    (["form", "rename-tab", "new_project", "general", "--label", "X"],
     "would_rename"),
    (["form", "move-tab", "new_project", "details"], "would_move"),
    (["form", "add-section", "new_project", "new_sec", "--tab", "details"],
     "would_add"),
    (["form", "remove-section", "new_project", "extra", "--tab", "details"],
     "would_remove"),
    (["form", "rename-section", "new_project", "summary", "--tab", "general",
      "--label", "X"], "would_rename"),
    (["form", "move-section", "new_project", "extra", "--tab", "details"],
     "would_move"),
]


@pytest.mark.parametrize("args,flag", _DRY_RUN_CASES)
def test_tab_section_verb_dry_run_does_not_write(dry_backend, monkeypatch, args, flag):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: dry_backend)
    with rm_module.Mocker() as m:
        m.get(_forms_url(dry_backend), json={"value": [_FORM_LAYOUT]})
        patched = m.patch(_form_pk_url(dry_backend), status_code=204)
        result = CliRunner().invoke(cli, ["--json", "--dry-run", *args])
    assert result.exit_code == 0, result.output
    assert patched.call_count == 0  # zero HTTP write under dry-run
    assert json.loads(result.output)["data"][flag] is True
