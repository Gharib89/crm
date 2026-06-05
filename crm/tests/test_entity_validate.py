"""Pre-write field-name validation for `entity create/update --validate` (#72).

Mirrors the mocked-backend metadata pattern (see test_metadata_describe): a real
D365Backend driven by requests_mock so the exact GET paths are asserted and
over-fetching surfaces as NoMockAddress. Validation is FIELD-NAME only (v1): no
picklist-value checking.
"""
# pyright: basic

from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


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


def _sets_url(backend) -> str:
    return backend.url_for("EntityDefinitions")


def _attrs_url(backend) -> str:
    return backend.url_for("EntityDefinitions(LogicalName='account')/Attributes")


def _m2o_url(backend) -> str:
    return backend.url_for(
        "EntityDefinitions(LogicalName='account')/ManyToOneRelationships"
    )


_SETS = {"value": [{"LogicalName": "account", "EntitySetName": "accounts"}]}
_ATTRS = {"value": [
    {"LogicalName": "name"},
    {"LogicalName": "telephone1"},
    {"LogicalName": "accountid"},
]}
_M2O = {"value": [
    {"ReferencingEntityNavigationPropertyName": "primarycontactid_account"},
]}


def _mock_three(m, backend) -> None:
    m.get(_sets_url(backend), json=_SETS)
    m.get(_attrs_url(backend), json=_ATTRS)
    m.get(_m2o_url(backend), json=_M2O)


class TestTracer:
    def test_unknown_field_flagged_with_did_you_mean(self, backend):
        from crm.core import entity as ent
        with requests_mock.Mocker() as m:
            _mock_three(m, backend)
            result = ent.validate_payload(backend, "accounts", {"naem": "Contoso"})
            assert {r.method for r in m.request_history} == {"GET"}

        assert result["ok"] is False
        assert result["meta"]["unknown_fields"] == ["naem"]
        assert result["meta"]["did_you_mean"] == {"naem": "name"}


class TestNavBindUnion:
    def test_valid_odata_bind_nav_not_flagged(self, backend):
        from crm.core import entity as ent
        payload = {
            "name": "Contoso",
            "primarycontactid_account@odata.bind": "/contacts(<guid>)",
        }
        with requests_mock.Mocker() as m:
            _mock_three(m, backend)
            result = ent.validate_payload(backend, "accounts", payload)
        # Nav property resolves via the ManyToOne union, so the bound lookup is
        # a known field — nothing flagged.
        assert result == {"ok": True}


class TestGetCost:
    def test_no_bind_keys_skips_relationships_get(self, backend):
        from crm.core import entity as ent
        # Payload has no @odata.bind keys, so nav-property names cannot matter:
        # only the set→logical and attributes GETs should fire (2, not 3).
        with requests_mock.Mocker() as m:
            m.get(_sets_url(backend), json=_SETS)
            m.get(_attrs_url(backend), json=_ATTRS)
            result = ent.validate_payload(
                backend, "accounts", {"name": "Contoso", "telephone1": "555"}
            )
            paths = [r.path for r in m.request_history]
        assert result == {"ok": True}
        assert not any("ManyToOneRelationships" in p for p in paths)


class TestDidYouMean:
    def test_no_suggestion_when_nothing_close(self, backend):
        from crm.core import entity as ent
        with requests_mock.Mocker() as m:
            _mock_three(m, backend)
            result = ent.validate_payload(
                backend, "accounts", {"zzzqqq": "x"}
            )
        # Flagged unknown, but no valid key is close enough to suggest.
        assert result["ok"] is False
        assert result["meta"]["unknown_fields"] == ["zzzqqq"]
        assert result["meta"]["did_you_mean"] == {}


class TestControlAnnotations:
    def test_control_annotation_key_ignored(self, backend):
        from crm.core import entity as ent
        payload = {"@odata.etag": 'W/"123"', "name": "Contoso"}
        with requests_mock.Mocker() as m:
            _mock_three(m, backend)
            result = ent.validate_payload(backend, "accounts", payload)
        # A bare control annotation strips to "" and is never treated as a field.
        assert result == {"ok": True}


class TestUnknownEntitySet:
    def test_unresolvable_set_raises(self, backend):
        from crm.core import entity as ent
        with requests_mock.Mocker() as m:
            m.get(_sets_url(backend), json={"value": []})
            with pytest.raises(D365Error, match="Unknown entity set"):
                ent.validate_payload(backend, "nopes", {"name": "x"})


@pytest.fixture
def dry_backend(profile):
    return D365Backend(profile, password="pw", dry_run=True)


class TestCommandGate:
    def _stub(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    def test_create_validate_blocks_write_on_unknown_field(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            _mock_three(m, backend)
            result = CliRunner().invoke(cli, [
                "--json", "entity", "create", "accounts",
                "--data", json.dumps({"naem": "Contoso"}), "--validate",
            ])
            # Validation read-only GETs ran; the POST never fired.
            assert {r.method for r in m.request_history} == {"GET"}
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert env["meta"]["unknown_fields"] == ["naem"]
        assert env["meta"]["did_you_mean"] == {"naem": "name"}

    def test_create_validate_dry_run_composes(self, monkeypatch, dry_backend):
        self._stub(monkeypatch, dry_backend)
        with requests_mock.Mocker() as m:
            _mock_three(m, dry_backend)
            result = CliRunner().invoke(cli, [
                "--json", "--dry-run", "entity", "create", "accounts",
                "--data", json.dumps({"name": "Contoso"}), "--validate",
            ])
            # Validation forces real GETs even under dry-run; the write itself is
            # previewed, never issued.
            assert {r.method for r in m.request_history} == {"GET"}
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["_dry_run"] is True
        assert env["data"]["method"] == "POST"

    def test_update_validate_blocks_write_on_unknown_field(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        guid = "11111111-1111-1111-1111-111111111111"
        with requests_mock.Mocker() as m:
            _mock_three(m, backend)
            result = CliRunner().invoke(cli, [
                "--json", "entity", "update", "accounts", guid,
                "--data", json.dumps({"naem": "x"}), "--validate",
            ])
            assert {r.method for r in m.request_history} == {"GET"}
        assert result.exit_code != 0
        env = json.loads(result.output)
        assert env["ok"] is False
        assert env["meta"]["unknown_fields"] == ["naem"]
