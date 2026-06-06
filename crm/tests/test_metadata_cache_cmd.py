# pyright: basic
"""Tests for --cache-metadata / --refresh-metadata flags and metadata cache-clear command (#88)."""
from __future__ import annotations

import json

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import CLIContext, cli
from crm.utils.d365_backend import ConnectionProfile, D365Backend


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
def backend(profile: ConnectionProfile) -> D365Backend:
    return D365Backend(profile, password="pw", dry_run=False)


# Two-field response — used by the cache path (EntityDefinitions?$select=LogicalName,EntitySetName)
_ENTITY_DEFS_2F = {
    "value": [
        {"LogicalName": "account", "EntitySetName": "accounts"},
        {"LogicalName": "contact", "EntitySetName": "contacts"},
    ]
}

# Five-field response — used by the non-cache path
_ENTITY_DEFS_5F = {
    "value": [
        {
            "LogicalName": "account",
            "EntitySetName": "accounts",
            "SchemaName": "Account",
            "IsCustomEntity": False,
            "DisplayName": {"UserLocalizedLabel": {"Label": "Account"}},
        },
    ]
}


def _stub_backend(monkeypatch, backend: D365Backend) -> None:
    monkeypatch.setattr(CLIContext, "backend", lambda self: backend)


def _invoke(args: list[str]):
    return CliRunner().invoke(cli, args)


# ---------------------------------------------------------------------------
# Test 1: cold miss — first fetch hits the network, result stored
# ---------------------------------------------------------------------------

