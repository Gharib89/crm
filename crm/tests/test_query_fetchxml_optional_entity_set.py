# pyright: basic
"""Tests for `query fetchxml` with optional ENTITY_SET positional (#202).

Behaviors under test:
- resolver: LogicalName → EntitySetName via the entity_names seam (#261)
- backward compat: positional provided → zero resolver round-trips
- derived path: no positional → parse XML name + resolve → query
- error: no positional + unparseable / missing name → exit 2
- error: no positional + unknown logical name → D365Error (exit 1)

`metadata.resolve_entity_set_name` now delegates to the shared `entity_names`
seam (#261), which loads the bidirectional name map read-through from the
metadata cache. So the resolver reads the full ``EntityDefinitions`` collection
via ``get_collection`` (not a per-logical row GET); tests isolate CRM_HOME and
feed the map through ``get_collection``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.core.metadata import resolve_entity_set_name
from crm.utils.d365_backend import ConnectionProfile, D365Error


@pytest.fixture(autouse=True)
def _isolate_crm_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_entity_set_name now reads the name map through the metadata cache
    (#261); isolate CRM_HOME so a real ~/.crm is never touched and each test
    starts with a cold cache."""
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))


def _profile(name: str = "testp") -> ConnectionProfile:
    return ConnectionProfile(
        name=name,
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


# ── Resolver unit tests ─────────────────────────────────────────────────────

class _ResolverBackend:
    """Serves the EntityDefinitions collection through ``get_collection`` (what the
    seam's ``load_name_map`` calls) and records the paths requested."""

    def __init__(self, defs: list[dict[str, Any]]) -> None:
        self.profile = _profile()
        self.calls: list[str] = []
        self._defs = defs

    def get_collection(self, path: str, *, params: Any = None, **_kw: Any) -> list[dict[str, Any]]:
        self.calls.append(path)
        if path == "EntityDefinitions":
            return self._defs
        raise AssertionError(f"Unexpected get_collection call: {path!r}")


def test_resolve_entity_set_name_returns_set_name():
    backend = _ResolverBackend([{"LogicalName": "account", "EntitySetName": "accounts"}])
    result = resolve_entity_set_name(backend, "account")  # type: ignore[arg-type]
    assert result == "accounts"
    # Resolution reads the full collection through the seam, not a per-logical GET.
    assert backend.calls == ["EntityDefinitions"]


def test_resolve_entity_set_name_raises_on_missing():
    """A logical name whose row carries no EntitySetName is not addressable → D365Error."""
    backend = _ResolverBackend([{"LogicalName": "account"}])  # EntitySetName absent
    with pytest.raises(D365Error):
        resolve_entity_set_name(backend, "account")  # type: ignore[arg-type]


def test_resolve_entity_set_name_raises_on_unknown_logical_name():
    """A logical name absent from the org's metadata → D365Error."""
    backend = _ResolverBackend([{"LogicalName": "contact", "EntitySetName": "contacts"}])
    with pytest.raises(D365Error):
        resolve_entity_set_name(backend, "account")  # type: ignore[arg-type]


def test_resolve_entity_set_name_propagates_d365_error():
    """Server returns 404 → D365Error (backend raises, not swallowed)."""
    class _ErrorBackend:
        profile = _profile()

        def get_collection(self, *_a: Any, **_kw: Any) -> Any:
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
    """Stub backend: records paths, resolves names via get_collection, and returns
    empty value arrays for the fetchxml query GET."""
    mock = MagicMock()
    mock.profile = _profile()
    recorded: list[str] = calls if calls is not None else []

    def _get(path: str, **_kw: Any) -> Any:
        recorded.append(path)
        return {"value": []}

    def _get_collection(path: str, **_kw: Any) -> Any:
        recorded.append(path)
        if path == "EntityDefinitions":
            return [{"LogicalName": "account", "EntitySetName": "accounts"}]
        return []

    mock.get.side_effect = _get
    mock.get_collection.side_effect = _get_collection
    monkeypatch.setattr(CLIContext, "backend", lambda self: mock)
    return mock


# ── Backward-compat: positional provided → NO resolver round-trip ───────────

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
    assert len(metadata_calls) == 0, f"unexpected metadata reads: {metadata_calls}"


# ── Derived path: no positional → parse + resolve → query ───────────────────

def test_no_positional_derives_entity_set(monkeypatch: pytest.MonkeyPatch):
    """Omitting ENTITY_SET derives it from XML name → one resolver read + one query GET."""
    calls: list[str] = []
    _make_backend(monkeypatch, calls)
    result = CliRunner().invoke(
        cli,
        ["--json", "query", "fetchxml", "--xml", _FETCH_ACCOUNT],
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is True
    # Resolver read the EntityDefinitions collection exactly once (the seam map).
    resolver_calls = [c for c in calls if "EntityDefinitions" in c]
    assert len(resolver_calls) == 1
    # Actual query used the resolved set name
    query_calls = [c for c in calls if c == "accounts"]
    assert len(query_calls) == 1


def test_no_positional_file_path_derives_entity_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
):
    """--file path with no positional also derives entity set via resolver."""
    xml_file = tmp_path / "query.xml"  # type: ignore[operator]
    xml_file.write_text(_FETCH_ACCOUNT, encoding="utf-8")
    calls: list[str] = []
    _make_backend(monkeypatch, calls)
    result = CliRunner().invoke(
        cli,
        ["--json", "query", "fetchxml", "--file", str(xml_file)],
    )
    assert result.exit_code == 0, result.output
    env = json.loads(result.output)
    assert env["ok"] is True
    assert any("EntityDefinitions" in c for c in calls)
    assert any(c == "accounts" for c in calls)


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
        profile = _profile()

        def get(self, path: str, **_kw: Any) -> Any:
            return {"value": []}

        def get_collection(self, path: str, **_kw: Any) -> Any:
            raise D365Error("Not found", status=404)

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
    # Assert against the specific UsageError text (not Click's usage line).
    # The message must name both fixes: pass ENTITY_SET explicitly + add name=.
    output = result.output.lower()
    assert "pass entity_set explicitly" in output
    assert 'name="' in result.output or "name=" in result.output
