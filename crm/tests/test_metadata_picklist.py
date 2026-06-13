"""Unit tests for `metadata picklist` — type-aware cast selection (#229).

`picklist_options()` must use the correct OData cast segment for each
attribute type (Picklist → PicklistAttributeMetadata, State →
StateAttributeMetadata, Status → StatusAttributeMetadata) by inspecting
the attribute's `AttributeType` before fetching options.

requests_mock raises NoMockAddress for any unmocked endpoint, so each test
registers exactly the GET paths its scenario should hit.
"""
# pyright: basic

from __future__ import annotations

import json

import pytest
import requests_mock as req_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.utils.d365_backend import D365Error


def _attr_info_url(backend, entity: str, attribute: str) -> str:
    return backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')"
        f"/Attributes(LogicalName='{attribute}')"
    )


def _cast_url(backend, entity: str, attribute: str, cast: str) -> str:
    return backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')"
        f"/Attributes(LogicalName='{attribute}')"
        f"/Microsoft.Dynamics.CRM.{cast}"
    )


def _opt(value: int, lbl: str) -> dict:
    return {"Value": value, "Label": {"UserLocalizedLabel": {"Label": lbl}}}


class TestPicklistOptionsStatusType:
    """statuscode (Status) returns options via StatusAttributeMetadata."""

    def test_status_attribute_uses_status_cast(self, backend):
        from crm.core import metadata as meta
        with req_mock.Mocker() as m:
            m.get(
                _attr_info_url(backend, "account", "statuscode"),
                json={"LogicalName": "statuscode", "AttributeType": "Status"},
            )
            m.get(
                _cast_url(backend, "account", "statuscode", "StatusAttributeMetadata"),
                json={
                    "LogicalName": "statuscode",
                    "OptionSet": {"Options": [
                        _opt(1, "Active"),
                        _opt(2, "Inactive"),
                    ]},
                },
            )
            info = meta.picklist_options(backend, "account", "statuscode")

        assert info["LogicalName"] == "statuscode"
        assert len(info["OptionSet"]["Options"]) == 2
        cast_req = m.request_history[-1]
        assert "StatusAttributeMetadata" in cast_req.url

    def test_status_command_meta_options_populated(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
        with req_mock.Mocker() as m:
            m.get(
                _attr_info_url(backend, "account", "statuscode"),
                json={"LogicalName": "statuscode", "AttributeType": "Status"},
            )
            m.get(
                _cast_url(backend, "account", "statuscode", "StatusAttributeMetadata"),
                json={
                    "LogicalName": "statuscode",
                    "OptionSet": {"Options": [
                        _opt(1, "Active"),
                        _opt(2, "Inactive"),
                    ]},
                },
            )
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "picklist", "account", "statuscode"]
            )

        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["meta"]["options"] == [
            {"value": 1, "label": "Active"},
            {"value": 2, "label": "Inactive"},
        ]


class TestPicklistOptionsStateType:
    """statecode (State) returns options via StateAttributeMetadata."""

    def test_state_attribute_uses_state_cast(self, backend):
        from crm.core import metadata as meta
        with req_mock.Mocker() as m:
            m.get(
                _attr_info_url(backend, "account", "statecode"),
                json={"LogicalName": "statecode", "AttributeType": "State"},
            )
            m.get(
                _cast_url(backend, "account", "statecode", "StateAttributeMetadata"),
                json={
                    "LogicalName": "statecode",
                    "OptionSet": {"Options": [
                        _opt(0, "Active"),
                        _opt(1, "Inactive"),
                    ]},
                },
            )
            info = meta.picklist_options(backend, "account", "statecode")

        assert info["LogicalName"] == "statecode"
        cast_req = m.request_history[-1]
        assert "StateAttributeMetadata" in cast_req.url

    def test_state_command_meta_options_populated(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
        with req_mock.Mocker() as m:
            m.get(
                _attr_info_url(backend, "account", "statecode"),
                json={"LogicalName": "statecode", "AttributeType": "State"},
            )
            m.get(
                _cast_url(backend, "account", "statecode", "StateAttributeMetadata"),
                json={
                    "LogicalName": "statecode",
                    "OptionSet": {"Options": [
                        _opt(0, "Active"),
                        _opt(1, "Inactive"),
                    ]},
                },
            )
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "picklist", "account", "statecode"]
            )

        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["meta"]["options"] == [
            {"value": 0, "label": "Active"},
            {"value": 1, "label": "Inactive"},
        ]


