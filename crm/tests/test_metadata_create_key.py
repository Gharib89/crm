"""Unit tests for `crm metadata create-key` / `delete-key` (#350).

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


def _keys_url(backend, entity: str) -> str:
    return backend.url_for(f"EntityDefinitions(LogicalName='{entity}')/Keys")


def _key_url(backend, entity: str, key_logical: str) -> str:
    return backend.url_for(
        f"EntityDefinitions(LogicalName='{entity}')/Keys(LogicalName='{key_logical}')"
    )


# ── core: create_entity_key ────────────────────────────────────────────────


def test_create_entity_key_posts_and_returns_created(backend):
    """Happy path: POST to the Keys collection, return a created dict."""
    with requests_mock.Mocker() as m:
        # existence probe → absent
        m.get(_key_url(backend, "account", "new_code"), status_code=404,
              json={"error": {"code": "0x0", "message": "Not found"}})
        post = m.post(
            _keys_url(backend, "account"), status_code=204,
            headers={"OData-EntityId":
                     backend.url_for("EntityDefinitions(LogicalName='account')/Keys"
                                     "(11111111-1111-1111-1111-111111111111)")},
        )
        result = meta_mod.create_entity_key(
            backend, entity="account", schema_name="new_Code",
            key_attributes=["accountnumber"],
        )
    assert post.called
    body = post.last_request.json()
    assert body["@odata.type"] == "Microsoft.Dynamics.CRM.EntityKeyMetadata"
    assert body["SchemaName"] == "new_Code"
    assert body["LogicalName"] == "new_code"
    assert body["KeyAttributes"] == ["accountnumber"]
    assert result["created"] is True
    assert result["entity"] == "account"
    assert result["logical_name"] == "new_code"
    assert result["key_attributes"] == ["accountnumber"]


def test_create_entity_key_composite(backend):
    """A multi-attribute (composite) key sends all attributes."""
    with requests_mock.Mocker() as m:
        m.get(_key_url(backend, "contact", "new_nameemail"), status_code=404,
              json={"error": {"code": "0x0", "message": "Not found"}})
        post = m.post(_keys_url(backend, "contact"), status_code=204,
                      headers={"OData-EntityId": "x"})
        meta_mod.create_entity_key(
            backend, entity="contact", schema_name="new_NameEmail",
            key_attributes=["firstname", "emailaddress1"],
        )
    assert post.last_request.json()["KeyAttributes"] == ["firstname", "emailaddress1"]


def test_create_entity_key_display_defaults_to_schema(backend):
    """Without --display the DisplayName label falls back to the schema name."""
    with requests_mock.Mocker() as m:
        m.get(_key_url(backend, "account", "new_code"), status_code=404,
              json={"error": {"code": "0x0", "message": "Not found"}})
        post = m.post(_keys_url(backend, "account"), status_code=204,
                      headers={"OData-EntityId": "x"})
        meta_mod.create_entity_key(
            backend, entity="account", schema_name="new_Code",
            key_attributes=["accountnumber"],
        )
    labels = post.last_request.json()["DisplayName"]["LocalizedLabels"]
    assert labels[0]["Label"] == "new_Code"


def test_create_entity_key_solution_header(backend):
    """--solution is sent as the MSCRM.SolutionUniqueName header."""
    with requests_mock.Mocker() as m:
        m.get(_key_url(backend, "account", "new_code"), status_code=404,
              json={"error": {"code": "0x0", "message": "Not found"}})
        post = m.post(_keys_url(backend, "account"), status_code=204,
                      headers={"OData-EntityId": "x"})
        meta_mod.create_entity_key(
            backend, entity="account", schema_name="new_Code",
            key_attributes=["accountnumber"], solution="mysol",
        )
    assert post.last_request.headers["MSCRM.SolutionUniqueName"] == "mysol"


def test_create_entity_key_dry_run_does_not_post(dry_backend):
    """Dry-run returns a preview and issues no POST (the probe GET still runs)."""
    with requests_mock.Mocker() as m:
        m.get(_key_url(dry_backend, "account", "new_code"), status_code=404,
              json={"error": {"code": "0x0", "message": "Not found"}})
        result = meta_mod.create_entity_key(
            dry_backend, entity="account", schema_name="new_Code",
            key_attributes=["accountnumber"],
        )
    assert result["_dry_run"] is True
    assert result["method"] == "POST"
    assert "Keys" in result["url"]


def test_create_entity_key_exists_error(backend):
    """if_exists=error (default) raises when the key already exists."""
    with requests_mock.Mocker() as m:
        m.get(_key_url(backend, "account", "new_code"), status_code=200,
              json={"MetadataId": "1"})
        with pytest.raises(D365Error) as exc:
            meta_mod.create_entity_key(
                backend, entity="account", schema_name="new_Code",
                key_attributes=["accountnumber"],
            )
    assert exc.value.code == "AlreadyExists"


def test_create_entity_key_exists_skip(backend):
    """if_exists=skip returns a no-op success and never POSTs."""
    with requests_mock.Mocker() as m:
        m.get(_key_url(backend, "account", "new_code"), status_code=200,
              json={"MetadataId": "1"})
        result = meta_mod.create_entity_key(
            backend, entity="account", schema_name="new_Code",
            key_attributes=["accountnumber"], if_exists="skip",
        )
    assert result["skipped"] is True
    assert result["exists"] is True


def test_create_entity_key_requires_prefix(backend):
    """A schema name without a publisher prefix is rejected before any call."""
    with requests_mock.Mocker():
        with pytest.raises(D365Error):
            meta_mod.create_entity_key(
                backend, entity="account", schema_name="Code",
                key_attributes=["accountnumber"],
            )


def test_create_entity_key_requires_attributes(backend):
    """An empty key-attribute list is rejected before any call."""
    with requests_mock.Mocker():
        with pytest.raises(D365Error):
            meta_mod.create_entity_key(
                backend, entity="account", schema_name="new_Code",
                key_attributes=[],
            )


# ── core: delete_entity_key ────────────────────────────────────────────────


def test_delete_entity_key_deletes(backend):
    """Happy path: DELETE the key by logical name, return deleted dict."""
    with requests_mock.Mocker() as m:
        d = m.delete(_key_url(backend, "account", "new_code"), status_code=204)
        result = meta_mod.delete_entity_key(backend, "account", "new_code")
    assert d.called
    assert result["deleted"] is True
    assert result["entity"] == "account"
    assert result["key"] == "new_code"


def test_delete_entity_key_lowercases_schema_name(backend):
    """A schema-name argument is lower-cased to address the collection."""
    with requests_mock.Mocker() as m:
        d = m.delete(_key_url(backend, "account", "new_code"), status_code=204)
        result = meta_mod.delete_entity_key(backend, "account", "New_Code")
    assert d.called
    assert result["key"] == "new_code"


def test_delete_entity_key_dry_run(dry_backend):
    """Dry-run returns a would_delete preview and issues no DELETE."""
    with requests_mock.Mocker():
        result = meta_mod.delete_entity_key(dry_backend, "account", "new_code")
    assert result["_dry_run"] is True
    assert result["would_delete"] is True


# ── command: crm metadata create-key / delete-key ──────────────────────────


@pytest.fixture
def runner():
    return CliRunner()


def test_cmd_create_key_json(runner, backend, monkeypatch):
    """create-key emits ok=true with the created key in JSON mode."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    with requests_mock.Mocker() as m:
        m.get(_key_url(backend, "account", "new_code"), status_code=404,
              json={"error": {"code": "0x0", "message": "Not found"}})
        m.post(_keys_url(backend, "account"), status_code=204,
               headers={"OData-EntityId": "x"})
        result = runner.invoke(
            cli, ["--json", "metadata", "create-key", "account",
                  "--name", "new_Code", "--key-attributes", "accountnumber",
                  "--no-publish"],
            catch_exceptions=False)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["data"]["created"] is True
    assert data["data"]["key_attributes"] == ["accountnumber"]


def test_cmd_create_key_splits_comma_attributes(runner, backend, monkeypatch):
    """--key-attributes is split on commas into a list."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    with requests_mock.Mocker() as m:
        m.get(_key_url(backend, "contact", "new_nameemail"), status_code=404,
              json={"error": {"code": "0x0", "message": "Not found"}})
        post = m.post(_keys_url(backend, "contact"), status_code=204,
                      headers={"OData-EntityId": "x"})
        result = runner.invoke(
            cli, ["--json", "metadata", "create-key", "contact",
                  "--name", "new_NameEmail",
                  "--key-attributes", "firstname, emailaddress1",
                  "--no-publish"],
            catch_exceptions=False)
    assert result.exit_code == 0
    assert post.last_request.json()["KeyAttributes"] == ["firstname", "emailaddress1"]


def test_cmd_delete_key_json(runner, backend, monkeypatch):
    """delete-key with --yes emits ok=true and deletes."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    with requests_mock.Mocker() as m:
        m.delete(_key_url(backend, "account", "new_code"), status_code=204)
        result = runner.invoke(
            cli, ["--json", "metadata", "delete-key", "account", "new_code",
                  "--yes"],
            catch_exceptions=False)
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["data"]["deleted"] is True
