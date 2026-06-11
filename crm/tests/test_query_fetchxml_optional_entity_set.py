# pyright: basic
"""Tests for `query fetchxml` with optional ENTITY_SET positional (#202).

Behaviors under test:
- resolver: LogicalName → EntitySetName via metadata GET
- backward compat: positional provided → zero extra GETs
- derived path: no positional → parse XML name + resolve → query
- error: no positional + unparseable / missing name → exit 2
- error: no positional + unknown logical name → D365Error (exit 1)
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.core.metadata import resolve_entity_set_name
from crm.utils.d365_backend import D365Error


# ── Resolver unit tests ─────────────────────────────────────────────────────

class _ResolverBackend:
    """Records calls and returns configured responses per path."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.calls: list[str] = []
        self._responses = responses

    def get(self, path: str, *, params: Any = None, **_kw: Any) -> Any:
        self.calls.append(path)
        if path in self._responses:
            return self._responses[path]
        raise AssertionError(f"Unexpected backend.get call: {path!r}")


_ENTITY_DEF_PATH = "EntityDefinitions(LogicalName='account')"


def test_resolve_entity_set_name_returns_set_name():
    backend = _ResolverBackend({
        _ENTITY_DEF_PATH: {"EntitySetName": "accounts", "LogicalName": "account"},
    })
    result = resolve_entity_set_name(backend, "account")  # type: ignore[arg-type]
    assert result == "accounts"
    assert backend.calls == [_ENTITY_DEF_PATH]


def test_resolve_entity_set_name_raises_on_missing():
    """Server returns a record without EntitySetName → D365Error."""
    backend = _ResolverBackend({
        _ENTITY_DEF_PATH: {"LogicalName": "account"},  # EntitySetName absent
    })
    with pytest.raises(D365Error):
        resolve_entity_set_name(backend, "account")  # type: ignore[arg-type]


def test_resolve_entity_set_name_propagates_d365_error():
    """Server returns 404 → D365Error (backend raises, not swallowed)."""
    class _ErrorBackend:
        def get(self, *_a: Any, **_kw: Any) -> Any:
            raise D365Error("Not found", status=404)

    with pytest.raises(D365Error) as exc_info:
        resolve_entity_set_name(_ErrorBackend(), "account")  # type: ignore[arg-type]
    assert exc_info.value.status == 404


# ── Helpers ─────────────────────────────────────────────────────────────────

_FETCH_ACCOUNT = "<fetch><entity name=\"account\"><attribute name=\"name\"/></entity></fetch>"
_FETCH_NO_NAME = "<fetch><entity><attribute name=\"name\"/></entity></fetch>"
_FETCH_NO_ENTITY = "<fetch><attribute name=\"name\"/></fetch>"
_NOT_XML = "this is not xml <<<"


def _make_backend(monkeypatch: pytest.MonkeyPatch, calls: list[str] | None = None) -> MagicMock:
    """Stub backend that records paths and returns empty value arrays."""
    mock = MagicMock()
    recorded: list[str] = calls if calls is not None else []

    def _get(path: str, **_kw: Any) -> Any:
        recorded.append(path)
        if "EntityDefinitions" in path:
            return {"EntitySetName": "accounts", "LogicalName": "account"}
        return {"value": []}

    mock.get.side_effect = _get
    monkeypatch.setattr(CLIContext, "backend", lambda self: mock)
    return mock


# ── Backward-compat: positional provided → NO extra GET ─────────────────────

def test_positional_provided_no_extra_metadata_get(monkeypatch: pytest.MonkeyPatch):
    """When ENTITY_SET is given, only the FetchXML query GET fires — no resolver call."""
    calls: list[str] = []
    _make_backend(monkeypatch, calls)
    result = CliRunner().invoke(
        cli,
        ["--json", "query", "fetchxml", "accounts", "--xml", _FETCH_ACCOUNT],
    )
    assert result.exit_code == 0, result.output
    # Exactly one GET: the fetchxml query itself (path == "accounts")
    entity_set_calls = [c for c in calls if c == "accounts"]
    metadata_calls = [c for c in calls if "EntityDefinitions" in c]
    assert len(entity_set_calls) == 1
    assert len(metadata_calls) == 0, f"unexpected metadata GETs: {metadata_calls}"


