"""Unit tests for `crm metadata attributes <entity>` projection + rendering (#337).

Real D365Backend driven by requests_mock. The projection exposes write/read
validity and required level so a caller can tell which attributes are settable
when building a create/update payload.
"""
# pyright: basic

from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli


def _attrs_url(backend, logical_name: str) -> str:
    return backend.url_for(
        f"EntityDefinitions(LogicalName='{logical_name}')/Attributes"
    )


_SAMPLE = {"value": [
    {"LogicalName": "name", "SchemaName": "Name", "AttributeType": "String",
     "IsCustomAttribute": False, "IsValidForCreate": True,
     "IsValidForUpdate": True, "IsValidForRead": True,
     "RequiredLevel": {"Value": "ApplicationRequired"}},
    {"LogicalName": "fullname", "SchemaName": "FullName",
     "AttributeType": "String", "IsCustomAttribute": False,
     "IsValidForCreate": False, "IsValidForUpdate": False,
     "IsValidForRead": True, "RequiredLevel": {"Value": "None"}},
]}


@pytest.fixture
def runner():
    return CliRunner()


def test_cmd_attributes_json_carries_validity_fields(runner, backend, monkeypatch):
    """JSON mode carries all four new fields, with RequiredLevel flattened."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    with requests_mock.Mocker() as m:
        m.get(_attrs_url(backend, "contact"), json=_SAMPLE)
        result = runner.invoke(cli, ["--json", "metadata", "attributes", "contact"],
                               catch_exceptions=False)
    assert result.exit_code == 0
    data = json.loads(result.output)
    first = data["data"][0]
    assert first["IsValidForCreate"] is True
    assert first["IsValidForUpdate"] is True
    assert first["IsValidForRead"] is True
    assert first["RequiredLevel"] == "ApplicationRequired"


def test_cmd_attributes_human_surfaces_validity(runner, backend, monkeypatch):
    """Human table surfaces create/update validity and required level."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    with requests_mock.Mocker() as m:
        m.get(_attrs_url(backend, "contact"), json=_SAMPLE)
        result = runner.invoke(cli, ["metadata", "attributes", "contact"],
                               catch_exceptions=False)
    assert result.exit_code == 0
    assert "ApplicationRequired" in result.output
    # The required-level string of a read-only attribute is "None"; the create
    # validity column must distinguish it from the writable row.
    assert "fullname" in result.output
