"""entity update against a metadata set surfaces a metadata-command hint (#146d)."""
# pyright: basic
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from crm.cli import cli, CLIContext
from crm.utils.d365_backend import D365Error


class StubBackend:
    """Minimal backend stub that raises D365Error on patch."""
    def patch(self, *args, **kw):
        raise D365Error('Operation not supported on EntityMetadata', status=400, code='0x0')


def test_entitydefinitions_update_failure_hints_metadata_command(monkeypatch):
    monkeypatch.setattr(CLIContext, "backend", lambda self: StubBackend())
    runner = CliRunner()
    record_id = "550e8400-e29b-41d4-a716-446655440000"
    result = runner.invoke(cli, [
        "--json", "entity", "update", "EntityDefinitions", record_id,
        "--data", '{"DisplayName": "X"}',
    ])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "metadata update-entity" in payload["error"]


def test_globaloptionsetdefinitions_update_failure_hints_update_optionset(monkeypatch):
    monkeypatch.setattr(CLIContext, "backend", lambda self: StubBackend())
    runner = CliRunner()
    record_id = "550e8400-e29b-41d4-a716-446655440000"
    result = runner.invoke(cli, [
        "--json", "entity", "update", "GlobalOptionSetDefinitions", record_id,
        "--data", '{"DisplayName": "X"}',
    ])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "metadata update-optionset" in payload["error"]
    assert "metadata update-entity" not in payload["error"]


def test_normal_entity_update_failure_has_no_hint(monkeypatch):
    monkeypatch.setattr(CLIContext, "backend", lambda self: StubBackend())
    runner = CliRunner()
    record_id = "550e8400-e29b-41d4-a716-446655440000"
    result = runner.invoke(cli, [
        "--json", "entity", "update", "accounts", record_id,
        "--data", '{"name": "X"}',
    ])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "metadata update-entity" not in payload["error"]
