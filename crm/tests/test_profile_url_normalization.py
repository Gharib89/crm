# pyright: basic
"""Tests for ConnectionProfile URL whitespace/trailing-slash normalization (#631).

A `url` with a leading or trailing space (pasted from a chat/clipboard into the
`profile add` wizard or `--url`) used to be stored verbatim: `.rstrip("/")` was
the only cleanup, so a trailing space survived and landed as a literal `%20`
inside the API path — 404-ing every request. Normalization now lives in one seam
(`ConnectionProfile.normalize_url`, called from `__post_init__`), so construction,
`from_dict` (self-healing a stored profile), and `profile edit` all get it.
"""
from __future__ import annotations

import pytest

from crm.utils.d365_backend import ConnectionProfile


def _profile(url: str) -> ConnectionProfile:
    return ConnectionProfile(name="t", url=url, domain="D", username="u")


class TestNormalizeUrlStaticmethod:
    @pytest.mark.parametrize("raw,expected", [
        (" https://host.contoso.local/org", "https://host.contoso.local/org"),
        ("https://host.contoso.local/org ", "https://host.contoso.local/org"),
        ("  https://host.contoso.local/org  ", "https://host.contoso.local/org"),
        ("https://host.contoso.local/org/", "https://host.contoso.local/org"),
        (" https://host.contoso.local/org/ ", "https://host.contoso.local/org"),
        ("\thttps://host.contoso.local/org\n", "https://host.contoso.local/org"),
        # A space *before* the final slash must not re-surface as trailing
        # whitespace once the slash is stripped (would reintroduce %20).
        ("https://host.contoso.local/org /", "https://host.contoso.local/org"),
        (" https://host.contoso.local/org / ", "https://host.contoso.local/org"),
    ])
    def test_strips_surrounding_whitespace_and_trailing_slash(self, raw, expected):
        assert ConnectionProfile.normalize_url(raw) == expected

    def test_interior_whitespace_untouched(self):
        # Only leading/trailing whitespace is stripped — an interior space is a
        # genuinely different (broken) URL and is left as-is, not silently fused.
        assert ConnectionProfile.normalize_url("https://ho st/org") == "https://ho st/org"


class TestConstructionNormalizes:
    def test_trailing_space_stripped_from_url_attr(self):
        # The clear-cut bug: a trailing space survives rstrip("/") pre-fix.
        assert _profile("https://host.contoso.local/org ").url == "https://host.contoso.local/org"

    def test_leading_space_stripped_from_url_attr(self):
        assert _profile(" https://host.contoso.local/org").url == "https://host.contoso.local/org"

    def test_api_base_has_no_encoded_space(self):
        # The whole point of #631: the org segment must not become `org%20`.
        p = _profile("https://host.contoso.local/org ")
        assert p.api_base == "https://host.contoso.local/org/api/data/v9.2/"
        assert " " not in p.api_base

    def test_trailing_slash_still_stripped(self):
        # Behavior unchanged: trailing-slash stripping is preserved.
        assert _profile("https://host.contoso.local/org/").url == "https://host.contoso.local/org"

    def test_to_dict_url_normalized(self):
        assert _profile("  https://host.contoso.local/org  ").to_dict()["url"] == \
            "https://host.contoso.local/org"

    def test_interior_whitespace_untouched(self):
        assert _profile("https://ho st/org").url == "https://ho st/org"


class TestFromDictSelfHeals:
    def test_stored_whitespace_url_healed_on_load(self):
        # A profile file written by an older version with a stray space in `url`
        # self-heals on from_dict — no migration script needed.
        d = _profile("https://host.contoso.local/org").to_dict()
        d["url"] = " https://host.contoso.local/org "  # simulate a legacy stored value
        healed = ConnectionProfile.from_dict(d)
        assert healed.url == "https://host.contoso.local/org"
        assert healed.api_base == "https://host.contoso.local/org/api/data/v9.2/"