class TestPicklistOptionsPicklistType:
    """Plain Picklist still uses PicklistAttributeMetadata (no regression)."""

    def test_picklist_attribute_uses_picklist_cast(self, backend):
        from crm.core import metadata as meta
        with req_mock.Mocker() as m:
            m.get(
                _attr_info_url(backend, "account", "industrycode"),
                json={"LogicalName": "industrycode", "AttributeType": "Picklist"},
            )
            m.get(
                _cast_url(backend, "account", "industrycode", "PicklistAttributeMetadata"),
                json={
                    "LogicalName": "industrycode",
                    "OptionSet": {"Options": [_opt(1, "Tech"), _opt(2, "Finance")]},
                    "GlobalOptionSet": None,
                },
            )
            info = meta.picklist_options(backend, "account", "industrycode")

        assert info["LogicalName"] == "industrycode"
        cast_req = m.request_history[-1]
        assert "PicklistAttributeMetadata" in cast_req.url

    def test_global_bound_picklist_fallback_still_works(self, monkeypatch, backend):
        """OptionSet null → flatten GlobalOptionSet → meta.options populated."""
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
        with req_mock.Mocker() as m:
            m.get(
                _attr_info_url(backend, "account", "industrycode"),
                json={"LogicalName": "industrycode", "AttributeType": "Picklist"},
            )
            m.get(
                _cast_url(backend, "account", "industrycode", "PicklistAttributeMetadata"),
                json={
                    "LogicalName": "industrycode",
                    "OptionSet": None,
                    "GlobalOptionSet": {"Options": [_opt(10, "Low"), _opt(20, "High")]},
                },
            )
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "picklist", "account", "industrycode"]
            )

        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["meta"]["options"] == [
            {"value": 10, "label": "Low"},
            {"value": 20, "label": "High"},
        ]


class TestNoGlobalFlag:
    """--no-global strips GlobalOptionSet from the expand parameter."""

    def test_no_global_omits_global_optionset_expand(self, backend):
        from crm.core import metadata as meta
        with req_mock.Mocker() as m:
            m.get(
                _attr_info_url(backend, "account", "industrycode"),
                json={"LogicalName": "industrycode", "AttributeType": "Picklist"},
            )
            m.get(
                _cast_url(backend, "account", "industrycode", "PicklistAttributeMetadata"),
                json={
                    "LogicalName": "industrycode",
                    "OptionSet": {"Options": [_opt(1, "Tech")]},
                },
            )
            info = meta.picklist_options(backend, "account", "industrycode",
                                         global_optionset=False)

        cast_req = m.request_history[-1]
        assert "GlobalOptionSet" not in cast_req.url
        assert "OptionSet" in cast_req.url
        assert info["OptionSet"]["Options"][0]["Value"] == 1


class TestPicklistOptionsUnsupportedType:
    """Unsupported attribute types → clear D365Error, not null options with ok:true."""

    def test_string_attribute_raises_clear_error(self, backend):
        from crm.core import metadata as meta
        with req_mock.Mocker() as m:
            m.get(
                _attr_info_url(backend, "account", "name"),
                json={"LogicalName": "name", "AttributeType": "String"},
            )
            with pytest.raises(D365Error, match="String"):
                meta.picklist_options(backend, "account", "name")

    def test_command_unsupported_type_returns_ok_false(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
        with req_mock.Mocker() as m:
            m.get(
                _attr_info_url(backend, "account", "name"),
                json={"LogicalName": "name", "AttributeType": "String"},
            )
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "picklist", "account", "name"]
            )

        # ok:false → cli emits JSON then exits with FAILURE_EXIT_CODE (1)
        assert result.exit_code == 1
        env = json.loads(result.output)
        assert env["ok"] is False
        assert "String" in env["error"]
