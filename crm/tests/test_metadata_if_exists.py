"""Idempotency tests for --if-exists on the five metadata create commands.

Covers the core create functions (existence probe + skip/error semantics +
dry-run preview) and the CLI exit-code contract. All HTTP mocked via
requests_mock; no live D365 server.
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error
from crm.core import metadata as meta_mod
from crm.core import metadata_attrs as ma_mod
from crm.core import optionsets as os_mod
from crm.core import relationships as rel_mod


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


@pytest.fixture
def dry_backend(profile):
    return D365Backend(profile, password="pw", dry_run=True)


def _posts(m) -> list:
    return [r for r in m.request_history if r.method == "POST"]


# ── create-entity ────────────────────────────────────────────────────────


class TestCreateEntityIfExists:
    def test_exists_skip_no_post(self, backend):
        probe = backend.url_for("EntityDefinitions(LogicalName='new_widget')")
        with requests_mock.Mocker() as m:
            m.get(probe, json={"LogicalName": "new_widget", "EntitySetName": "new_widgets"})
            info = meta_mod.create_entity(
                backend, schema_name="new_Widget", display_name="Widget",
                if_exists="skip",
            )
        assert info.get("skipped") is True
        assert info.get("exists") is True
        assert info["logical_name"] == "new_widget"
        assert info["schema_name"] == "new_Widget"
        assert _posts(m) == []

    def test_exists_error_raises_no_post(self, backend):
        probe = backend.url_for("EntityDefinitions(LogicalName='new_widget')")
        with requests_mock.Mocker() as m:
            m.get(probe, json={"LogicalName": "new_widget"})
            with pytest.raises(D365Error, match="new_widget"):
                meta_mod.create_entity(
                    backend, schema_name="new_Widget", display_name="Widget",
                    if_exists="error",
                )
            assert _posts(m) == []

    def test_not_exists_creates(self, backend):
        probe = backend.url_for("EntityDefinitions(LogicalName='new_widget')")
        md_url = backend.url_for("EntityDefinitions(11111111-1111-1111-1111-111111111111)")
        with requests_mock.Mocker() as m:
            m.get(probe, status_code=404, json={"error": {"code": "0x", "message": "not found"}})
            m.post(backend.url_for("EntityDefinitions"), status_code=204,
                   headers={"OData-EntityId": md_url})
            m.get(md_url, json={"LogicalName": "new_widget", "EntitySetName": "new_widgets"})
            info = meta_mod.create_entity(
                backend, schema_name="new_Widget", display_name="Widget",
                if_exists="error",
            )
        assert info["created"] is True
        assert info["entity_set_name"] == "new_widgets"
        assert len(_posts(m)) == 1

    def test_probe_non_404_reraised(self, backend):
        probe = backend.url_for("EntityDefinitions(LogicalName='new_widget')")
        with requests_mock.Mocker() as m:
            m.get(probe, status_code=500,
                  json={"error": {"code": "0x", "message": "boom"}})
            with pytest.raises(D365Error) as ei:
                meta_mod.create_entity(
                    backend, schema_name="new_Widget", display_name="Widget",
                    if_exists="skip",
                )
            assert ei.value.status == 500
            assert _posts(m) == []

    def test_dry_run_exists(self, dry_backend):
        probe = dry_backend.url_for("EntityDefinitions(LogicalName='new_widget')")
        with requests_mock.Mocker() as m:
            m.get(probe, json={"LogicalName": "new_widget"})
            info = meta_mod.create_entity(
                dry_backend, schema_name="new_Widget", display_name="Widget",
                if_exists="error",
            )
        assert info.get("_dry_run") is True
        assert info.get("_exists") is True
        assert _posts(m) == []

    def test_dry_run_not_exists(self, dry_backend):
        probe = dry_backend.url_for("EntityDefinitions(LogicalName='new_widget')")
        with requests_mock.Mocker() as m:
            m.get(probe, status_code=404, json={"error": {"code": "0x", "message": "nf"}})
            info = meta_mod.create_entity(
                dry_backend, schema_name="new_Widget", display_name="Widget",
                if_exists="error",
            )
        assert info.get("_dry_run") is True
        assert info.get("_exists") is False
        assert _posts(m) == []


# ── add-attribute ──────────────────────────────────────────────────────────


class TestAddAttributeIfExists:
    def test_exists_skip_no_post(self, backend):
        probe = backend.url_for(
            "EntityDefinitions(LogicalName='new_widget')/Attributes(LogicalName='new_amount')"
        )
        with requests_mock.Mocker() as m:
            m.get(probe, json={"LogicalName": "new_amount", "AttributeType": "Money"})
            info = ma_mod.add_attribute(
                backend, entity="new_widget", kind="string",
                schema_name="new_Amount", display_name="Amount",
                max_length=100, if_exists="skip", publish=True,
            )
        assert info.get("skipped") is True
        assert info.get("exists") is True
        assert _posts(m) == []

    def test_exists_error_raises(self, backend):
        probe = backend.url_for(
            "EntityDefinitions(LogicalName='new_widget')/Attributes(LogicalName='new_amount')"
        )
        with requests_mock.Mocker() as m:
            m.get(probe, json={"LogicalName": "new_amount"})
            with pytest.raises(D365Error, match="new_amount"):
                ma_mod.add_attribute(
                    backend, entity="new_widget", kind="string",
                    schema_name="new_Amount", display_name="Amount",
                    max_length=100, if_exists="error",
                )
            assert _posts(m) == []

    def test_not_exists_creates(self, backend):
        probe = backend.url_for(
            "EntityDefinitions(LogicalName='new_widget')/Attributes(LogicalName='new_amount')"
        )
        attr_id = "33333333-3333-3333-3333-333333333333"
        attr_url = backend.url_for(
            f"EntityDefinitions(LogicalName='new_widget')/Attributes({attr_id})"
        )
        with requests_mock.Mocker() as m:
            m.get(probe, status_code=404, json={"error": {"code": "0x", "message": "nf"}})
            m.post(backend.url_for("EntityDefinitions(LogicalName='new_widget')/Attributes"),
                   status_code=204, headers={"OData-EntityId": attr_url})
            m.get(attr_url, json={"LogicalName": "new_amount", "SchemaName": "new_Amount",
                                  "AttributeType": "String"})
            info = ma_mod.add_attribute(
                backend, entity="new_widget", kind="string",
                schema_name="new_Amount", display_name="Amount",
                max_length=100, if_exists="error",
            )
        assert info["created"] is True
        assert len(_posts(m)) == 1


# ── create-optionset ─────────────────────────────────────────────────────


class TestCreateOptionsetIfExists:
    def test_exists_skip_no_post(self, backend):
        probe = backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')")
        with requests_mock.Mocker() as m:
            m.get(probe, json={"Name": "new_priority"})
            info = os_mod.create_optionset(
                backend, name="new_priority", display_name="Priority",
                if_exists="skip", publish=True,
            )
        assert info.get("skipped") is True
        assert info.get("exists") is True
        assert info["name"] == "new_priority"
        assert _posts(m) == []

    def test_exists_error_raises(self, backend):
        probe = backend.url_for("GlobalOptionSetDefinitions(Name='new_priority')")
        with requests_mock.Mocker() as m:
            m.get(probe, json={"Name": "new_priority"})
            with pytest.raises(D365Error, match="new_priority"):
                os_mod.create_optionset(
                    backend, name="new_priority", display_name="Priority",
                    if_exists="error",
                )
            assert _posts(m) == []


# ── create-one-to-many / create-many-to-many ──────────────────────────────


class TestCreateOneToManyIfExists:
    def test_exists_skip_no_post(self, backend):
        probe = backend.url_for("RelationshipDefinitions(SchemaName='new_rel')")
        with requests_mock.Mocker() as m:
            m.get(probe, json={"SchemaName": "new_rel"})
            info = rel_mod.create_one_to_many(
                backend, schema_name="new_rel",
                referenced_entity="account", referencing_entity="new_widget",
                lookup_schema="new_AccountId", lookup_display="Account",
                if_exists="skip", publish=True,
            )
        assert info.get("skipped") is True
        assert info.get("exists") is True
        assert _posts(m) == []

    def test_exists_error_raises(self, backend):
        probe = backend.url_for("RelationshipDefinitions(SchemaName='new_rel')")
        with requests_mock.Mocker() as m:
            m.get(probe, json={"SchemaName": "new_rel"})
            with pytest.raises(D365Error, match="new_rel"):
                rel_mod.create_one_to_many(
                    backend, schema_name="new_rel",
                    referenced_entity="account", referencing_entity="new_widget",
                    lookup_schema="new_AccountId", lookup_display="Account",
                    if_exists="error",
                )
            assert _posts(m) == []


class TestCreateManyToManyIfExists:
    def test_exists_skip_no_post(self, backend):
        probe = backend.url_for("RelationshipDefinitions(SchemaName='new_nn')")
        with requests_mock.Mocker() as m:
            m.get(probe, json={"SchemaName": "new_nn"})
            info = rel_mod.create_many_to_many(
                backend, schema_name="new_nn",
                entity1_logical="account", entity2_logical="new_widget",
                intersect_entity="new_account_widget",
                if_exists="skip", publish=True,
            )
        assert info.get("skipped") is True
        assert _posts(m) == []


# ── CLI exit-code contract ─────────────────────────────────────────────────


class TestCliIfExistsContract:
    def _stub(self, monkeypatch, backend):
        monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    def test_skip_exit_0_ok_true(self, monkeypatch, backend):
        probe = backend.url_for("EntityDefinitions(LogicalName='new_widget')")
        self._stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(probe, json={"LogicalName": "new_widget"})
            result = CliRunner().invoke(cli, [
                "--json", "metadata", "create-entity",
                "--schema-name", "new_Widget", "--display", "Widget",
                "--if-exists", "skip", "--no-publish",
            ])
            assert _posts(m) == []
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True

    def test_default_error_exit_1_ok_false(self, monkeypatch, backend):
        probe = backend.url_for("EntityDefinitions(LogicalName='new_widget')")
        self._stub(monkeypatch, backend)
        with requests_mock.Mocker() as m:
            m.get(probe, json={"LogicalName": "new_widget"})
            result = CliRunner().invoke(cli, [
                "--json", "metadata", "create-entity",
                "--schema-name", "new_Widget", "--display", "Widget",
                "--no-publish",
            ])
            assert _posts(m) == []
        assert result.exit_code == 1, result.output
        env = json.loads(result.output)
        assert env["ok"] is False

    def test_invalid_choice_exit_2(self, monkeypatch, backend):
        self._stub(monkeypatch, backend)
        result = CliRunner().invoke(cli, [
            "--json", "metadata", "create-entity",
            "--schema-name", "new_Widget", "--display", "Widget",
            "--if-exists", "bogus",
        ])
        assert result.exit_code == 2

    @pytest.mark.parametrize("argv", [
        ["metadata", "create-entity", "--help"],
        ["metadata", "add-attribute", "--help"],
        ["metadata", "create-optionset", "--help"],
        ["metadata", "create-one-to-many", "--help"],
        ["metadata", "create-many-to-many", "--help"],
    ])
    def test_if_exists_present_on_all_five(self, argv):
        result = CliRunner().invoke(cli, argv)
        assert result.exit_code == 0
        assert "--if-exists" in result.output
