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

# Backend module name keyring uses when no real backend is available.
_NULL_BACKEND_MODULE = "keyring.backends.fail"

# Reused by both failure paths (import failure, no usable backend).
_SECRET_FALLBACK = (
    " Meanwhile, store the secret with `crm profile set-password "
    "--profile <name> --store-password-plaintext`, or pass `--password` "
    "for a single run."
)


def _missing_keyring_message() -> str:
    """keyring is a core dependency, so this only fires on a broken install. The
    remedy depends on how crm was installed — a frozen binary can't pip-install,
    a `uv tool` install needs uv — so point at the install method, not a single
    (often wrong for this machine) pip command."""
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
    try:
        # Imported lazily (not at module top) to keep the CLI fast path cheap —
        # keyring is only needed when a secret is actually stored or read.
        import keyring
    except ImportError as exc:
        raise D365Error(_missing_keyring_message()) from exc
    return keyring


def is_available() -> bool:
    """True only when keyring is importable AND a real backend is configured."""
    try:
        kr = _import_keyring()
    except D365Error:
        return False
    try:
        backend = kr.get_keyring()
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:
        # A backend can panic at the Rust layer (pyo3) when its native bindings
        # can't load — that raises pyo3_runtime.PanicException, a BaseException
        # subclass, not Exception (issue #308). "Unusable" is exactly this case.
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


def get_secret_or_raise(profile_name: str) -> str | None:
    """Like get_secret, but a flaky/locked backend raises D365Error instead of
    soft-failing to None. Used by callers (profile rename) that must tell "no
    secret was ever stored" apart from "the backend errored reading it" so they
    can surface a recovery-hint warning instead of silently skipping migration."""
    if not is_available():
        return None
    try:
        return _import_keyring().get_password(KEYRING_SERVICE, profile_name)
    except Exception as exc:
        raise D365Error(f"Keyring read failed: {exc}") from exc


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


def delete_secret_or_raise(profile_name: str) -> bool:
    """Like delete_secret, but a flaky/locked backend raises D365Error instead of
    soft-failing to False. See get_secret_or_raise."""
    if not is_available():
        return False
    kr = _import_keyring()
    try:
        if kr.get_password(KEYRING_SERVICE, profile_name) is None:
            return False
        kr.delete_password(KEYRING_SERVICE, profile_name)
        return True
    except Exception as exc:
        raise D365Error(f"Keyring delete failed: {exc}") from exc


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
