"""CLI-layer tests for `crm dup` (duplicate-detection rules)."""

# pyright: basic
from __future__ import annotations

import json

import requests_mock as rm_module
from click.testing import CliRunner

from crm.cli import cli

_RULE_ID = "11112222-3333-4444-5555-666677778888"
_COND_ID = "aaaa1111-2222-3333-4444-555566667777"
_JOB_ID = "dddd1111-2222-3333-4444-555566667777"


def _use_backend(monkeypatch, backend):
    monkeypatch.setattr("crm.cli.CLIContext.backend", lambda self: backend)


def _entity_id_headers(backend, entity_set, rec_id):
    return {"OData-EntityId": backend.url_for(f"{entity_set}({rec_id})")}


class TestDupList:
    def test_list_rules(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        row = {
            "duplicateruleid": _RULE_ID,
            "name": "Accts",
            "baseentityname": "account",
            "matchingentityname": "account",
            "statuscode": 2,
        }
        with rm_module.Mocker() as m:
            m.get(backend.url_for("duplicaterules"), json={"value": [row]})
            result = CliRunner().invoke(cli, ["--json", "dup", "list"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"][0]["duplicateruleid"] == _RULE_ID


class TestDupCreate:
    def test_create_rule(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.post(
                backend.url_for("duplicaterules"),
                status_code=204,
                headers=_entity_id_headers(backend, "duplicaterules", _RULE_ID),
            )
            result = CliRunner().invoke(
                cli,
                ["--json", "dup", "create", "account", "--name", "Accts", "--solution", "TestSol"],
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["created"] is True
        assert data["duplicateruleid"] == _RULE_ID
        assert m.last_request.json()["baseentityname"] == "account"

    def test_dry_run(self, dry_backend, monkeypatch):
        _use_backend(monkeypatch, dry_backend)
        with rm_module.Mocker():
            result = CliRunner().invoke(
                cli,
                [
                    "--json",
                    "--dry-run",
                    "dup",
                    "create",
                    "account",
                    "--name",
                    "X",
                    "--solution",
                    "TestSol",
                ],
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["_dry_run"] is True


class TestDupAddCondition:
    def test_add_condition(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.post(
                backend.url_for("duplicateruleconditions"),
                status_code=204,
                headers=_entity_id_headers(backend, "duplicateruleconditions", _COND_ID),
            )
            result = CliRunner().invoke(
                cli,
                [
                    "--json",
                    "dup",
                    "add-condition",
                    _RULE_ID,
                    "--attr",
                    "name",
                    "--operator",
                    "exact",
                    "--solution",
                    "TestSol",
                ],
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["created"] is True
        assert m.last_request.json()["operatorcode"] == 0

    def test_invalid_operator_is_usage_error(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        result = CliRunner().invoke(
            cli,
            ["--json", "dup", "add-condition", _RULE_ID, "--attr", "name", "--operator", "bogus"],
        )
        # click.Choice rejects an invalid value at parse time (exit 2).
        assert result.exit_code == 2, result.output

    def test_same_first_param_threads_through(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.post(
                backend.url_for("duplicateruleconditions"),
                status_code=204,
                headers=_entity_id_headers(backend, "duplicateruleconditions", _COND_ID),
            )
            result = CliRunner().invoke(
                cli,
                [
                    "--json",
                    "dup",
                    "add-condition",
                    _RULE_ID,
                    "--attr",
                    "name",
                    "--operator",
                    "same-first",
                    "--operator-param",
                    "5",
                    "--solution",
                    "TestSol",
                ],
            )
        assert result.exit_code == 0, result.output
        assert m.last_request.json()["operatorparam"] == 5


class TestDupPublish:
    def test_publish(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        url = backend.url_for(
            f"duplicaterules({_RULE_ID})/Microsoft.Dynamics.CRM.PublishDuplicateRule"
        )
        with rm_module.Mocker() as m:
            m.post(url, status_code=200, json={"asyncoperationid": _JOB_ID})
            result = CliRunner().invoke(cli, ["--json", "dup", "publish", _RULE_ID])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["status"] == "submitted"
        assert data["published"] is False
        assert data["job_id"] == _JOB_ID


class TestDupUnpublish:
    def test_unpublish(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.post(backend.url_for("UnpublishDuplicateRule"), status_code=204)
            result = CliRunner().invoke(cli, ["--json", "dup", "unpublish", _RULE_ID])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["unpublished"] is True
        assert m.last_request.json()["DuplicateRuleId"] == _RULE_ID


class TestDupBulkDetect:
    def test_submit_returns_job_id(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.post(backend.url_for("BulkDetectDuplicates"), json={"JobId": _JOB_ID})
            result = CliRunner().invoke(cli, ["--json", "dup", "bulk-detect", "account"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["status"] == "submitted"
        assert data["job_id"] == _JOB_ID
        assert m.last_request.json()["Query"]["EntityName"] == "account"

    def test_wait_lists_detected_duplicates(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.post(backend.url_for("BulkDetectDuplicates"), json={"JobId": _JOB_ID})
            m.get(
                backend.url_for(f"asyncoperations({_JOB_ID})"),
                json={"asyncoperationid": _JOB_ID, "statecode": 3, "statuscode": 30},
            )
            m.get(
                backend.url_for("duplicaterecords"),
                json={"value": [{"_baserecordid_value": "a1", "duplicateid": "row1"}]},
            )
            result = CliRunner().invoke(cli, ["--json", "dup", "bulk-detect", "account", "--wait"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["status"] == "completed"
        assert data["count"] == 1
        assert data["duplicates"][0]["_baserecordid_value"] == "a1"

    def test_wait_human_mode_renders_duplicate_table(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        with rm_module.Mocker() as m:
            m.post(backend.url_for("BulkDetectDuplicates"), json={"JobId": _JOB_ID})
            m.get(
                backend.url_for(f"asyncoperations({_JOB_ID})"),
                json={"asyncoperationid": _JOB_ID, "statecode": 3, "statuscode": 30},
            )
            m.get(
                backend.url_for("duplicaterecords"),
                json={"value": [{"_baserecordid_value": "base-1", "duplicateid": "row-1"}]},
            )
            result = CliRunner().invoke(cli, ["dup", "bulk-detect", "account", "--wait"])
        assert result.exit_code == 0, result.output
        # Human mode renders the flagged records as a table.
        assert "baserecordid" in result.output
        assert "base-1" in result.output

    def test_both_fetchxml_sources_is_usage_error(self, backend, monkeypatch, tmp_path):
        _use_backend(monkeypatch, backend)
        f = tmp_path / "q.xml"
        f.write_text('<fetch><entity name="account"/></fetch>', encoding="utf-8")
        result = CliRunner().invoke(
            cli,
            [
                "--json",
                "dup",
                "bulk-detect",
                "account",
                "--fetchxml",
                '<fetch><entity name="account"/></fetch>',
                "--fetchxml-file",
                str(f),
            ],
        )
        assert result.exit_code == 2, result.output


class TestDupCheck:
    def test_check_with_inline_data(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        url = backend.url_for(
            "RetrieveDuplicates(BusinessEntity=@p1,MatchingEntityName=@p2,PagingInfo=@p3)"
        )
        with rm_module.Mocker() as m:
            m.get(url, json={"value": [{"accountid": "x", "name": "Contoso"}]})
            result = CliRunner().invoke(
                cli, ["--json", "dup", "check", "account", "--data", '{"name": "Contoso"}']
            )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        assert data["count"] == 1
        assert data["duplicates"][0]["name"] == "Contoso"

    def test_check_requires_data(self, backend, monkeypatch):
        _use_backend(monkeypatch, backend)
        result = CliRunner().invoke(cli, ["--json", "dup", "check", "account"])
        # _load_payload raises a usage error when neither --data nor --data-file given.
        assert result.exit_code == 2, result.output
