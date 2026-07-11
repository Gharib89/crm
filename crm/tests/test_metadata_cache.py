"""Unit tests for REPL metadata cache + completion logic."""

# pyright: basic
from __future__ import annotations

from crm.commands.repl import MetadataCache, complete_entity_token

_ENTITY_LIST = {
    "value": [
        {"LogicalName": "account", "EntitySetName": "accounts"},
        {"LogicalName": "contact", "EntitySetName": "contacts"},
        {"LogicalName": "new_project", "EntitySetName": "new_projects"},
    ]
}

_OTHER_ORG_LIST = {
    "value": [
        {"LogicalName": "widget", "EntitySetName": "widgets"},
    ]
}


def _profile(name: str):
    from crm.utils.d365_backend import ConnectionProfile

    return ConnectionProfile(
        name=name,
        url=f"https://{name}.contoso.local/{name}",
        domain="CONTOSO",
        username="alice",
        api_version="v9.2",
        verify_ssl=False,
    )


class TestMetadataCache:
    def test_first_call_fetches_entity_names(self, make_fake_backend):
        b = make_fake_backend(responses={"get": _ENTITY_LIST})
        cache = MetadataCache()
        names = cache.logical_names(b)
        assert names == ["account", "contact", "new_project"]
        assert b.count() == 1

    def test_repeated_call_uses_cache(self, make_fake_backend):
        b = make_fake_backend(responses={"get": _ENTITY_LIST})
        cache = MetadataCache()
        cache.logical_names(b)
        cache.logical_names(b)
        cache.logical_names(b)
        assert b.count() == 1

    def test_set_names_uses_same_fetch(self, make_fake_backend):
        b = make_fake_backend(responses={"get": _ENTITY_LIST})
        cache = MetadataCache()
        cache.logical_names(b)  # first fetch
        sets = cache.set_names(b)  # should reuse cache
        assert sets == ["accounts", "contacts", "new_projects"]
        assert b.count() == 1

    def test_entities_backward_compat(self, make_fake_backend):
        b = make_fake_backend(responses={"get": _ENTITY_LIST})
        cache = MetadataCache()
        assert cache.entities(b) == ["account", "contact", "new_project"]

    def test_profile_switch_reloads_entity_names(self, make_fake_backend):
        """Switching the active profile mid-REPL must re-fetch: completion
        candidates are org-specific, so a cached list from the previous profile
        is wrong.
        """
        a = make_fake_backend(profile=_profile("orga"), responses={"get": _ENTITY_LIST})
        b = make_fake_backend(profile=_profile("orgb"), responses={"get": _OTHER_ORG_LIST})
        cache = MetadataCache()
        assert cache.logical_names(a) == ["account", "contact", "new_project"]
        # Profile switched to orgb — completer must now serve orgb's entities.
        assert cache.logical_names(b) == ["widget"]
        assert cache.set_names(b) == ["widgets"]

    def test_same_profile_still_cached_after_reload_support(self, make_fake_backend):
        """Repeat calls on the SAME profile must not refetch (regression guard
        that the profile-keying didn't defeat the session cache).
        """
        b = make_fake_backend(profile=_profile("orga"), responses={"get": _ENTITY_LIST})
        cache = MetadataCache()
        cache.logical_names(b)
        cache.set_names(b)
        cache.logical_names(b)
        assert b.count() == 1


_ATTRS_ACCOUNT = {
    "value": [
        {"LogicalName": "name"},
        {"LogicalName": "accountnumber"},
        {"LogicalName": "telephone1"},
    ]
}


def _defs_or_attrs(attrs):
    """Fake-backend GET responder: entity defs for the ``EntityDefinitions``
    collection, ``attrs`` for the ``.../Attributes`` sub-path.
    """

    def _respond(path):
        if "/Attributes" in str(path):
            return attrs
        return _ENTITY_LIST

    return _respond


class TestAttributeNames:
    def test_fetches_and_memoizes_per_entity(self, make_fake_backend):
        b = make_fake_backend(responses={"get": _defs_or_attrs(_ATTRS_ACCOUNT)})
        cache = MetadataCache()
        assert cache.attribute_names(b, "account") == ["name", "accountnumber", "telephone1"]
        # One EntityDefinitions GET (the def lists) + one Attributes GET.
        assert b.count("get") == 2
        cache.attribute_names(b, "account")  # memoized — no further fetch
        assert b.count("get") == 2

    def test_resolves_set_name_to_logical(self, make_fake_backend):
        b = make_fake_backend(responses={"get": _defs_or_attrs(_ATTRS_ACCOUNT)})
        cache = MetadataCache()
        # "accounts" (entity-set name) resolves to the "account" logical name.
        assert cache.attribute_names(b, "accounts") == ["name", "accountnumber", "telephone1"]

    def test_unknown_token_treated_as_logical(self, make_fake_backend):
        # A token in neither list (e.g. a just-created entity) is fetched as a
        # logical name directly rather than yielding nothing.
        b = make_fake_backend(responses={"get": _defs_or_attrs(_ATTRS_ACCOUNT)})
        cache = MetadataCache()
        assert cache.attribute_names(b, "new_widget") == ["name", "accountnumber", "telephone1"]

    def test_profile_switch_clears_attribute_memo(self, make_fake_backend):
        other = {"value": [{"LogicalName": "widgetname"}]}
        a = make_fake_backend(
            profile=_profile("orga"), responses={"get": _defs_or_attrs(_ATTRS_ACCOUNT)}
        )
        b = make_fake_backend(profile=_profile("orgb"), responses={"get": _defs_or_attrs(other)})
        cache = MetadataCache()
        assert cache.attribute_names(a, "account") == ["name", "accountnumber", "telephone1"]
        # Profile switched: the def lists reload and the attribute memo is cleared,
        # so the previous org's columns are never served for the new profile.
        assert cache.attribute_names(b, "account") == ["widgetname"]


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
