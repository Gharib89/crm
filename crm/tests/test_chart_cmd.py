"""Command-layer tests for `crm chart` (list / get / create / delete)."""
# pyright: basic
from __future__ import annotations

import json
import re

import requests_mock as rm_module

from click.testing import CliRunner
from crm.cli import cli
from crm.utils.d365_backend import D365Backend


_CHART = {
    "savedqueryvisualizationid": "11112222-3333-4444-5555-666677778888",
    "name": "Tickets by Priority",
    "primaryentitytypecode": "new_project",
    "datadescription": '<datadefinition><fetch><entity name="new_project"/></fetch></datadefinition>',
    "presentationdescription": "<Chart/>",
    "description": "By priority",
    "isdefault": False,
}
_NEW_ID = "99998888-7777-6666-5555-444433332222"


def _sys_url(backend: D365Backend) -> str:
    return backend.url_for("savedqueryvisualizations")


def _use_backend(monkeypatch, backend):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)


class TestChartList:
    def test_list_system_charts(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.get(_sys_url(backend), json={"value": [_CHART]})
            result = CliRunner().invoke(cli, ["--json", "chart", "list", "new_project"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        row = env["data"][0]
        assert row["savedqueryvisualizationid"] == _CHART["savedqueryvisualizationid"]
        # list returns list columns only — XML is fetched via `chart get`
        assert "datadescription" not in row
        assert "presentationdescription" not in row

    def test_list_user_charts_hits_userset(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.get(backend.url_for("userqueryvisualizations"), json={"value": []})
            result = CliRunner().invoke(
                cli, ["--json", "chart", "list", "new_project", "--user"])
        assert result.exit_code == 0, result.output
        assert "userqueryvisualizations" in m.last_request.url


class TestChartGet:
    def test_get_includes_xml(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        cid = _CHART["savedqueryvisualizationid"]
        with rm_module.Mocker() as m:
            m.get(backend.url_for(f"savedqueryvisualizations({cid})"), json=_CHART)
            result = CliRunner().invoke(cli, ["--json", "chart", "get", cid])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["presentationdescription"] == "<Chart/>"


class TestChartDelete:
    def test_delete_system_chart(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        cid = _CHART["savedqueryvisualizationid"]
        with rm_module.Mocker() as m:
            m.delete(backend.url_for(f"savedqueryvisualizations({cid})"), status_code=204)
            result = CliRunner().invoke(cli, ["--json", "chart", "delete", cid])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"] == {"deleted": True, "savedqueryvisualizationid": cid}


class TestReadFile:
    def test_unreadable_path_raises_usage_error(self, tmp_path):
        import click
        import pytest
        from crm.commands.chart import _read_file
        # A directory can't be read as a file → OSError → clean UsageError.
        with pytest.raises(click.UsageError):
            _read_file(str(tmp_path))


class TestChartCreate:
    def _post_mock(self, m, backend):
        m.post(_sys_url(backend), status_code=204,
               headers={"OData-EntityId": backend.url_for(
                   f"savedqueryvisualizations({_NEW_ID})")})

    def test_create_xml_mode(self, backend, monkeypatch, tmp_path):
        _use_backend(monkeypatch, backend)
        dd = tmp_path / "data.xml"
        dd.write_text("<datadefinition/>", encoding="utf-8")
        pd = tmp_path / "pres.xml"
        pd.write_text("<Chart/>", encoding="utf-8")
        with rm_module.Mocker() as m:
            self._post_mock(m, backend)
            result = CliRunner().invoke(cli, [
                "--json", "chart", "create", "new_project",
                "--name", "By Priority",
                "--data-description", str(dd),
                "--presentation-description", str(pd),
                "--solution", "MySol", "--no-publish",
            ])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["created"] is True
        assert env["data"]["savedqueryvisualizationid"] == _NEW_ID

    def test_create_requires_a_mode(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "--json", "chart", "create", "new_project", "--name", "X", "--no-publish"])
        assert result.exit_code != 0
        assert "either" in result.output.lower()

    def test_create_rejects_both_modes(self, backend, monkeypatch, tmp_path):
        _use_backend(monkeypatch, backend)
        dd = tmp_path / "data.xml"
        dd.write_text("<datadefinition/>", encoding="utf-8")
        pd = tmp_path / "pres.xml"
        pd.write_text("<Chart/>", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "--json", "chart", "create", "new_project", "--name", "X",
            "--data-description", str(dd), "--presentation-description", str(pd),
            "--web-resource", "new_chartscript", "--no-publish"])
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output.lower()

    def test_create_xml_mode_requires_both_files(self, backend, monkeypatch, tmp_path):
        _use_backend(monkeypatch, backend)
        dd = tmp_path / "data.xml"
        dd.write_text("<datadefinition/>", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "--json", "chart", "create", "new_project", "--name", "X",
            "--data-description", str(dd), "--no-publish"])
        assert result.exit_code != 0
        assert "both" in result.output.lower()

    def test_create_web_resource_mode(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        wr_id = "dddddddd-0000-0000-0000-000000000001"
        with rm_module.Mocker() as m:
            m.get(backend.url_for("webresourceset"),
                  json={"value": [{"webresourceid": wr_id, "name": "new_chartscript"}]})
            self._post_mock(m, backend)
            result = CliRunner().invoke(cli, [
                "--json", "chart", "create", "new_project", "--name", "Script",
                "--web-resource", "new_chartscript", "--solution", "MySol", "--no-publish"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["data"]["created"] is True

    def test_create_dry_run_previews(self, dry_backend, monkeypatch, tmp_path):
        # The dry_backend fixture is dry_run=True, so create_chart short-circuits
        # the POST and returns the {_dry_run, would_create} preview.
        _use_backend(monkeypatch, dry_backend)
        dd = tmp_path / "data.xml"
        dd.write_text("<datadefinition/>", encoding="utf-8")
        pd = tmp_path / "pres.xml"
        pd.write_text("<Chart/>", encoding="utf-8")
        result = CliRunner().invoke(cli, [
            "--json", "chart", "create", "new_project",
            "--name", "By Priority",
            "--data-description", str(dd), "--presentation-description", str(pd),
            "--solution", "MySol", "--no-publish"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["_dry_run"] is True
        assert env["data"]["would_create"]["entity_set"] == "savedqueryvisualizations"


_EDIT_DATA = (
    '<datadefinition><fetchcollection>'
    '<fetch mapping="logical" aggregate="true"><entity name="new_project">'
    '<attribute name="new_priority" groupby="true" alias="groupby_column" />'
    '<attribute name="new_projectid" aggregate="count" alias="aggregate_column" />'
    '</entity></fetch></fetchcollection>'
    '<categorycollection><category alias="groupby_column">'
    '<measurecollection><measure alias="aggregate_column" /></measurecollection>'
    '</category></categorycollection></datadefinition>'
)
_EDIT_PRES = ('<Chart><Series><Series ChartType="Column" /></Series>'
              '<ChartAreas><ChartArea /></ChartAreas></Chart>')
_EDIT_CHART = {
    "savedqueryvisualizationid": _CHART["savedqueryvisualizationid"],
    "name": "Projects by Priority",
    "primaryentitytypecode": "new_project",
    "datadescription": _EDIT_DATA,
    "presentationdescription": _EDIT_PRES,
    "description": None,
    "isdefault": False,
}
_EDIT_ID = _EDIT_CHART["savedqueryvisualizationid"]


def _edit_mocks(m, backend):
    """Register GET chart + GET metadata + PATCH for an editor command run."""
    url = backend.url_for(f"savedqueryvisualizations({_EDIT_ID})")
    m.get(url, json=_EDIT_CHART)
    m.get(re.compile("EntityDefinitions"), json={"AttributeType": "Money"})
    m.patch(url, status_code=204)


class TestChartUpdateCmd:
    def test_update_name_and_type(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            _edit_mocks(m, backend)
            result = CliRunner().invoke(cli, [
                "--json", "chart", "update", _EDIT_ID,
                "--name", "Renamed", "--type", "Bar", "--solution", "MySol", "--no-publish"])
        assert result.exit_code == 0, result.output
        body = m.last_request.json()
        assert body["name"] == "Renamed"
        assert 'ChartType="Bar"' in body["presentationdescription"]

    def test_update_data_from_file(self, backend, monkeypatch, tmp_path):
        _use_backend(monkeypatch, backend)
        dd = tmp_path / "data.xml"
        dd.write_text(_EDIT_DATA, encoding="utf-8")
        with rm_module.Mocker() as m:
            _edit_mocks(m, backend)
            result = CliRunner().invoke(cli, [
                "--json", "chart", "update", _EDIT_ID,
                "--data-description", str(dd), "--solution", "MySol", "--no-publish"])
        assert result.exit_code == 0, result.output
        assert m.last_request.json()["datadescription"] == _EDIT_DATA


class TestChartSetFetchCmd:
    def test_set_fetch_replaces_inner_fetch(self, backend, monkeypatch, tmp_path):
        _use_backend(monkeypatch, backend)
        fetch = tmp_path / "fetch.xml"
        fetch.write_text(
            '<fetch mapping="logical" aggregate="true"><entity name="new_project">'
            '<attribute name="new_stage" groupby="true" alias="groupby_column" />'
            '<attribute name="new_projectid" aggregate="count" alias="aggregate_column" />'
            '</entity></fetch>', encoding="utf-8")
        with rm_module.Mocker() as m:
            _edit_mocks(m, backend)
            result = CliRunner().invoke(cli, [
                "--json", "chart", "set-fetch", _EDIT_ID,
                "--fetch", str(fetch), "--solution", "MySol", "--no-publish"])
        assert result.exit_code == 0, result.output
        assert 'name="new_stage"' in m.last_request.json()["datadescription"]


class TestChartSeriesCmd:
    def test_add_series(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            _edit_mocks(m, backend)
            result = CliRunner().invoke(cli, [
                "--json", "chart", "add-series", _EDIT_ID,
                "--column", "new_budget", "--aggregate", "sum",
                "--alias", "series2", "--solution", "MySol", "--no-publish"])
        assert result.exit_code == 0, result.output
        assert 'alias="series2"' in m.last_request.json()["datadescription"]

    def test_remove_series_requires_known_alias(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            _edit_mocks(m, backend)
            result = CliRunner().invoke(cli, [
                "--json", "chart", "remove-series", _EDIT_ID,
                "--alias", "ghost", "--solution", "MySol", "--no-publish"])
        assert result.exit_code != 0
        assert "ghost" in result.output.lower()


class TestChartSetGroupbyCmd:
    def test_set_groupby_with_dategrouping(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        url = backend.url_for(f"savedqueryvisualizations({_EDIT_ID})")
        with rm_module.Mocker() as m:
            m.get(url, json=_EDIT_CHART)
            m.patch(url, status_code=204)
            # --dategrouping requires a date column
            m.get(re.compile("EntityDefinitions"), json={"AttributeType": "DateTime"})
            result = CliRunner().invoke(cli, [
                "--json", "chart", "set-groupby", _EDIT_ID,
                "--column", "createdon", "--dategrouping", "month", "--solution", "MySol", "--no-publish"])
        assert result.exit_code == 0, result.output
        body = m.last_request.json()
        assert 'name="createdon"' in body["datadescription"]
        assert 'dategrouping="month"' in body["datadescription"]

    def test_set_groupby_dategrouping_rejects_non_date_column(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        url = backend.url_for(f"savedqueryvisualizations({_EDIT_ID})")
        with rm_module.Mocker() as m:
            m.get(url, json=_EDIT_CHART)
            m.get(re.compile("EntityDefinitions"), json={"AttributeType": "Picklist"})
            result = CliRunner().invoke(cli, [
                "--json", "chart", "set-groupby", _EDIT_ID,
                "--column", "new_priority", "--dategrouping", "month", "--solution", "MySol", "--no-publish"])
        assert result.exit_code != 0
        assert "date column" in result.output.lower()

    def test_set_groupby_dry_run_previews(self, dry_backend, monkeypatch):
        _use_backend(monkeypatch, dry_backend)
        with rm_module.Mocker() as m:
            # get_chart still reads live under dry-run (reads-execute); metadata too
            m.get(dry_backend.url_for(f"savedqueryvisualizations({_EDIT_ID})"),
                  json=_EDIT_CHART)
            m.get(re.compile("EntityDefinitions"), json={"AttributeType": "Picklist"})
            result = CliRunner().invoke(cli, [
                "--json", "chart", "set-groupby", _EDIT_ID,
                "--column", "new_priority", "--solution", "MySol", "--no-publish"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["data"]["_dry_run"] is True
        assert env["data"]["would_update"] is True
