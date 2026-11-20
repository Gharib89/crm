"""Optional OS-keyring credential store (issue #130).

Isolates the optional `keyring` dependency (the `crm[keyring]` extra) behind a
small, mockable interface so the credential resolver stays clean and tests never
touch a live keyring backend. Service name is fixed; the account is the profile
name. The stored secret is scheme-aware at the call site (NTLM password or OAuth
client secret) — this module just stores an opaque string.
"""

from __future__ import annotations

import sys
from typing import Any

from crm.utils.d365_backend import D365Error

KEYRING_SERVICE = "crm"

_NULL_BACKEND_MODULE = "keyring.backends.fail"
"""The module of the backend keyring falls back to when no real backend is available."""

_SECRET_FALLBACK = (
    " Meanwhile, store the secret with `crm profile set-password "
    "--profile <name> --store-password-plaintext`, or pass `--password` "
    "for a single run."
)


def _missing_keyring_message() -> str:
    """Keyring is a core dependency, so this only fires on a broken install. The
    remedy depends on how crm was installed — a frozen binary can't pip-install,
    a `uv tool` install needs uv — so point at the install method, not a single
    (often wrong for this machine) pip command.
    """
    if getattr(sys, "frozen", False):
        return (
            "This crm binary is missing its bundled 'keyring' support — likely a "
            "build defect; please report it." + _SECRET_FALLBACK
        )
    return (
        "The 'keyring' package is missing from this crm install (it ships as a "
        "core dependency). Reinstall crm with your installer, e.g. "
        "`uv tool install --reinstall <source>` or `pip install --force-reinstall "
        "crm`." + _SECRET_FALLBACK
    )


def _import_keyring() -> Any:
    """Import keyring on first use, since only storing or reading a secret needs it.

    See ADR 0029.
    """
    try:
        import keyring
    except ImportError as exc:
        raise D365Error(_missing_keyring_message()) from exc
    return keyring


def is_available() -> bool:
    """True only when keyring is importable AND a real backend is configured.

    A backend whose native bindings cannot load panics at the Rust layer (pyo3)
    with `pyo3_runtime.PanicException`, a `BaseException` subclass rather than an
    `Exception`, so the backend lookup catches `BaseException` and reads it as
    unusable (issue #308).
    """
    try:
        kr = _import_keyring()
    except D365Error:
        return False
    try:
        backend = kr.get_keyring()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        return False
    return type(backend).__module__ != _NULL_BACKEND_MODULE


def get_secret(profile_name: str) -> str | None:
    """Read the stored secret, or None. Soft on a missing keyring (returns None)
    so the resolver can treat keyring as just one optional source.

    A configured but flaky backend (a locked Keychain, a DBus error) also returns
    None rather than crashing the resolver, so resolution falls through to the
    prompt or the error, matching the soft `is_available()` guard.
    """
    if not is_available():
        return None
    try:
        return _import_keyring().get_password(KEYRING_SERVICE, profile_name)
    except Exception:
        return None


def set_secret(profile_name: str, secret: str) -> None:
    """Store the secret. Hard error if keyring is unavailable — the caller asked
    for keyring storage explicitly (--store-password).

    When keyring is not importable, `_import_keyring()` raises its own install
    guidance; only an importable keyring with no backend reaches the generic error.
    """
    if not is_available():
        _import_keyring()
        raise D365Error("No usable OS keyring backend is available." + _SECRET_FALLBACK)
    try:
        _import_keyring().set_password(KEYRING_SERVICE, profile_name, secret)
    except Exception as exc:
        raise D365Error(
            f"Failed to store the secret in the OS keyring: {exc}." + _SECRET_FALLBACK
        ) from exc


def delete_secret(profile_name: str) -> bool:
    """Remove the stored secret. Returns True iff an entry existed. Soft on a
    missing keyring (returns False — nothing to delete).

    A backend error also returns False, so a flaky backend never breaks
    delete-password or the profile commands.
    """
    if not is_available():
        return False
    kr = _import_keyring()
    try:
        if kr.get_password(KEYRING_SERVICE, profile_name) is None:
            return False
        kr.delete_password(KEYRING_SERVICE, profile_name)
        return True
    except Exception:
        return False
