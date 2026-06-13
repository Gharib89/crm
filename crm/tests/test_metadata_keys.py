"""Unit tests for `crm metadata keys <entity>` (#232).

Real D365Backend driven by requests_mock — over-fetching is a bug: requests_mock
raises NoMockAddress for any endpoint not registered, so each test mocks only
the round-trips its scenario should make.
"""
# pyright: basic

from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.core import metadata as meta_mod
from crm.utils.d365_backend import D365Error


def _keys_url(backend, logical_name: str) -> str:
    return backend.url_for(f"EntityDefinitions(LogicalName='{logical_name}')/Keys")


# ── core: list_entity_keys ─────────────────────────────────────────────────


def test_list_entity_keys_empty(backend):
    """Entity with no alternate keys returns empty list (not an error)."""
    with requests_mock.Mocker() as m:
        m.get(_keys_url(backend, "account"), json={"value": []})
        result = meta_mod.list_entity_keys(backend, "account")
    assert result == []


def test_list_entity_keys_returns_key_fields(backend):
    """Key objects include logical_name, schema_name, key_attributes, index_status."""
    with requests_mock.Mocker() as m:
        m.get(_keys_url(backend, "account"), json={"value": [
            {
                "LogicalName": "account_code_ak",
                "SchemaName": "Account_Code_AK",
                "KeyAttributes": ["accountnumber"],
                "EntityKeyIndexStatus": "Active",
            }
        ]})
        result = meta_mod.list_entity_keys(backend, "account")
    assert len(result) == 1
    k = result[0]
    assert k["logical_name"] == "account_code_ak"
    assert k["schema_name"] == "Account_Code_AK"
    assert k["key_attributes"] == ["accountnumber"]
    assert k["index_status"] == "Active"


def test_list_entity_keys_multi_attr_key(backend):
    """Composite key: key_attributes contains multiple attribute names."""
    with requests_mock.Mocker() as m:
        m.get(_keys_url(backend, "contact"), json={"value": [
            {
                "LogicalName": "contact_name_email_ak",
                "SchemaName": "Contact_Name_Email_AK",
                "KeyAttributes": ["firstname", "emailaddress1"],
                "EntityKeyIndexStatus": "Active",
            }
        ]})
        result = meta_mod.list_entity_keys(backend, "contact")
    assert result[0]["key_attributes"] == ["firstname", "emailaddress1"]


def test_list_entity_keys_multiple_keys(backend):
    """Entity with multiple alternate keys returns all of them."""
    with requests_mock.Mocker() as m:
        m.get(_keys_url(backend, "account"), json={"value": [
            {"LogicalName": "ak1", "SchemaName": "AK1", "KeyAttributes": ["a"], "EntityKeyIndexStatus": "Active"},
            {"LogicalName": "ak2", "SchemaName": "AK2", "KeyAttributes": ["b", "c"], "EntityKeyIndexStatus": "Pending"},
        ]})
        result = meta_mod.list_entity_keys(backend, "account")
    assert len(result) == 2
    assert result[1]["index_status"] == "Pending"


def test_list_entity_keys_missing_logical_name_raises(backend):
    """Empty logical_name raises D365Error immediately (no backend call)."""
    with requests_mock.Mocker():
        with pytest.raises(D365Error):
            meta_mod.list_entity_keys(backend, "")


def test_list_entity_keys_propagates_backend_error(backend):
    """D365Error from the backend (e.g. 404) propagates unchanged."""
    with requests_mock.Mocker() as m:
        m.get(_keys_url(backend, "nosuchentity"), status_code=404, json={
            "error": {"code": "0x80040217", "message": "Not found"}
        })
        with pytest.raises(D365Error) as exc_info:
            meta_mod.list_entity_keys(backend, "nosuchentity")
    assert exc_info.value.status == 404


# ── command: crm metadata keys ─────────────────────────────────────────────


@pytest.fixture
def runner():
    return CliRunner()


def test_cmd_keys_json_empty(runner, backend, monkeypatch):
    """Command with no keys emits ok=true, empty data list, count=0 in JSON mode."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    with requests_mock.Mocker() as m:
        m.get(_keys_url(backend, "account"), json={"value": []})
        result = runner.invoke(cli, ["--json", "metadata", "keys", "account"],
                               catch_exceptions=False)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["data"] == []
    assert data["meta"]["count"] == 0


def test_cmd_keys_json_with_key(runner, backend, monkeypatch):
    """Command with a key emits the key in data with correct fields."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    with requests_mock.Mocker() as m:
        m.get(_keys_url(backend, "account"), json={"value": [
            {
                "LogicalName": "account_code_ak",
                "SchemaName": "Account_Code_AK",
                "KeyAttributes": ["accountnumber"],
                "EntityKeyIndexStatus": "Active",
            }
        ]})
        result = runner.invoke(cli, ["--json", "metadata", "keys", "account"],
                               catch_exceptions=False)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["meta"]["count"] == 1
    k = data["data"][0]
    assert k["logical_name"] == "account_code_ak"
    assert k["key_attributes"] == ["accountnumber"]


def test_cmd_keys_human_mode_empty(runner, backend, monkeypatch):
    """Human mode with no keys exits 0 and shows a zero count."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    with requests_mock.Mocker() as m:
        m.get(_keys_url(backend, "account"), json={"value": []})
        result = runner.invoke(cli, ["metadata", "keys", "account"],
                               catch_exceptions=False)
    assert result.exit_code == 0
    assert "0" in result.output


def test_cmd_keys_human_mode_table(runner, backend, monkeypatch):
    """Human mode with keys renders a table containing the key's logical name."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    with requests_mock.Mocker() as m:
        m.get(_keys_url(backend, "account"), json={"value": [
            {
                "LogicalName": "account_code_ak",
                "SchemaName": "Account_Code_AK",
                "KeyAttributes": ["accountnumber"],
                "EntityKeyIndexStatus": "Active",
            }
        ]})
        result = runner.invoke(cli, ["metadata", "keys", "account"],
                               catch_exceptions=False)
    assert result.exit_code == 0
    assert "account_code_ak" in result.output


def test_cmd_keys_error_propagates(runner, backend, monkeypatch):
    """Backend error (e.g. 404) exits non-zero with ok=false."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    with requests_mock.Mocker() as m:
        m.get(_keys_url(backend, "nosuch"), status_code=404, json={
            "error": {"code": "0x80040217", "message": "Entity not found"}
        })
        result = runner.invoke(cli, ["--json", "metadata", "keys", "nosuch"],
                               catch_exceptions=False)
    assert result.exit_code != 0
    data = json.loads(result.output)
    assert data["ok"] is False