# ── Derived path: no positional → parse + resolve → query ───────────────────

def test_no_positional_derives_entity_set(monkeypatch: pytest.MonkeyPatch):
    """Omitting ENTITY_SET derives it from XML name → one resolver GET + one query GET."""
    calls: list[str] = []
    _make_backend(monkeypatch, calls)
    result = CliRunner().invoke(
        cli,
        ["--json", "query", "fetchxml", "--xml", _FETCH_ACCOUNT],
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is True
    # Resolver was called
    resolver_calls = [c for c in calls if "EntityDefinitions" in c]
    assert len(resolver_calls) == 1
    assert "account" in resolver_calls[0]
    # Actual query used the resolved set name
    query_calls = [c for c in calls if c == "accounts"]
    assert len(query_calls) == 1


def test_no_positional_result_uses_resolved_name(monkeypatch: pytest.MonkeyPatch):
    """The resolved entity set name flows into the result envelope's entity_set field."""
    _make_backend(monkeypatch)
    result = CliRunner().invoke(
        cli,
        ["--json", "query", "fetchxml", "--xml", _FETCH_ACCOUNT],
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is True
    # entity_set in meta should reflect the resolved name, not None
    assert env.get("meta", {}).get("entity_set") == "accounts"


# ── Error paths ──────────────────────────────────────────────────────────────

def test_no_positional_unparseable_xml_exit2(monkeypatch: pytest.MonkeyPatch):
    """Unparseable XML with no positional → exit 2 (UsageError)."""
    _make_backend(monkeypatch)
    result = CliRunner().invoke(
        cli,
        ["--json", "query", "fetchxml", "--xml", _NOT_XML],
    )
    assert result.exit_code == 2, result.output


def test_no_positional_missing_entity_name_exit2(monkeypatch: pytest.MonkeyPatch):
    """<entity> without name= attribute → exit 2 (UsageError)."""
    _make_backend(monkeypatch)
    result = CliRunner().invoke(
        cli,
        ["--json", "query", "fetchxml", "--xml", _FETCH_NO_NAME],
    )
    assert result.exit_code == 2, result.output


def test_no_positional_no_entity_element_exit2(monkeypatch: pytest.MonkeyPatch):
    """<fetch> without <entity> child → exit 2 (UsageError)."""
    _make_backend(monkeypatch)
    result = CliRunner().invoke(
        cli,
        ["--json", "query", "fetchxml", "--xml", _FETCH_NO_ENTITY],
    )
    assert result.exit_code == 2, result.output


def test_no_positional_unknown_logical_name_exit1(monkeypatch: pytest.MonkeyPatch):
    """Resolver raises D365Error (404) → exit 1 clean error envelope."""
    class _404Backend:
        def get(self, path: str, **_kw: Any) -> Any:
            if "EntityDefinitions" in path:
                raise D365Error("Not found", status=404)
            return {"value": []}

    monkeypatch.setattr(CLIContext, "backend", lambda self: _404Backend())
    result = CliRunner().invoke(
        cli,
        ["--json", "query", "fetchxml", "--xml", _FETCH_ACCOUNT],
    )
    assert result.exit_code == 1, result.output
    env = json.loads(result.output)
    assert env["ok"] is False


def test_error_message_names_both_remedies(monkeypatch: pytest.MonkeyPatch):
    """UsageError message on missing name tells user BOTH fixes."""
    _make_backend(monkeypatch)
    result = CliRunner().invoke(
        cli,
        ["query", "fetchxml", "--xml", _FETCH_NO_NAME],
    )
    assert result.exit_code == 2
    # Message should mention passing the positional AND the name= attribute
    output = result.output.lower()
    assert "entity_set" in output or "positional" in output or "entity set" in output
    assert "name=" in output or "name attribute" in output or "<entity" in output
