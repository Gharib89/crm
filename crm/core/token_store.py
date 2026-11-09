"""Encrypted store for a delegated sign-in's serialized MSAL token cache (#1144).

A delegated cache holds a refresh token carrying the user's full Dataverse role,
and one account serializes to far more than Windows Credential Manager's
2,560-byte blob limit, so the cache cannot live in the keyring. It is an
AES-256-GCM file in the state home; only its 32-byte key goes in the OS keyring
(service ``crm``, account ``token-cache-key:<profile>``, so it never collides
with a profile secret). With no usable keyring the file is plaintext ``0600``,
the same rule profile secrets follow, and ``save`` returns ``"plaintext"`` so the
caller surfaces the warning (``crm/core`` cannot import ``crm/commands``).

App sign-in's ``msal_token_cache.json`` is separate and unchanged.
"""

from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from crm.core import keyring_store
from crm.core.state_home import atomic_write, state_home

# Marks an encrypted file, so a plaintext file read after a keyring appeared (or
# an encrypted one read after it vanished) is told apart without guessing.
_MAGIC = b"CRMTC1\n"
_NONCE_LEN = 12
_KEY_LEN = 32


def cache_path(profile: str) -> Path:
    """The profile's cache file, named apart from app sign-in's cache."""
    return state_home() / f"msal_user_cache.{quote(profile, safe='')}.bin"


def _key_account(profile: str) -> str:
    return f"token-cache-key:{profile}"


def _read_key(profile: str) -> bytes | None:
    """The stored key, or ``None`` when absent, undecodable or the wrong size."""
    raw = keyring_store.get_secret(_key_account(profile))
    if raw is None:
        return None
    try:
        key = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None
    return key if len(key) == _KEY_LEN else None


def load(profile: str) -> str:
    """The serialized cache, or ``""``.

    Never raises on bad content: a missing, corrupt, truncated, tampered or
    wrong-key file, a missing key, or a plaintext/encrypted mismatch with the
    current keyring all read as an empty cache, which the caller reports as
    needing sign-in. I/O faults (permissions) still propagate so they never read
    as empty and get overwritten.
    """
    try:
        blob = cache_path(profile).read_bytes()
    except FileNotFoundError:
        return ""
    if blob.startswith(_MAGIC):
        key = _read_key(profile)
        body = blob[len(_MAGIC) :]
        if key is None or len(body) <= _NONCE_LEN:
            return ""
        try:
            return AESGCM(key).decrypt(body[:_NONCE_LEN], body[_NONCE_LEN:], None).decode("utf-8")
        except (InvalidTag, UnicodeDecodeError):
            return ""
    if keyring_store.is_available():
        return ""  # plaintext file from a keyring-less run: not trusted once a keyring exists
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def save(profile: str, serialized: str) -> Literal["keyring", "plaintext"]:
    """Write the cache atomically (``0600``) and say where the secret lives."""
    path = cache_path(profile)
    if not keyring_store.is_available():
        atomic_write(path, serialized.encode("utf-8"), mode=0o600, lock=True)
        return "plaintext"
    key = _read_key(profile)
    if key is None:
        key = AESGCM.generate_key(bit_length=256)
        keyring_store.set_secret(_key_account(profile), base64.b64encode(key).decode("ascii"))
    nonce = os.urandom(_NONCE_LEN)
    ciphertext = AESGCM(key).encrypt(nonce, serialized.encode("utf-8"), None)
    atomic_write(path, _MAGIC + nonce + ciphertext, mode=0o600, lock=True)
    return "keyring"
