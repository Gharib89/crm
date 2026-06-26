# pyright: basic
"""Tests for crm/core/metadata_cache.py — on-disk entity-definition cache."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from crm.utils.d365_backend import ConnectionProfile
from crm.core.metadata_cache import (
    SCHEMA_VERSION,
    TTL_SECONDS,
    CacheLookup,
    cache_file,
    clear,
    invalidate,
    load_definitions,
    read_definitions,
    write_definitions,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_DEFS: list[dict[str, str]] = [
    {"logical": "account", "set_name": "accounts"},
    {"logical": "contact", "set_name": "contacts"},
]


@pytest.fixture()
def crm_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point CRM_HOME at a temp dir so real ~/.crm is never touched."""
    monkeypatch.setenv("CRM_HOME", str(tmp_path))
    return tmp_path


def make_profile(
    name: str = "testp",
    url: str = "https://crm.contoso.local/contoso",
    api_version: str = "v9.2",
) -> ConnectionProfile:
    return ConnectionProfile(
        name=name,
        url=url,
        domain="CONTOSO",
        username="alice",
        api_version=api_version,
        verify_ssl=False,
    )


T0 = 1_000_000.0  # arbitrary base epoch time


# ---------------------------------------------------------------------------
# cache_file
# ---------------------------------------------------------------------------

def test_cache_file_path(crm_home: Path) -> None:
    profile = make_profile()
    expected = crm_home / "cache" / "testp" / "entitydefs.json"
    assert cache_file(profile) == expected


def test_cache_file_different_profiles(crm_home: Path) -> None:
    p1 = make_profile(name="alpha")
    p2 = make_profile(name="beta")
    assert cache_file(p1) != cache_file(p2)


# ---------------------------------------------------------------------------
# write_definitions / read_definitions — roundtrip
# ---------------------------------------------------------------------------

def test_write_read_roundtrip(crm_home: Path) -> None:
    profile = make_profile()
    write_definitions(profile, SAMPLE_DEFS, now=T0)
    result = read_definitions(profile, now=T0)
    assert result == SAMPLE_DEFS


def test_write_creates_parent_dirs(crm_home: Path) -> None:
    profile = make_profile(name="newprofile")
    cf = cache_file(profile)
    assert not cf.parent.exists()
    write_definitions(profile, SAMPLE_DEFS, now=T0)
    assert cf.is_file()


# ---------------------------------------------------------------------------
# read_definitions — miss conditions
# ---------------------------------------------------------------------------

def test_read_absent_file_returns_none(crm_home: Path) -> None:
    profile = make_profile()
    assert read_definitions(profile, now=T0) is None


def test_read_url_mismatch_returns_none(crm_home: Path) -> None:
    writer = make_profile(url="https://crm.contoso.local/contoso")
    reader = make_profile(url="https://other.contoso.local/other")
    write_definitions(writer, SAMPLE_DEFS, now=T0)
    assert read_definitions(reader, now=T0) is None


def test_read_api_version_mismatch_returns_none(crm_home: Path) -> None:
    writer = make_profile(api_version="v9.2")
    reader = make_profile(api_version="v9.1")
    write_definitions(writer, SAMPLE_DEFS, now=T0)
    assert read_definitions(reader, now=T0) is None


# ---------------------------------------------------------------------------
# TTL matrix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("delta", [0, 899, 900])
def test_read_within_ttl_returns_data(crm_home: Path, delta: int) -> None:
    profile = make_profile()
    write_definitions(profile, SAMPLE_DEFS, now=T0)
    assert read_definitions(profile, now=T0 + delta) == SAMPLE_DEFS


def test_read_past_ttl_returns_none(crm_home: Path) -> None:
    profile = make_profile()
    write_definitions(profile, SAMPLE_DEFS, now=T0)
    assert read_definitions(profile, now=T0 + TTL_SECONDS + 1) is None


# ---------------------------------------------------------------------------
# read_definitions — corrupt / malformed payload
# ---------------------------------------------------------------------------

