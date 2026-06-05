"""Unit tests for `metadata describe <entity>` — the one-shot write-readiness
brief (#68). Mirrors the mocked-backend metadata-update test pattern: a real
D365Backend driven by requests_mock so the exact GET paths are asserted.

The brief is built from pure read-only GETs and over-fetching is itself a bug:
requests_mock raises NoMockAddress for any endpoint a test does not register, so
each test mocks ONLY the round-trips its scenario should make.
"""
# pyright: basic

from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
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


def _entity_url(backend) -> str:
    return backend.url_for("EntityDefinitions(LogicalName='new_project')")


def _attrs_url(backend) -> str:
    return backend.url_for("EntityDefinitions(LogicalName='new_project')/Attributes")


def _m2o_url(backend) -> str:
    return backend.url_for(
        "EntityDefinitions(LogicalName='new_project')/ManyToOneRelationships"
    )


def _cast_url(backend, cast: str) -> str:
    return backend.url_for(
        f"EntityDefinitions(LogicalName='new_project')/Attributes/"
        f"Microsoft.Dynamics.CRM.{cast}"
    )


def _opt(value: int, lbl: str) -> dict:
    return {"Value": value, "Label": {"UserLocalizedLabel": {"Label": lbl}}}


_ENTITY = {
    "LogicalName": "new_project",
    "EntitySetName": "new_projects",
    "PrimaryIdAttribute": "new_projectid",
    "PrimaryNameAttribute": "new_name",
}


def _attr(logical, attr_type, *, required="None", create=True, update=True):
    return {
        "LogicalName": logical,
        "AttributeType": attr_type,
        "RequiredLevel": {"Value": required},
        "IsValidForCreate": create,
        "IsValidForUpdate": update,
    }


