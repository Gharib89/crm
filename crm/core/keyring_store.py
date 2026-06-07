"""Optional OS-keyring credential store (issue #130).

Isolates the optional `keyring` dependency (the `crm[keyring]` extra) behind a
small, mockable interface so the credential resolver stays clean and tests never
touch a live keyring backend. Service name is fixed; the account is the profile
name. The stored secret is scheme-aware at the call site (NTLM password or OAuth
client secret) — this module just stores an opaque string.
"""

from __future__ import annotations

from typing import Any

from crm.utils.d365_backend import D365Error

KEYRING_SERVICE = "crm"

# Backend module name keyring uses when no real backend is available.
_NULL_BACKEND_MODULE = "keyring.backends.fail"


def _import_keyring() -> Any:
    try:
        import keyring
    except ImportError as exc:
        raise D365Error(
            "The optional 'keyring' dependency is not installed. Install it with "
            "`pip install crm[keyring]`, or store the secret with "
            "--store-password-plaintext, or supply it via D365_PASSWORD / "
            "D365_CLIENT_SECRET (env or .env)."
        ) from exc
    return keyring


def is_available() -> bool:
    """True only when keyring is importable AND a real backend is configured."""
    try:
        kr = _import_keyring()
    except D365Error:
        return False
    try:
        backend = kr.get_keyring()
    except Exception:
        return False
    return type(backend).__module__ != _NULL_BACKEND_MODULE


def get_secret(profile_name: str) -> str | None:
    """Read the stored secret, or None. Soft on a missing keyring (returns None)
    so the resolver can treat keyring as just one optional source."""
    if not is_available():
        return None
    try:
        return _import_keyring().get_password(KEYRING_SERVICE, profile_name)
    except Exception:
        # A configured-but-flaky backend (locked Keychain, DBus error) must not
        # crash the resolver — degrade to "no stored secret" so resolution can
        # fall through to env/prompt/raise. Matches the soft is_available() guard.
        return None


def set_secret(profile_name: str, secret: str) -> None:
    """Store the secret. Hard error if keyring is unavailable — the caller asked
    for keyring storage explicitly (--store-password)."""
    if not is_available():
        # _import_keyring raises the actionable message; if it imported but has
        # no backend, raise the same guidance here.
        _import_keyring()
        raise D365Error(
            "No usable OS keyring backend is available. Use "
            "--store-password-plaintext, or env vars, instead."
        )
    try:
        _import_keyring().set_password(KEYRING_SERVICE, profile_name, secret)
    except Exception as exc:
        # Convert a backend write failure into a D365Error so the CLI's handler
        # reports it cleanly instead of leaking a raw keyring traceback.
        raise D365Error(
            f"Failed to store the secret in the OS keyring: {exc}. Use "
            "--store-password-plaintext, or env vars, instead."
        ) from exc


def delete_secret(profile_name: str) -> bool:
    """Remove the stored secret. Returns True iff an entry existed. Soft on a
    missing keyring (returns False — nothing to delete)."""
    if not is_available():
        return False
    kr = _import_keyring()
    try:
        if kr.get_password(KEYRING_SERVICE, profile_name) is None:
            return False
        kr.delete_password(KEYRING_SERVICE, profile_name)
        return True
    except Exception:
        # Soft-fail: a backend error must not break delete-password / profiles.
        return False


def has_secret(profile_name: str) -> bool:
    return get_secret(profile_name) is not None