def test_read_corrupt_json_returns_none(crm_home: Path) -> None:
    profile = make_profile()
    cf = cache_file(profile)
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_bytes(b"not valid json }{")
    assert read_definitions(profile, now=T0) is None


def test_read_corrupt_json_does_not_raise(crm_home: Path) -> None:
    profile = make_profile()
    cf = cache_file(profile)
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_bytes(b"\x00\x01\x02")
    # Must not raise
    result = read_definitions(profile, now=T0)
    assert result is None


def test_read_payload_not_dict_returns_none(crm_home: Path) -> None:
    profile = make_profile()
    cf = cache_file(profile)
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert read_definitions(profile, now=T0) is None


def test_read_missing_keys_returns_none(crm_home: Path) -> None:
    profile = make_profile()
    cf = cache_file(profile)
    cf.parent.mkdir(parents=True, exist_ok=True)
    # Write a dict that is missing required keys
    cf.write_text(json.dumps({"url": profile.url}), encoding="utf-8")
    assert read_definitions(profile, now=T0) is None


def test_read_definitions_not_list_returns_none(crm_home: Path) -> None:
    profile = make_profile()
    cf = cache_file(profile)
    cf.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": profile.url.rstrip("/"),
        "api_version": profile.api_version,
        "cached_at": T0,
        "schema": SCHEMA_VERSION,
        "definitions": "not-a-list",
    }
    cf.write_text(json.dumps(payload), encoding="utf-8")
    assert read_definitions(profile, now=T0) is None


def test_read_definitions_type_violation_returns_none(crm_home: Path) -> None:
    """definitions with correct keys but non-str value are rejected."""
    profile = make_profile()
    cf = cache_file(profile)
    cf.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": profile.url.rstrip("/"),
        "api_version": profile.api_version,
        "cached_at": T0,
        "schema": SCHEMA_VERSION,
        "definitions": [{"logical": "account", "set_name": 123}],  # int value, not str
    }
    cf.write_text(json.dumps(payload), encoding="utf-8")
    assert read_definitions(profile, now=T0) is None


def test_read_definitions_wrong_keys_returns_none(crm_home: Path) -> None:
    """definitions using legacy LogicalName/EntitySetName keys are a MISS (C2 regression guard)."""
    profile = make_profile()
    cf = cache_file(profile)
    cf.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": profile.url.rstrip("/"),
        "api_version": profile.api_version,
        "cached_at": T0,
        "schema": SCHEMA_VERSION,
        "definitions": [{"LogicalName": "account", "EntitySetName": "accounts"}],
    }
    cf.write_text(json.dumps(payload), encoding="utf-8")
    assert read_definitions(profile, now=T0) is None


# ---------------------------------------------------------------------------
# clear / invalidate
# ---------------------------------------------------------------------------

def test_clear_existing_file_returns_true(crm_home: Path) -> None:
    profile = make_profile()
    write_definitions(profile, SAMPLE_DEFS, now=T0)
    assert clear(profile) is True
    assert not cache_file(profile).exists()


def test_clear_nonexistent_returns_false(crm_home: Path) -> None:
    profile = make_profile()
    assert clear(profile) is False


def test_clear_twice_second_is_false(crm_home: Path) -> None:
    profile = make_profile()
    write_definitions(profile, SAMPLE_DEFS, now=T0)
    assert clear(profile) is True
    assert clear(profile) is False


def test_invalidate_no_cache_dir_does_not_raise(crm_home: Path) -> None:
    profile = make_profile(name="never_written")
    # No dir was ever created — must not raise
    invalidate(profile)


def test_invalidate_removes_file(crm_home: Path) -> None:
    profile = make_profile()
    write_definitions(profile, SAMPLE_DEFS, now=T0)
    invalidate(profile)
    assert not cache_file(profile).exists()


# ---------------------------------------------------------------------------
# load_definitions — orchestration
# ---------------------------------------------------------------------------

def test_load_definitions_refresh_true_calls_fetch_once(crm_home: Path) -> None:
    call_count = 0

    def fetcher() -> list[dict[str, str]]:
        nonlocal call_count
        call_count += 1
        return SAMPLE_DEFS

    profile = make_profile()
    result = load_definitions(profile, fetcher, refresh=True, now=T0)
    assert call_count == 1
    assert result.status == "refreshed"
    assert result.definitions == SAMPLE_DEFS


