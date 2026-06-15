"""Unit tests for query count (RetrieveTotalRecordCount)."""
# pyright: basic
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.core.query import total_record_count
from crm.utils.d365_backend import ConnectionProfile


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


@pytest.fixture(autouse=True)
def _isolate_crm_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`query count` now resolves the name through the read-through metadata cache
    (#305); isolate CRM_HOME so a real ~/.crm is never touched and each test
    starts with a cold cache."""
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))


def _count_backend(monkeypatch: pytest.MonkeyPatch, count: int = 7) -> MagicMock:
    """Backend stub: resolves names via the EntityDefinitions collection and
    returns `count` from RetrieveTotalRecordCount."""
    mock = MagicMock()
    mock.profile = ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )

    def _get(path: str, **_kw: Any) -> Any:
        assert "RetrieveTotalRecordCount" in path
        return {"EntityRecordCountCollection": {"Keys": ["account"], "Values": [count]}}

    def _get_collection(path: str, **_kw: Any) -> Any:
        if path == "EntityDefinitions":
            return [{"LogicalName": "account", "EntitySetName": "accounts"}]
        return []

    mock.get.side_effect = _get
    mock.get_collection.side_effect = _get_collection
    monkeypatch.setattr(CLIContext, "backend", lambda self: mock)
    return mock


class TestCLI:
    def test_cli_count_logical_name(self, monkeypatch):
        _count_backend(monkeypatch)
        result = CliRunner().invoke(cli, ["--json", "query", "count", "account"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["count"] == 7
        assert env["data"]["entity"] == "account"

    def test_cli_count_accepts_entity_set_name(self, monkeypatch):
        """`query count accounts` resolves the set name to the logical name (#305)."""
        _count_backend(monkeypatch)
        result = CliRunner().invoke(cli, ["--json", "query", "count", "accounts"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["count"] == 7
        # Output reports the canonical logical name actually counted.
        assert env["data"]["entity"] == "account"

    def test_cli_count_is_case_insensitive(self, monkeypatch):
        _count_backend(monkeypatch)
        for name in ("Account", "Accounts", "ACCOUNTS"):
            result = CliRunner().invoke(cli, ["--json", "query", "count", name])
            assert result.exit_code == 0, result.output
            env = json.loads(result.output)
            assert env["data"]["entity"] == "account"

    def test_cli_count_unknown_entity_errors_cleanly(self, monkeypatch):
        _count_backend(monkeypatch)
        result = CliRunner().invoke(cli, ["--json", "query", "count", "totallybogus"])
        assert result.exit_code == 1, result.output
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "totallybogus" in env["error"]
