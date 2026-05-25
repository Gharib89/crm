"""Unit tests for REPL metadata cache + completion logic."""
# pyright: basic
from __future__ import annotations

import pytest

from crm.commands.repl import MetadataCache, complete_entity_token


class _FakeBackend:
    def __init__(self):
        self.calls = 0

    def get(self, path, params=None, **kw):
        self.calls += 1
        return {"value": [
            {"LogicalName": "account", "EntitySetName": "accounts"},
            {"LogicalName": "contact", "EntitySetName": "contacts"},
            {"LogicalName": "new_project", "EntitySetName": "new_projects"},
        ]}


class TestMetadataCache:
    def test_first_call_fetches_entity_names(self):
        b = _FakeBackend()
        cache = MetadataCache()
        names = cache.logical_names(b)
        assert names == ["account", "contact", "new_project"]
        assert b.calls == 1

    def test_repeated_call_uses_cache(self):
        b = _FakeBackend()
        cache = MetadataCache()
        cache.logical_names(b)
        cache.logical_names(b)
        cache.logical_names(b)
        assert b.calls == 1

    def test_set_names_uses_same_fetch(self):
        b = _FakeBackend()
        cache = MetadataCache()
        cache.logical_names(b)          # first fetch
        sets = cache.set_names(b)       # should reuse cache
        assert sets == ["accounts", "contacts", "new_projects"]
        assert b.calls == 1

    def test_entities_backward_compat(self):
        b = _FakeBackend()
        cache = MetadataCache()
        assert cache.entities(b) == ["account", "contact", "new_project"]


class TestCompleteEntityToken:
    _LOGICAL = ["account", "contact", "new_project"]
    _SETS = ["accounts", "contacts", "new_projects"]

    def test_no_match_when_prefix_unrecognized(self):
        assert complete_entity_token("ent", self._LOGICAL, self._SETS) is None

    def test_entity_get_completes_set_name(self):
        out = complete_entity_token("entity get acc", self._LOGICAL, self._SETS)
        assert out == ["accounts"]

    def test_query_count_completes_logical_name(self):
        out = complete_entity_token("query count n", self._LOGICAL, self._SETS)
        assert out == ["new_project"]

    def test_returns_all_set_names_when_no_prefix(self):
        out = complete_entity_token("entity get ", self._LOGICAL, self._SETS)
        assert out == self._SETS

    def test_returns_all_logical_when_no_prefix_on_count(self):
        out = complete_entity_token("query count ", self._LOGICAL, self._SETS)
        assert out == self._LOGICAL