def test_load_definitions_refresh_true_writes_file(crm_home: Path) -> None:
    profile = make_profile()
    load_definitions(profile, lambda: SAMPLE_DEFS, refresh=True, now=T0)
    assert cache_file(profile).is_file()


def test_load_definitions_cold_miss_fetches_and_writes(crm_home: Path) -> None:
    call_count = 0

    def fetcher() -> list[dict[str, str]]:
        nonlocal call_count
        call_count += 1
        return SAMPLE_DEFS

    profile = make_profile()
    result = load_definitions(profile, fetcher, refresh=False, now=T0)
    assert call_count == 1
    assert result.status == "miss"
    assert result.definitions == SAMPLE_DEFS
    assert cache_file(profile).is_file()


def test_load_definitions_warm_hit_no_fetch(crm_home: Path) -> None:
    call_count = 0

    def fetcher() -> list[dict[str, str]]:
        nonlocal call_count
        call_count += 1
        return SAMPLE_DEFS

    profile = make_profile()
    # Prime the cache
    write_definitions(profile, SAMPLE_DEFS, now=T0)
    result = load_definitions(profile, fetcher, refresh=False, now=T0)
    assert call_count == 0
    assert result.status == "hit"
    assert result.definitions == SAMPLE_DEFS


def test_load_definitions_refresh_overwrites_existing(crm_home: Path) -> None:
    """refresh=True should overwrite even if a valid cache exists."""
    profile = make_profile()
    write_definitions(profile, SAMPLE_DEFS, now=T0)

    new_defs = [{"logical": "opportunity", "set_name": "opportunities"}]
    result = load_definitions(profile, lambda: new_defs, refresh=True, now=T0 + 1)
    assert result.status == "refreshed"
    assert result.definitions == new_defs
    # Check file contains new data
    on_disk = read_definitions(profile, now=T0 + 1)
    assert on_disk == new_defs


# ---------------------------------------------------------------------------
# atomic write — no leftover .tmp
# ---------------------------------------------------------------------------

def test_write_no_tmp_sibling_remains(crm_home: Path) -> None:
    """After write_definitions, no *.tmp sibling should exist in the cache dir."""
    profile = make_profile()
    write_definitions(profile, SAMPLE_DEFS, now=T0)
    cache_dir = cache_file(profile).parent
    tmp_files = list(cache_dir.glob("*.tmp"))
    assert tmp_files == [], f"leftover .tmp files: {tmp_files}"


# ---------------------------------------------------------------------------
# load_definitions — TTL-expired miss
# ---------------------------------------------------------------------------

def test_load_definitions_ttl_expired_miss_calls_fetch(crm_home: Path) -> None:
    """A TTL-expired cache entry is a miss; the fetcher must be called."""
    call_count = 0

    def fetcher() -> list[dict[str, str]]:
        nonlocal call_count
        call_count += 1
        return SAMPLE_DEFS

    profile = make_profile()
    write_definitions(profile, SAMPLE_DEFS, now=T0)
    result = load_definitions(profile, fetcher, refresh=False, now=T0 + TTL_SECONDS + 1)
    assert call_count == 1
    assert result.status == "miss"
    assert result.definitions == SAMPLE_DEFS


# ---------------------------------------------------------------------------
# Per-profile isolation
# ---------------------------------------------------------------------------

def test_per_profile_isolation(crm_home: Path) -> None:
    p1 = make_profile(name="alpha")
    p2 = make_profile(name="beta")
    defs1 = [{"logical": "account", "set_name": "accounts"}]
    defs2 = [{"logical": "contact", "set_name": "contacts"}]

    write_definitions(p1, defs1, now=T0)
    write_definitions(p2, defs2, now=T0)

    assert read_definitions(p1, now=T0) == defs1
    assert read_definitions(p2, now=T0) == defs2

    clear(p1)
    assert read_definitions(p1, now=T0) is None
    assert read_definitions(p2, now=T0) == defs2
