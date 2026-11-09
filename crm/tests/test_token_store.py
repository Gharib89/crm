"""Unit tests for the encrypted delegated-sign-in token store (#1144)."""

# pyright: basic
from __future__ import annotations

import base64
import os
import stat

import pytest

from crm.core import keyring_store, token_store

CACHE = '{"RefreshToken": {"k": {"secret": "super-secret-refresh-token"}}}'


class _FakeKeyring:
    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, name):
        return self.store.get((service, name))

    def set_password(self, service, name, secret):
        self.store[(service, name)] = secret

    def get_keyring(self):
        return self


def _assert_private(path):
    """Mode bits are enforced on POSIX only; Windows reports 0o666 regardless."""
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    return tmp_path / ".crm"


@pytest.fixture
def kr(home, monkeypatch):
    fake = _FakeKeyring()
    monkeypatch.setattr(keyring_store, "_import_keyring", lambda: fake)
    return fake


@pytest.fixture
def no_kr(home, monkeypatch):
    monkeypatch.setattr(keyring_store, "is_available", lambda: False)


def test_encrypted_round_trip_keeps_plaintext_off_disk(kr):
    assert token_store.save("p", CACHE) == "keyring"
    blob = token_store.cache_path("p").read_bytes()
    assert b"super-secret-refresh-token" not in blob
    assert token_store.load("p") == CACHE
    _assert_private(token_store.cache_path("p"))


def test_second_save_uses_new_nonce_and_same_key(kr):
    token_store.save("p", CACHE)
    first = token_store.cache_path("p").read_bytes()
    key = dict(kr.store)
    token_store.save("p", CACHE)
    second = token_store.cache_path("p").read_bytes()
    n = len(token_store._MAGIC)
    assert first[n : n + token_store._NONCE_LEN] != second[n : n + token_store._NONCE_LEN]
    assert kr.store == key


def test_fail_backend_writes_plaintext_0600(no_kr):
    assert token_store.save("p", CACHE) == "plaintext"
    path = token_store.cache_path("p")
    assert path.read_text(encoding="utf-8") == CACHE
    _assert_private(path)
    assert token_store.load("p") == CACHE


def test_missing_file_loads_empty(kr):
    assert token_store.load("p") == ""


def test_corrupt_file_loads_empty(kr):
    token_store.save("p", CACHE)
    token_store.cache_path("p").write_bytes(b"\x00garbage")
    assert token_store.load("p") == ""


def test_truncated_blob_loads_empty(kr):
    token_store.save("p", CACHE)
    token_store.cache_path("p").write_bytes(token_store._MAGIC + b"abc")
    assert token_store.load("p") == ""


def test_flipped_byte_loads_empty(kr):
    token_store.save("p", CACHE)
    path = token_store.cache_path("p")
    blob = bytearray(path.read_bytes())
    blob[-1] ^= 0x01
    path.write_bytes(bytes(blob))
    assert token_store.load("p") == ""


def test_wrong_key_loads_empty(kr):
    token_store.save("p", CACHE)
    kr.store[("crm", token_store._key_account("p"))] = base64.b64encode(b"\x01" * 32).decode()
    assert token_store.load("p") == ""


def test_missing_key_loads_empty(kr):
    token_store.save("p", CACHE)
    kr.store.clear()
    assert token_store.load("p") == ""


def test_undecodable_key_loads_empty(kr):
    token_store.save("p", CACHE)
    kr.store[("crm", token_store._key_account("p"))] = "not base64!"
    assert token_store.load("p") == ""


def test_plaintext_file_read_once_keyring_appeared_is_empty(home, monkeypatch):
    monkeypatch.setattr(keyring_store, "is_available", lambda: False)
    token_store.save("p", CACHE)
    monkeypatch.undo()
    monkeypatch.setenv("CRM_HOME", str(home))
    monkeypatch.setattr(keyring_store, "_import_keyring", lambda: _FakeKeyring())
    assert token_store.load("p") == ""


def test_encrypted_file_read_after_keyring_vanished_is_empty(home, monkeypatch):
    fake = _FakeKeyring()
    monkeypatch.setattr(keyring_store, "_import_keyring", lambda: fake)
    token_store.save("p", CACHE)
    monkeypatch.setattr(keyring_store, "is_available", lambda: False)
    assert token_store.load("p") == ""


def test_profiles_do_not_share_a_cache_or_key(kr):
    token_store.save("a", "A")
    token_store.save("b", "B")
    assert (token_store.load("a"), token_store.load("b")) == ("A", "B")
    assert token_store.cache_path("a") != token_store.cache_path("b")


def test_concurrent_first_save_key_swap_still_loads(kr, monkeypatch):
    """A second process replacing the key between the read and the write must not
    strand the file under a key the keyring no longer holds.
    """
    other = base64.b64encode(b"\x02" * 32).decode()
    real_write = token_store.atomic_write
    calls = []

    def racing_write(*args, **kwargs):
        if not calls:
            kr.store[("crm", token_store._key_account("p"))] = other
        calls.append(1)
        real_write(*args, **kwargs)

    monkeypatch.setattr(token_store, "atomic_write", racing_write)
    token_store.save("p", CACHE)
    assert token_store.load("p") == CACHE
