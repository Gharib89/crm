"""Unit tests for query count (RetrieveTotalRecordCount)."""
# pyright: basic
from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core.query import total_record_count


class TestCoreHelper:
    def test_total_record_count_returns_count(self, backend):
        with requests_mock.Mocker() as m:
            m.get(
                "https://crm.contoso.local/contoso/api/data/v9.2/"
                "RetrieveTotalRecordCount(EntityNames=%5B'account'%5D)",
                json={"EntityRecordCountCollection": {
                    "Keys": ["account"], "Values": [42]
                }},
            )
            n = total_record_count(backend, "account")
        assert n == 42

    def test_total_record_count_raises_for_empty_entity(self, backend):
        from crm.utils.d365_backend import D365Error
        with pytest.raises(D365Error):
            total_record_count(backend, "")


class TestCLI:
    def test_cli_count_json_envelope(self, monkeypatch):
        from crm.cli import CLIContext

        class StubBackend:
            def get(self, path, **kw):
                assert "RetrieveTotalRecordCount" in path
                return {"EntityRecordCountCollection": {
                    "Keys": ["account"], "Values": [7]
                }}

        monkeypatch.setattr(CLIContext, "backend", lambda self: StubBackend())

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "query", "count", "account"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["count"] == 7
        assert env["data"]["entity"] == "account"
