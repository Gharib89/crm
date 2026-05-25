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
            {"LogicalName": "account"},
            {"LogicalName": "contact"},
            {"LogicalName": "new_project"},
        ]}


class TestMetadataCache:
    def test_first_call_fetches_entity_names(self):
        b = _FakeBackend()
        cache = MetadataCache()
        names = cache.entities(b)
        assert names == ["account", "contact", "new_project"]
        assert b.calls == 1

    def test_repeated_call_uses_cache(self):
        b = _FakeBackend()
        cache = MetadataCache()
        cache.entities(b)
        cache.entities(b)
        cache.entities(b)
        assert b.calls == 1


class TestCompleteEntityToken:
    def test_no_match_when_prefix_unrecognized(self):
        names = ["account", "contact"]
        assert complete_entity_token("ent", names) is None

    def test_returns_matches_after_entity_get(self):
        names = ["account", "contact", "new_project"]
        out = complete_entity_token("entity get acc", names)
        assert out == ["account"]

    def test_returns_matches_after_query_count(self):
        names = ["account", "contact", "new_project"]
        out = complete_entity_token("query count n", names)
        assert out == ["new_project"]

    def test_returns_all_when_no_prefix(self):
        names = ["account", "contact"]
        out = complete_entity_token("entity get ", names)
        assert out == names