class TestTracer:
    def test_brief_carries_entity_set_primary_ids_and_writable_attrs(self, backend):
        from crm.core import metadata as meta
        attrs = {"value": [
            _attr("new_name", "String", required="ApplicationRequired"),
            _attr("new_code", "String"),
            # Not writable: system column valid for neither create nor update.
            _attr("createdon", "DateTime", create=False, update=False),
        ]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            brief = meta.describe_entity(backend, "new_project")

        assert brief["entity_set_name"] == "new_projects"
        assert brief["primary_id"] == "new_projectid"
        assert brief["primary_name"] == "new_name"

        by_name = {a["logical_name"]: a for a in brief["writable_attributes"]}
        # Non-writable system column is filtered out entirely.
        assert "createdon" not in by_name
        assert set(by_name) == {"new_name", "new_code"}
        assert by_name["new_name"]["attribute_type"] == "String"
        assert by_name["new_name"]["required_level"] == "ApplicationRequired"
        assert by_name["new_code"]["required_level"] == "None"


class TestLookupBindEnrichment:
    def test_lookup_exposes_bind_key_and_targets_with_set_name(self, backend):
        from crm.core import metadata as meta
        attrs = {"value": [
            _attr("new_name", "String", required="ApplicationRequired"),
            _attr("new_accountid", "Lookup"),
        ]}
        m2o = {"value": [{
            "ReferencingAttribute": "new_accountid",
            "ReferencedEntity": "account",
            "ReferencedEntityNavigationPropertyName": "new_AccountId",
        }]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_m2o_url(backend), json=m2o)
            # Per-referenced-entity set-name resolution.
            m.get(
                backend.url_for("EntityDefinitions(LogicalName='account')"),
                json={"EntitySetName": "accounts"},
            )
            brief = meta.describe_entity(backend, "new_project")

        by_name = {a["logical_name"]: a for a in brief["writable_attributes"]}
        lookup = by_name["new_accountid"]
        # Bind key is the navigation property + @odata.bind, self-derived from
        # the ManyToOne relationship joined on ReferencingAttribute.
        assert lookup["bind_key"] == "new_AccountId@odata.bind"
        assert lookup["targets"] == [{"logical": "account", "set_name": "accounts"}]
        # A non-lookup attribute carries neither enrichment key.
        assert "bind_key" not in by_name["new_name"]
        assert "targets" not in by_name["new_name"]


class TestPicklistLocalOptions:
    def test_local_picklist_exposes_inline_options(self, backend):
        from crm.core import metadata as meta
        attrs = {"value": [
            _attr("new_name", "String", required="ApplicationRequired"),
            _attr("new_stage", "Picklist"),
        ]}
        picklists = {"value": [{
            "LogicalName": "new_stage",
            "OptionSet": {
                "MetadataId": "55555555-5555-5555-5555-555555555555",
                "IsGlobal": False,
                "Options": [_opt(1, "New"), _opt(2, "Done")],
            },
            "GlobalOptionSet": None,
        }]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_cast_url(backend, "PicklistAttributeMetadata"), json=picklists)
            brief = meta.describe_entity(backend, "new_project")

        stage = {a["logical_name"]: a for a in brief["writable_attributes"]}["new_stage"]
        assert stage["options"] == [
            {"value": 1, "label": "New"},
            {"value": 2, "label": "Done"},
        ]
        # A local option set carries no global option set id.
        assert "global_optionset_id" not in stage


class TestPicklistGlobalOptionSet:
    def test_global_bound_picklist_emits_options_and_optionset_id(self, backend):
        from crm.core import metadata as meta
        attrs = {"value": [_attr("new_priority", "Picklist")]}
        # Global-bound: OptionSet is null, GlobalOptionSet carries the options
        # AND the MetadataId GUID (on-prem 9.1 needs the GUID to bind on create).
        gos_id = "99999999-9999-9999-9999-999999999999"
        picklists = {"value": [{
            "LogicalName": "new_priority",
            "OptionSet": None,
            "GlobalOptionSet": {
                "MetadataId": gos_id,
                "IsGlobal": True,
                "Options": [_opt(10, "Low"), _opt(20, "High")],
            },
        }]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_cast_url(backend, "PicklistAttributeMetadata"), json=picklists)
            brief = meta.describe_entity(backend, "new_project")

        prio = {a["logical_name"]: a
                for a in brief["writable_attributes"]}["new_priority"]
        assert prio["options"] == [
            {"value": 10, "label": "Low"},
            {"value": 20, "label": "High"},
        ]
        assert prio["global_optionset_id"] == gos_id


class TestStateStatusOptions:
    def test_state_and_status_carry_inline_options(self, backend):
        from crm.core import metadata as meta
        attrs = {"value": [
            _attr("statecode", "State"),
            _attr("statuscode", "Status"),
        ]}
        states = {"value": [{
            "LogicalName": "statecode",
            "OptionSet": {"Options": [_opt(0, "Active"), _opt(1, "Inactive")]},
        }]}
        statuses = {"value": [{
            "LogicalName": "statuscode",
            "OptionSet": {"Options": [_opt(1, "Active"), _opt(2, "Inactive")]},
        }]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            m.get(_cast_url(backend, "StateAttributeMetadata"), json=states)
            m.get(_cast_url(backend, "StatusAttributeMetadata"), json=statuses)
            brief = meta.describe_entity(backend, "new_project")

        by_name = {a["logical_name"]: a for a in brief["writable_attributes"]}
        assert by_name["statecode"]["options"] == [
            {"value": 0, "label": "Active"},
            {"value": 1, "label": "Inactive"},
        ]
        assert by_name["statuscode"]["options"] == [
            {"value": 1, "label": "Active"},
            {"value": 2, "label": "Inactive"},
        ]


class TestCommand:
    def _stub(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    def test_describe_emits_brief_via_pure_gets(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        attrs = {"value": [
            _attr("new_name", "String", required="ApplicationRequired"),
        ]}
        with requests_mock.Mocker() as m:
            m.get(_entity_url(backend), json=_ENTITY)
            m.get(_attrs_url(backend), json=attrs)
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "describe", "new_project"]
            )
            # The brief is built from read-only GETs alone.
            assert {r.method for r in m.request_history} == {"GET"}
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["data"]["entity_set_name"] == "new_projects"
        assert env["data"]["primary_id"] == "new_projectid"
        assert env["meta"]["writable_attributes"] == 1

    def test_describe_help_lists_command(self):
        result = CliRunner().invoke(cli, ["metadata", "describe", "--help"])
        assert result.exit_code == 0
        assert "write-readiness" in result.output.lower()


class TestPicklistMetaOptions:
    """`metadata picklist` JSON mode flattens options to `meta.options` (#76)."""

    def _stub(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    def _picklist_url(self, backend) -> str:
        return backend.url_for(
            "EntityDefinitions(LogicalName='account')"
            "/Attributes(LogicalName='industrycode')"
            "/Microsoft.Dynamics.CRM.PicklistAttributeMetadata"
        )

    def test_local_picklist_meta_options_from_optionset(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        raw = {
            "LogicalName": "industrycode",
            "OptionSet": {"Options": [_opt(1, "Accounting"), _opt(2, "Retail")]},
            "GlobalOptionSet": None,
        }
        with requests_mock.Mocker() as m:
            m.get(self._picklist_url(backend), json=raw)
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "picklist", "account", "industrycode"]
            )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["meta"]["options"] == [
            {"value": 1, "label": "Accounting"},
            {"value": 2, "label": "Retail"},
        ]
        # Raw data is untouched — no contract break.
        assert env["data"]["OptionSet"]["Options"] == raw["OptionSet"]["Options"]
        assert env["data"]["GlobalOptionSet"] is None

    def test_global_bound_picklist_meta_options_from_fallback(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        # OptionSet null/empty → options live under GlobalOptionSet.
        raw = {
            "LogicalName": "industrycode",
            "OptionSet": None,
            "GlobalOptionSet": {"Options": [_opt(10, "Low"), _opt(20, "High")]},
        }
        with requests_mock.Mocker() as m:
            m.get(self._picklist_url(backend), json=raw)
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "picklist", "account", "industrycode"]
            )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["meta"]["options"] == [
            {"value": 10, "label": "Low"},
            {"value": 20, "label": "High"},
        ]
        assert env["data"]["GlobalOptionSet"]["Options"] == raw["GlobalOptionSet"]["Options"]


class TestGetOptionsetMetaOptions:
    """`metadata get-optionset` JSON mode flattens root Options (#76)."""

    def _stub(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    def test_get_optionset_meta_options_from_root(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        # Label carried via LocalizedLabels (no UserLocalizedLabel) — the robust
        # fallback path must still resolve it.
        raw = {
            "Name": "new_priority",
            "Options": [
                {"Value": 1, "Label": {"LocalizedLabels": [{"Label": "Low"}]}},
                {"Value": 2, "Label": {"LocalizedLabels": [{"Label": "High"}]}},
            ],
        }
        with requests_mock.Mocker() as m:
            m.get(
                backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')"),
                json=raw,
            )
            result = CliRunner().invoke(
                cli, ["--json", "metadata", "get-optionset", "new_priority"]
            )
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["meta"]["options"] == [
            {"value": 1, "label": "Low"},
            {"value": 2, "label": "High"},
        ]
        # Raw data is untouched.
        assert env["data"]["Options"] == raw["Options"]
