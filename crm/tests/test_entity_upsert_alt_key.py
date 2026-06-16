"""`entity upsert --key` — upsert by alternate key (#335).

The positional RECORD_ID becomes optional: with --key the record is matched by
an alternate key whose values are read from --data, so the GUID is omitted.
"""
# pyright: basic
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.core import entity as entity_mod
from crm.utils.d365_backend import ConnectionProfile, D365Error

pytestmark = pytest.mark.usefixtures("isolated_home")


class RecordingBackend:
    """Captures the path/body handed to patch()."""

    def __init__(self):
        self.path = None
        self.body = None
        self.profile = ConnectionProfile(
            name="testp", url="https://crm.contoso.local/contoso",
            domain="CONTOSO", username="alice", api_version="v9.2",
        )

    def patch(self, path, *, json_body=None, **_kw):
        self.path = path
        self.body = json_body
        return {}

    def get_collection(self, _path=None, **_kw):
        return []

    def url_for(self, path):
        import urllib.parse
        return urllib.parse.urljoin(self.profile.api_base, path.lstrip("/"))


@pytest.fixture
def runner():
    return CliRunner()


def test_key_with_positional_id_is_usage_error(runner):
    result = runner.invoke(cli, [
        "entity", "upsert", "contacts", "some-id",
        "--key", "emailaddress1", "--data", "{}",
    ])
    assert result.exit_code == 2
    assert "mutually exclusive" in (result.output + result.stderr)


def test_neither_id_nor_key_is_usage_error(runner):
    result = runner.invoke(cli, ["entity", "upsert", "contacts", "--data", "{}"])
    assert result.exit_code == 2


def test_empty_key_is_usage_error(runner):
    result = runner.invoke(cli, [
        "entity", "upsert", "contacts", "--key", ",,,", "--data", "{}",
    ])
    assert result.exit_code == 2
    assert "at least one attribute" in (result.output + result.stderr)


def test_key_patches_alternate_key_path(runner, monkeypatch):
    monkeypatch.setattr(
        entity_mod, "resolve_alternate_key",
        lambda backend, entity_set, attrs: attrs,
    )
    backend = RecordingBackend()
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    payload = json.dumps({"emailaddress1": "joe@x.com", "firstname": "Joe"})
    result = runner.invoke(cli, [
        "--json", "entity", "upsert", "contacts",
        "--key", "emailaddress1", "--data", payload,
    ])
    assert result.exit_code == 0, result.output
    assert backend.path == "contacts(emailaddress1='joe%40x.com')"
    # Key attribute stripped from the body (URL identifies the record).
    assert backend.body == {"firstname": "Joe"}


def test_key_value_missing_from_payload_is_clean_error(runner, monkeypatch):
    monkeypatch.setattr(
        entity_mod, "resolve_alternate_key",
        lambda backend, entity_set, attrs: attrs,
    )
    backend = RecordingBackend()
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    result = runner.invoke(cli, [
        "--json", "entity", "upsert", "contacts",
        "--key", "emailaddress1", "--data", json.dumps({"firstname": "Joe"}),
    ])
    assert result.exit_code == 1
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    assert "emailaddress1" in envelope["error"]


def test_unknown_key_surfaces_clean_envelope(runner, monkeypatch):
    def _boom(backend, entity_set, attrs):
        raise D365Error("No alternate key on 'contacts' matches attribute(s) nope.")

    monkeypatch.setattr(entity_mod, "resolve_alternate_key", _boom)
    backend = RecordingBackend()
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    result = runner.invoke(cli, [
        "--json", "entity", "upsert", "contacts",
        "--key", "nope", "--data", json.dumps({"nope": "x"}),
    ])
    assert result.exit_code == 1
    envelope = json.loads(result.output)
    assert envelope["ok"] is False
    assert "No alternate key" in envelope["error"]


def test_primary_guid_upsert_still_works(runner, monkeypatch):
    """Regression: the positional-GUID path is unchanged when --key is absent."""
    backend = RecordingBackend()
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    guid = "11111111-2222-3333-4444-555555555555"
    result = runner.invoke(cli, [
        "--json", "entity", "upsert", "contacts", guid,
        "--data", json.dumps({"firstname": "Joe"}),
    ])
    assert result.exit_code == 0, result.output
    assert backend.path == f"contacts({guid})"
    assert backend.body == {"firstname": "Joe"}
