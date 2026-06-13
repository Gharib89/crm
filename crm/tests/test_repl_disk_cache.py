# pyright: basic
"""Tests for MetadataCache integration with the persistent on-disk entity-definition cache."""
from __future__ import annotations

import requests_mock as rm_module

from crm.core import metadata_cache as mc_mod
from crm.commands.repl import MetadataCache

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ENTITY_RESPONSE = {
    "value": [
        {"LogicalName": "account", "EntitySetName": "accounts"},
        {"LogicalName": "contact", "EntitySetName": "contacts"},
    ]
}


# ---------------------------------------------------------------------------
# Test 1: cold cache (empty CRM_HOME) — network hit, disk file created
# ---------------------------------------------------------------------------

class TestColdCacheWritesToDisk:
    def test_logical_names_and_set_names(
        self, monkeypatch, tmp_path, profile, backend
    ) -> None:
        monkeypatch.setenv("CRM_HOME", str(tmp_path))
        url = backend.url_for("EntityDefinitions")
        with rm_module.Mocker() as m:
            m.get(url, json=_ENTITY_RESPONSE)
            cache = MetadataCache(use_cache=True)
            logical = cache.logical_names(backend)
            sets = cache.set_names(backend)

        assert logical == ["account", "contact"]
        assert sets == ["accounts", "contacts"]
        assert mc_mod.cache_file(backend.profile).exists()


# ---------------------------------------------------------------------------
# Test 2: disk hit across instances — second instance must NOT hit the network
# ---------------------------------------------------------------------------

class TestDiskHitAcrossInstances:
    def test_second_instance_served_from_disk(
        self, monkeypatch, tmp_path, backend
    ) -> None:
        monkeypatch.setenv("CRM_HOME", str(tmp_path))
        url = backend.url_for("EntityDefinitions")
        with rm_module.Mocker() as m:
            m.get(url, json=_ENTITY_RESPONSE)
            # First instance — cold miss, hits network and writes disk
            cache1 = MetadataCache(use_cache=True)
            cache1.logical_names(backend)
            assert m.call_count == 1

            # Second fresh instance — warm hit, served from disk
            cache2 = MetadataCache(use_cache=True)
            result = cache2.logical_names(backend)
            assert m.call_count == 1  # no extra network call
            # set_names must also be served from disk (no extra network call)
            sets = cache2.set_names(backend)
            assert sets == ["accounts", "contacts"]
            assert m.call_count == 1

        assert result == ["account", "contact"]


# ---------------------------------------------------------------------------
# Test 3: default MetadataCache() (no cache) — always hits network, no disk file
# ---------------------------------------------------------------------------

class TestDefaultNoCachePath:
    def test_two_instances_both_hit_network(
        self, monkeypatch, tmp_path, profile, backend
    ) -> None:
        monkeypatch.setenv("CRM_HOME", str(tmp_path))
        url = backend.url_for("EntityDefinitions")
        with rm_module.Mocker() as m:
            m.get(url, json=_ENTITY_RESPONSE)

            cache1 = MetadataCache()  # default: use_cache=False
            cache1.logical_names(backend)
            assert m.call_count == 1

            cache2 = MetadataCache()
            cache2.logical_names(backend)
            assert m.call_count == 2

        # No disk file written
        assert not mc_mod.cache_file(backend.profile).exists()


# ---------------------------------------------------------------------------
# Test 4: refresh=True is one-shot — _refresh cleared after first load
# ---------------------------------------------------------------------------

class TestRefreshIsOneShot:
    def test_refresh_false_after_first_load(
        self, monkeypatch, tmp_path, backend
    ) -> None:
        monkeypatch.setenv("CRM_HOME", str(tmp_path))
        url = backend.url_for("EntityDefinitions")
        with rm_module.Mocker() as m:
            m.get(url, json=_ENTITY_RESPONSE)
            cache = MetadataCache(use_cache=True, refresh=True)
            cache.logical_names(backend)

        # After the first load, _refresh must be False so a hypothetical
        # subsequent _load call would use the cache instead of force-refreshing.
        assert cache._refresh is False
