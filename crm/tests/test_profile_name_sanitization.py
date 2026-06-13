# pyright: basic
"""Tests for profile/session name path-traversal hardening (#126).

validate_profile_name rejects names that could escape the intended state
directory (empty, ".", "..", path-separator-containing, or otherwise not a
single safe basename). The validator is called at two boundaries:
  - ConnectionProfile.__post_init__ (covers construction + from_dict/load)
  - profile_path() and session_path() in crm/core/session.py
"""
from __future__ import annotations

import pytest

from crm.utils.d365_backend import ConnectionProfile, D365Error, validate_profile_name
from crm.core.session import profile_path, session_path, save_profile, load_profile

pytestmark = pytest.mark.usefixtures("isolated_home")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile(name: str) -> ConnectionProfile:
    """Minimal valid ConnectionProfile using Contoso placeholder values."""
    return ConnectionProfile(
        name=name,
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
    )


# ---------------------------------------------------------------------------
# validate_profile_name — direct unit tests
# ---------------------------------------------------------------------------

class TestValidateProfileNameRejects:
    @pytest.mark.parametrize("bad_name", [
        "",
        ".",
        "..",
        "../evil",
        "foo/bar",
        "foo\\bar",
        "/etc/passwd",
        "a/../b",
        "a\x00b",
        "C:foo",   # Windows drive-relative — colon check
        "C:",      # bare drive letter — colon check
    ])
    def test_raises_d365error(self, bad_name: str) -> None:
        with pytest.raises(D365Error):
            validate_profile_name(bad_name)


class TestValidateProfileNameAccepts:
    @pytest.mark.parametrize("good_name", [
        "prod",
        "crmworx",
        "my-profile",
        "profile.1",
    ])
    def test_returns_name_unchanged(self, good_name: str) -> None:
        result = validate_profile_name(good_name)
        assert result == good_name


# ---------------------------------------------------------------------------
# ConnectionProfile construction boundary
# ---------------------------------------------------------------------------

class TestConnectionProfileConstructionBoundary:
    def test_traversal_name_raises_on_construction(self) -> None:
        with pytest.raises(D365Error):
            _make_profile("../evil")

    def test_slash_in_name_raises_on_construction(self) -> None:
        with pytest.raises(D365Error):
            _make_profile("foo/bar")

    def test_empty_name_raises_on_construction(self) -> None:
        with pytest.raises(D365Error):
            _make_profile("")

    def test_valid_name_constructs_normally(self) -> None:
        p = _make_profile("prod")
        assert p.name == "prod"


# ---------------------------------------------------------------------------
# profile_path / session_path — path-builder boundary
# ---------------------------------------------------------------------------

class TestPathBuilderBoundary:
    def test_profile_path_rejects_traversal(self) -> None:
        with pytest.raises(D365Error):
            profile_path("../evil")

    def test_session_path_rejects_traversal(self) -> None:
        with pytest.raises(D365Error):
            session_path("../evil")

    def test_profile_path_rejects_slash(self) -> None:
        with pytest.raises(D365Error):
            profile_path("foo/bar")

    def test_session_path_rejects_slash(self) -> None:
        with pytest.raises(D365Error):
            session_path("foo/bar")


# ---------------------------------------------------------------------------
# load_profile routes through profile_path — traversal rejected
# ---------------------------------------------------------------------------

class TestLoadProfileBoundary:
    def test_load_profile_raises_for_traversal_name(self) -> None:
        with pytest.raises(D365Error):
            load_profile("../evil")


# ---------------------------------------------------------------------------
# Happy path — normal names still round-trip
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_save_and_load_roundtrip(self) -> None:
        p = _make_profile("prod")
        save_profile(p)
        loaded = load_profile("prod")
        assert loaded.name == "prod"
        assert loaded.url == "https://crm.contoso.local/contoso"
        assert loaded.username == "alice"

    def test_session_path_returns_valid_path(self) -> None:
        path = session_path("default")
        # The path's name component should be "default.json"
        assert path.name == "default.json"


# ---------------------------------------------------------------------------
# Cache protection (transitive via constructor)
# ---------------------------------------------------------------------------

class TestCacheProtectionTransitive:
    def test_cache_file_never_reached_with_traversal_name(self) -> None:
        """cache_file(profile) uses profile.name, which is validated at
        construction time — so a traversal name can never reach cache_file.
        The constructor is the barrier; assert it raises before any profile
        object is ever produced."""
        # Construction must raise — no profile object is ever produced
        with pytest.raises(D365Error):
            _make_profile("../evil")
