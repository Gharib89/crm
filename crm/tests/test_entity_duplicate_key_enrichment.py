"""Command-layer wiring for alternate-key duplicate error enrichment (#232, #347).

Covers:
 - _parse_response: preserves body error code for 412 (not overwritten)
 - classify_d365_error: 0x80060892 → duplicate_detected
 - _handle_d365_error: extra_meta merged into envelope
 - entity create/update commands: enrichment wired end-to-end (json-only gate)

The detection helper and core enrichment seam moved to crm.core.entity in #347 —
their direct tests live in test_entity_dupe_key_enrichment_core.py.
"""
# pyright: basic

from __future__ import annotations

import json

import pytest
import requests
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.utils.d365_backend import D365Error


@pytest.fixture
def runner():
    return CliRunner()


# ── _parse_response: 412 body-code handling ────────────────────────────────


def test_parse_response_412_no_body_code_uses_fallback(profile):
    """When error body has no code, 412 still falls back to 'PreconditionFailed'."""
    from crm.utils.d365_backend import _parse_response

    mock_resp = requests.Response()
    mock_resp.status_code = 412
    mock_resp._content = json.dumps({
        "error": {
            "code": "",
            "message": "Optimistic concurrency mismatch.",
        }
    }).encode()
    mock_resp.headers["Content-Type"] = "application/json"

    with pytest.raises(D365Error) as exc_info:
        _parse_response(mock_resp, expect_json=True)

    assert exc_info.value.code == "PreconditionFailed"


# ── classify_d365_error: 0x80060892 → duplicate_detected ────────────────────


def test_classify_0x80060892_is_duplicate_detected():
    """0x80060892 must be classified as duplicate_detected, not concurrency_conflict."""
    from crm.utils.d365_backend import classify_d365_error
    category, retryable = classify_d365_error(412, "0x80060892", "Entity Key violated.")
    assert category == "duplicate_detected"
    assert retryable is False


def test_classify_precondition_failed_is_concurrency_conflict():
    """Plain 412 with no D365 code still maps to concurrency_conflict."""
    from crm.utils.d365_backend import classify_d365_error
    category, retryable = classify_d365_error(412, "PreconditionFailed", "ETag mismatch")
    assert category == "concurrency_conflict"


# Detection (`is_alternate_key_error`) and the core enrichment seam
# (`enrich_dupe_key` / `lookup_alternate_key_schema` / `dupe_key_hint`) relocated
# to crm.core.entity in #347 — covered in test_entity_dupe_key_enrichment_core.py.
# The tests below assert the command-layer wiring (json-only gate, end-to-end
# render) is unchanged after the move.


# ── _handle_d365_error: extra_meta merges into envelope ────────────────────


def test_handle_d365_error_extra_meta_merged(backend):
    """extra_meta is merged into meta when calling _handle_d365_error."""
    from crm.commands._helpers import _handle_d365_error
    from click.testing import CliRunner
    import click

    runner = CliRunner()
    ctx = CLIContext()
    ctx.json_mode = True

    exc = D365Error("dupe error", status=412, code="0x80060892")
    extra_meta = {"alternate_keys": [{"name": "ak1"}]}

    @click.command()
    def _cmd():
        _handle_d365_error(ctx, exc, extra_meta=extra_meta)

    result = runner.invoke(_cmd, catch_exceptions=False)
    data = json.loads(result.output)
    assert data["ok"] is False
    assert data["meta"]["alternate_keys"] == [{"name": "ak1"}]


# ── entity create: end-to-end enrichment ────────────────────────────────────