class TestCacheMiss:
    def test_cold_miss_returns_2field_rows(self, monkeypatch, tmp_path, backend: D365Backend) -> None:
        monkeypatch.setenv("CRM_HOME", str(tmp_path))
        _stub_backend(monkeypatch, backend)
        url = backend.url_for("EntityDefinitions")
        with requests_mock.Mocker() as m:
            m.get(url, json=_ENTITY_DEFS_2F)
            result = _invoke(["--json", "--cache-metadata", "metadata", "entities"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert env["meta"]["cache"] == "miss"
        assert env["data"] == [
            {"logical": "account", "set_name": "accounts"},
            {"logical": "contact", "set_name": "contacts"},
        ]


# ---------------------------------------------------------------------------
# Test 2: second call hits disk, NOT the network
# ---------------------------------------------------------------------------

class TestCacheHit:
    def test_second_call_is_disk_hit(self, monkeypatch, tmp_path, backend: D365Backend) -> None:
        monkeypatch.setenv("CRM_HOME", str(tmp_path))
        _stub_backend(monkeypatch, backend)
        url = backend.url_for("EntityDefinitions")
        with requests_mock.Mocker() as m:
            m.get(url, json=_ENTITY_DEFS_2F)
            # First invoke — cold miss
            r1 = _invoke(["--json", "--cache-metadata", "metadata", "entities"])
            assert r1.exit_code == 0, r1.output
            e1 = json.loads(r1.output)
            assert e1["meta"]["cache"] == "miss"

            # Second invoke — must be a disk hit; network call count must still be 1
            r2 = _invoke(["--json", "--cache-metadata", "metadata", "entities"])
            assert r2.exit_code == 0, r2.output
            e2 = json.loads(r2.output)
            assert e2["meta"]["cache"] == "hit"
            assert m.call_count == 1, f"Expected 1 network call, got {m.call_count}"


# ---------------------------------------------------------------------------
# Test 3: --refresh-metadata forces a network fetch even when cache is warm
# ---------------------------------------------------------------------------

class TestRefreshMetadata:
    def test_refresh_hits_network(self, monkeypatch, tmp_path, backend: D365Backend) -> None:
        monkeypatch.setenv("CRM_HOME", str(tmp_path))
        _stub_backend(monkeypatch, backend)
        url = backend.url_for("EntityDefinitions")
        with requests_mock.Mocker() as m:
            m.get(url, json=_ENTITY_DEFS_2F)
            # Warm the cache first
            r1 = _invoke(["--json", "--cache-metadata", "metadata", "entities"])
            assert r1.exit_code == 0
            assert json.loads(r1.output)["meta"]["cache"] == "miss"

            # Force refresh
            r2 = _invoke(["--json", "--refresh-metadata", "metadata", "entities"])
            assert r2.exit_code == 0, r2.output
            e2 = json.loads(r2.output)
            assert e2["meta"]["cache"] == "refreshed"
            # Network should have been called exactly twice (miss + refresh)
            assert m.call_count == 2, f"Expected 2 network calls, got {m.call_count}"


# ---------------------------------------------------------------------------
# Test 4: default path (no cache flags) — unchanged 5-field behavior
# ---------------------------------------------------------------------------

class TestDefaultPath:
    def test_no_cache_flag_uses_5field_path(self, monkeypatch, tmp_path, backend: D365Backend) -> None:
        monkeypatch.setenv("CRM_HOME", str(tmp_path))
        _stub_backend(monkeypatch, backend)
        url = backend.url_for("EntityDefinitions")
        with requests_mock.Mocker() as m:
            m.get(url, json=_ENTITY_DEFS_5F)
            result = _invoke(["--json", "metadata", "entities"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        # No cache key in meta
        assert "cache" not in env.get("meta", {})
        # 5-field path: rows carry SchemaName
        assert "SchemaName" in env["data"][0]


# ---------------------------------------------------------------------------
# Test 5: --custom-only + --cache-metadata is a usage error (exit 2)
# ---------------------------------------------------------------------------

class TestCustomOnlyConflict:
    def test_custom_only_with_cache_exits_2(self, monkeypatch, tmp_path, backend: D365Backend) -> None:
        monkeypatch.setenv("CRM_HOME", str(tmp_path))
        _stub_backend(monkeypatch, backend)
        result = _invoke([
            "--json", "--cache-metadata", "metadata", "entities", "--custom-only"
        ])
        assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Test 6: --top with cache path
# ---------------------------------------------------------------------------

class TestTopWithCache:
    def test_top_slices_cached_results(self, monkeypatch, tmp_path, backend: D365Backend) -> None:
        monkeypatch.setenv("CRM_HOME", str(tmp_path))
        _stub_backend(monkeypatch, backend)
        url = backend.url_for("EntityDefinitions")
        with requests_mock.Mocker() as m:
            m.get(url, json=_ENTITY_DEFS_2F)
            result = _invoke([
                "--json", "--cache-metadata", "metadata", "entities", "--top", "1"
            ])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert "cache" in env["meta"]
        assert env["meta"]["count"] == 1
        assert len(env["data"]) == 1


# ---------------------------------------------------------------------------
# Test 7: cache-clear command
# ---------------------------------------------------------------------------

class TestCacheClear:
    def test_clear_removes_cache_then_false_on_second(
        self, monkeypatch, tmp_path, backend: D365Backend
    ) -> None:
        monkeypatch.setenv("CRM_HOME", str(tmp_path))
        _stub_backend(monkeypatch, backend)
        url = backend.url_for("EntityDefinitions")

        # Seed the cache
        with requests_mock.Mocker() as m:
            m.get(url, json=_ENTITY_DEFS_2F)
            r = _invoke(["--json", "--cache-metadata", "metadata", "entities"])
            assert r.exit_code == 0

        # First clear — should say cleared: true
        r1 = _invoke(["--json", "metadata", "cache-clear"])
        assert r1.exit_code == 0, r1.output
        e1 = json.loads(r1.output)
        assert e1["ok"] is True
        assert e1["data"]["cleared"] is True

        # Second clear — nothing left, should say cleared: false
        r2 = _invoke(["--json", "metadata", "cache-clear"])
        assert r2.exit_code == 0, r2.output
        e2 = json.loads(r2.output)
        assert e2["ok"] is True
        assert e2["data"]["cleared"] is False


# ---------------------------------------------------------------------------
# Test 8: env-driven opt-in via CRM_CACHE_METADATA=1
# ---------------------------------------------------------------------------

class TestEnvDriven:
    def test_env_var_opts_in_cache(self, monkeypatch, tmp_path, backend: D365Backend) -> None:
        monkeypatch.setenv("CRM_HOME", str(tmp_path))
        monkeypatch.setenv("CRM_CACHE_METADATA", "1")
        _stub_backend(monkeypatch, backend)
        url = backend.url_for("EntityDefinitions")
        with requests_mock.Mocker() as m:
            m.get(url, json=_ENTITY_DEFS_2F)
            result = _invoke(["--json", "metadata", "entities"])
        assert result.exit_code == 0, result.output
        env = json.loads(result.output)
        assert env["ok"] is True
        assert "cache" in env["meta"]
