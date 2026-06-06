"""`--expect ATTR=VALUE` field-comparison verify primitive (#86).

A stringified AND-gate run after a record/definition is retrieved by
`entity get` / `metadata attribute`: every pair must match
(str(record.get(attr)) == VALUE) or the FIRST mismatch in CLI order exits 1
with meta {attr, expected, actual} (actual is the raw value). A malformed pair
is a usage error (exit 2) raised before any backend call — so no HTTP fires.

Mirrors test_entity_validate.py: a real D365Backend driven by requests_mock so
the exact GET path is asserted.
"""
# pyright: basic

from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.core import entity as entity_mod
from crm.utils.d365_backend import ConnectionProfile, D365Backend


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


@pytest.fixture
def backend(profile):
    return D365Backend(profile, password="pw", dry_run=False)


_GUID = "11111111-1111-1111-1111-111111111111"
_RECORD = {"accountid": _GUID, "name": "Contoso", "statecode": 0}
_ATTR = {"LogicalName": "industrycode", "AttributeType": "String"}


def _stub(monkeypatch, backend) -> None:
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)


def _get_url(backend) -> str:
    return backend.url_for(entity_mod.build_record_path("accounts", _GUID))


def _attr_url(backend) -> str:
    return backend.url_for(
        "EntityDefinitions(LogicalName='account')/Attributes(LogicalName='industrycode')"
    )


def _invoke(args):
    return CliRunner().invoke(cli, args)


class TestEntityGet:
    def test_match_passes_through(self, monkeypatch, backend):
        _stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(_get_url(backend), json=_RECORD)
            result = _invoke([
                "--json", "entity", "get", "accounts", _GUID,
                "--expect", "name=Contoso",
            ])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["name"] == "Contoso"

    def test_mismatch_exits_1_with_meta(self, monkeypatch, backend):
        _stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(_get_url(backend), json=_RECORD)
            result = _invoke([
                "--json", "entity", "get", "accounts", _GUID,
                "--expect", "name=Acme",
            ])
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert env["meta"] == {"attr": "name", "expected": "Acme", "actual": "Contoso"}

    def test_missing_key_never_matches(self, monkeypatch, backend):
        # A missing key stringifies to "None" and so cannot match a real value.
        _stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(_get_url(backend), json=_RECORD)
            result = _invoke([
                "--json", "entity", "get", "accounts", _GUID,
                "--expect", "nope=anything",
            ])
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert env["meta"] == {"attr": "nope", "expected": "anything", "actual": None}

    def test_value_may_contain_equals(self, monkeypatch, backend):
        # Split on the FIRST '=', so a VALUE may itself contain '='. Expecting
        # name=a=b must compare against the whole "a=b", not "a".
        _stub(monkeypatch, backend)
        record = {**_RECORD, "name": "a=b"}
        with requests_mock.Mocker() as m:
            m.get(_get_url(backend), json=record)
            result = _invoke([
                "--json", "entity", "get", "accounts", _GUID,
                "--expect", "name=a=b",
            ])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["ok"] is True

    def test_actual_is_raw_value_not_stringified(self, monkeypatch, backend):
        # statecode is the int 0; the meta `actual` must stay the raw int so JSON
        # consumers see real types (the comparison itself is stringified).
        _stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(_get_url(backend), json=_RECORD)
            result = _invoke([
                "--json", "entity", "get", "accounts", _GUID,
                "--expect", "statecode=1",
            ])
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["meta"]["actual"] == 0
        assert env["meta"]["actual"] is not None


class TestMetadataAttribute:
    def test_match_passes_through(self, monkeypatch, backend):
        _stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(_attr_url(backend), json=_ATTR)
            result = _invoke([
                "--json", "metadata", "attribute", "account", "industrycode",
                "--expect", "AttributeType=String",
            ])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["AttributeType"] == "String"

    def test_mismatch_exits_1_with_meta(self, monkeypatch, backend):
        _stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(_attr_url(backend), json=_ATTR)
            result = _invoke([
                "--json", "metadata", "attribute", "account", "industrycode",
                "--expect", "AttributeType=Lookup",
            ])
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert env["meta"] == {
            "attr": "AttributeType", "expected": "Lookup", "actual": "String",
        }


class TestRepeatableAndGate:
    def test_both_match_passes(self, monkeypatch, backend):
        _stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(_get_url(backend), json=_RECORD)
            result = _invoke([
                "--json", "entity", "get", "accounts", _GUID,
                "--expect", "name=Contoso", "--expect", "statecode=0",
            ])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["ok"] is True

    def test_first_matches_second_fails_reports_second(self, monkeypatch, backend):
        # AND-gate in CLI order: the first pair matches, so the reported miss is
        # the SECOND one (proves both ordering and the AND semantics).
        _stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(_get_url(backend), json=_RECORD)
            result = _invoke([
                "--json", "entity", "get", "accounts", _GUID,
                "--expect", "name=Contoso", "--expect", "statecode=9",
            ])
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert env["meta"] == {"attr": "statecode", "expected": "9", "actual": 0}


class TestMalformedPair:
    def test_no_equals_is_usage_error_before_backend(self, monkeypatch, backend):
        # A malformed pair must raise UsageError (exit 2) before any backend call,
        # so a typo never costs an HTTP round-trip.
        _stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(_get_url(backend), json=_RECORD)
            result = _invoke([
                "--json", "entity", "get", "accounts", _GUID,
                "--expect", "foo",
            ])
            assert m.request_history == []
        assert result.exit_code == 2
        assert "ATTR=VALUE" in result.output

    def test_empty_attr_is_usage_error(self, monkeypatch, backend):
        _stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(_attr_url(backend), json=_ATTR)
            result = _invoke([
                "--json", "metadata", "attribute", "account", "industrycode",
                "--expect", "=value",
            ])
            assert m.request_history == []
        assert result.exit_code == 2
        assert "ATTR=VALUE" in result.output