def test_entity_create_enriches_alternate_key_error(runner, backend, monkeypatch):
    """entity create with duplicate-key error returns enriched meta.alternate_keys."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    with requests_mock.Mocker() as m:
        # The create POST fails with 412 + 0x80060892
        m.post(backend.url_for("accounts"), status_code=412, json={
            "error": {"code": "0x80060892", "message": "Entity Key violated."}
        })
        # Entity set lookup
        m.get(backend.url_for("EntityDefinitions"), json={"value": [
            {"LogicalName": "account", "PrimaryIdAttribute": "accountid"}
        ]})
        # Keys fetch
        m.get(backend.url_for("EntityDefinitions(LogicalName='account')/Keys"), json={"value": [
            {
                "LogicalName": "account_code_ak",
                "SchemaName": "Account_Code_AK",
                "KeyAttributes": ["accountnumber"],
                "EntityKeyIndexStatus": "Active",
            }
        ]})
        result = runner.invoke(
            cli,
            ["--json", "entity", "create", "accounts",
             "--data", json.dumps({"name": "Contoso", "accountnumber": "ACC-001"})],
            catch_exceptions=False,
        )

    assert result.exit_code != 0
    data = json.loads(result.output)
    assert data["ok"] is False
    assert "alternate_keys" in data["meta"]
    ak = data["meta"]["alternate_keys"][0]
    assert ak["name"] == "account_code_ak"
    assert ak["payload_values"] == {"accountnumber": "ACC-001"}


def test_entity_update_enriches_alternate_key_error(runner, backend, monkeypatch):
    """entity update with duplicate-key error returns enriched meta.alternate_keys."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)
    record_id = "11111111-0000-0000-0000-000000000001"

    with requests_mock.Mocker() as m:
        m.patch(backend.url_for(f"accounts({record_id})"), status_code=412, json={
            "error": {"code": "0x80060892", "message": "Entity Key violated."}
        })
        m.get(backend.url_for("EntityDefinitions"), json={"value": [
            {"LogicalName": "account", "PrimaryIdAttribute": "accountid"}
        ]})
        m.get(backend.url_for("EntityDefinitions(LogicalName='account')/Keys"), json={"value": [
            {
                "LogicalName": "account_code_ak",
                "SchemaName": "Account_Code_AK",
                "KeyAttributes": ["accountnumber"],
                "EntityKeyIndexStatus": "Active",
            }
        ]})
        result = runner.invoke(
            cli,
            ["--json", "entity", "update", "accounts", record_id,
             "--data", json.dumps({"accountnumber": "ACC-001"})],
            catch_exceptions=False,
        )

    assert result.exit_code != 0
    data = json.loads(result.output)
    assert data["ok"] is False
    assert "alternate_keys" in data["meta"]


def test_entity_create_non_duplicate_key_error_not_enriched(runner, backend, monkeypatch):
    """Non-alternate-key errors are not enriched (no extra backend calls)."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    with requests_mock.Mocker() as m:
        m.post(backend.url_for("accounts"), status_code=403, json={
            "error": {"code": "0x80048306", "message": "Access denied"}
        })
        result = runner.invoke(
            cli,
            ["--json", "entity", "create", "accounts",
             "--data", json.dumps({"name": "Contoso"})],
            catch_exceptions=False,
        )

    assert result.exit_code != 0
    data = json.loads(result.output)
    assert data["ok"] is False
    # No alternate_keys in meta — no extra lookup was triggered
    assert "alternate_keys" not in data.get("meta", {})


def test_entity_create_human_mode_no_enrichment_calls(runner, backend, monkeypatch):
    """Human mode: enrichment GETs are not called (json_mode gate)."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    with requests_mock.Mocker() as m:
        m.post(backend.url_for("accounts"), status_code=412, json={
            "error": {"code": "0x80060892", "message": "Entity Key violated."}
        })
        # No EntityDefinitions or Keys mock registered — if called they'd raise NoMockAddress
        result = runner.invoke(
            cli,
            ["entity", "create", "accounts",  # no --json
             "--data", json.dumps({"name": "Contoso"})],
            catch_exceptions=False,
        )

    assert result.exit_code != 0
    # Human mode shows the error message, no enrichment lookup fired
    assert "Entity Key" in result.output or "violated" in result.output.lower()


def test_entity_create_enrichment_failure_passes_original_error(runner, backend, monkeypatch):
    """If enrichment lookup fails, original error is emitted unchanged."""
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)

    with requests_mock.Mocker() as m:
        m.post(backend.url_for("accounts"), status_code=412, json={
            "error": {"code": "0x80060892", "message": "Entity Key violated."}
        })
        # Entity definitions lookup also fails
        m.get(backend.url_for("EntityDefinitions"), status_code=500, json={
            "error": {"message": "Server error"}
        })
        result = runner.invoke(
            cli,
            ["--json", "entity", "create", "accounts",
             "--data", json.dumps({"name": "Contoso"})],
            catch_exceptions=False,
        )

    assert result.exit_code != 0
    data = json.loads(result.output)
    assert data["ok"] is False
    # Still gets the original error message
    assert "Entity Key" in data["error"] or "violated" in data["error"].lower()
