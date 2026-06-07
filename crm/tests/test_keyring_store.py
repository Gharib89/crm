"""Unit tests for the optional keyring wrapper (issue #130)."""
# pyright: basic
from __future__ import annotations

import pytest

from crm.core import keyring_store
from crm.utils.d365_backend import D365Error


class _FakeKeyring:
    """In-memory stand-in for the `keyring` module's password API."""
    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, name):
        return self.store.get((service, name))

    def set_password(self, service, name, secret):
        self.store[(service, name)] = secret

    def delete_password(self, service, name):
        del self.store[(service, name)]

    def get_keyring(self):
        return self  # __class__.__module__ != the null-backend module → "usable"


@pytest.fixture
def fake(monkeypatch):
    kr = _FakeKeyring()
    monkeypatch.setattr(keyring_store, "_import_keyring", lambda: kr)
    return kr


def test_set_then_get_roundtrips(fake):
    keyring_store.set_secret("prod", "s3cret")
    assert keyring_store.get_secret("prod") == "s3cret"


def test_get_missing_returns_none(fake):
    assert keyring_store.get_secret("nope") is None


def test_has_secret_true_false(fake):
    assert keyring_store.has_secret("prod") is False
    keyring_store.set_secret("prod", "x")
    assert keyring_store.has_secret("prod") is True


def test_delete_existing_returns_true(fake):
    keyring_store.set_secret("prod", "x")
    assert keyring_store.delete_secret("prod") is True
    assert keyring_store.get_secret("prod") is None


def test_delete_missing_returns_false(fake):
    assert keyring_store.delete_secret("nope") is False


def test_is_available_true_when_backend_usable(fake):
    assert keyring_store.is_available() is True


def test_unavailable_when_keyring_missing(monkeypatch):
    def _raise():
        raise D365Error("not installed")
    monkeypatch.setattr(keyring_store, "_import_keyring", _raise)
    assert keyring_store.is_available() is False
    assert keyring_store.has_secret("prod") is False
    assert keyring_store.get_secret("prod") is None      # soft: resolver source
    assert keyring_store.delete_secret("prod") is False  # soft: nothing to delete
    with pytest.raises(D365Error):
        keyring_store.set_secret("prod", "x")            # hard: explicit intent


def test_is_available_false_when_get_keyring_raises(monkeypatch):
    class _Raises:
        def get_keyring(self):
            raise RuntimeError("no backend")
    monkeypatch.setattr(keyring_store, "_import_keyring", lambda: _Raises())
    assert keyring_store.is_available() is False


class _ErroringKeyring:
    """Usable backend whose password ops raise (locked Keychain, DBus error)."""
    def get_password(self, service, name):
        raise RuntimeError("backend locked")

    def set_password(self, service, name, secret):
        raise RuntimeError("backend locked")

    def delete_password(self, service, name):
        raise RuntimeError("backend locked")

    def get_keyring(self):
        return self  # reports usable, but every op blows up


@pytest.fixture
def erroring(monkeypatch):
    kr = _ErroringKeyring()
    monkeypatch.setattr(keyring_store, "_import_keyring", lambda: kr)
    return kr


def test_get_secret_soft_fails_on_backend_error(erroring):
    assert keyring_store.get_secret("prod") is None  # no traceback to the resolver


def test_delete_secret_soft_fails_on_backend_error(erroring):
    assert keyring_store.delete_secret("prod") is False


def test_set_secret_converts_backend_error_to_d365error(erroring):
    with pytest.raises(D365Error):
        keyring_store.set_secret("prod", "x")


def test_is_available_false_for_null_backend(monkeypatch):
    # A backend object whose class lives in the null-backend module is "unusable".
    null_mod = keyring_store._NULL_BACKEND_MODULE

    class _NullBackend:
        pass
    _NullBackend.__module__ = null_mod

    class _Kr:
        def get_keyring(self):
            return _NullBackend()
    monkeypatch.setattr(keyring_store, "_import_keyring", lambda: _Kr())
    assert keyring_store.is_available() is False
