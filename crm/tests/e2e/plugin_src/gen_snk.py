#!/usr/bin/env python3
"""Regenerate ``NoOpPlugin.snk`` — the strong-name key the e2e plug-in is signed with.

The committed ``.snk`` is a **throwaway test signing key**, not a secret: strong
names are an identity/versioning mechanism, not a security boundary, and this key
signs only the no-op plug-in built for the assembly-lifecycle e2e test. It is kept
auditable by regenerating it from this script rather than treating it as an opaque
artifact.

A ``.snk`` is a Windows CryptoAPI ``PRIVATEKEYBLOB`` (the format ``sn.exe -k``
emits). There is no cross-platform tool that writes one, so this script generates
an RSA key with ``openssl`` (the only external dependency, present on the CI
runner and on dev machines) and serialises it into the blob layout by hand, with a
round-trip self-check.

The signed assembly's *public key token* is **not** pinned here: the e2e fixture
reads it back from the built ``.dll`` (see ``NoOpPlugin.csproj`` /
``conftest.py::plugin_assembly``), so regenerating the key with a fresh RSA pair
needs no other change. The token this script prints is a convenience cross-check.

Usage::

    python crm/tests/e2e/plugin_src/gen_snk.py
"""
from __future__ import annotations

import hashlib
import struct
import subprocess
from pathlib import Path

# CryptoAPI constants (wincrypt.h).
_BLOB_PRIVATEKEY = 0x07
_BLOB_PUBLICKEY = 0x06
_BLOB_VERSION = 0x02
_CALG_RSA_SIGN = 0x00002400
_CALG_SHA1 = 0x00008004
_MAGIC_RSA2 = 0x32415352  # "RSA2" — private key blob
_MAGIC_RSA1 = 0x31415352  # "RSA1" — public key blob


def _gen_pkcs1_der() -> bytes:
    """An RSA-2048 private key as PKCS#1 (RSAPrivateKey) DER, via openssl."""
    pem = subprocess.run(
        ["openssl", "genrsa", "2048"],
        check=True, capture_output=True).stdout
    return subprocess.run(
        ["openssl", "rsa", "-traditional", "-outform", "DER"],
        input=pem, check=True, capture_output=True).stdout


def _der_ints(der: bytes) -> list[int]:
    """Parse a DER ``SEQUENCE`` of ``INTEGER``s into a list of ints (minimal ASN.1)."""
    def read_len(buf: bytes, i: int) -> tuple[int, int]:
        n = buf[i]
        i += 1
        if n < 0x80:
            return n, i
        count = n & 0x7F
        return int.from_bytes(buf[i:i + count], "big"), i + count

    assert der[0] == 0x30, "expected top-level SEQUENCE"
    _, i = read_len(der, 1)
    out: list[int] = []
    while i < len(der):
        assert der[i] == 0x02, "expected INTEGER"
        length, i = read_len(der, i + 1)
        out.append(int.from_bytes(der[i:i + length], "big"))
        i += length
    return out


def _le(value: int, nbytes: int) -> bytes:
    """``value`` as little-endian, fixed width (CryptoAPI blob byte order)."""
    return value.to_bytes(nbytes, "little")


def _private_key_blob(n: int, e: int, d: int, p: int, q: int,
                      dp: int, dq: int, qinv: int, bits: int) -> bytes:
    """Serialise the RSA key as a CryptoAPI PRIVATEKEYBLOB (a ``.snk``)."""
    half = bits // 16   # keylen/2 bytes
    full = bits // 8    # keylen bytes
    blob = struct.pack("<BBHI", _BLOB_PRIVATEKEY, _BLOB_VERSION, 0, _CALG_RSA_SIGN)
    blob += struct.pack("<III", _MAGIC_RSA2, bits, e)
    blob += _le(n, full)
    blob += _le(p, half) + _le(q, half)
    blob += _le(dp, half) + _le(dq, half) + _le(qinv, half)
    blob += _le(d, full)
    return blob


def _public_key_token(n: int, e: int, bits: int) -> str:
    """The strong-name public key token: low 8 bytes of SHA-1(public-key blob), reversed."""
    pub = struct.pack("<BBHI", _BLOB_PUBLICKEY, _BLOB_VERSION, 0, _CALG_RSA_SIGN)
    pub += struct.pack("<III", _MAGIC_RSA1, bits, e)
    pub += _le(n, bits // 8)
    snk_pubkey = struct.pack("<III", _CALG_RSA_SIGN, _CALG_SHA1, len(pub)) + pub
    digest = hashlib.sha1(snk_pubkey).digest()
    return digest[-8:][::-1].hex()


def main() -> None:
    bits = 2048
    _, n, e, d, p, q, dp, dq, qinv = _der_ints(_gen_pkcs1_der())
    blob = _private_key_blob(n, e, d, p, q, dp, dq, qinv, bits)

    # Self-check: the blob must round-trip and describe a consistent RSA key.
    off = 8 + 12
    half, full = bits // 16, bits // 8
    rn = int.from_bytes(blob[off:off + full], "little")
    rp = int.from_bytes(blob[off + full:off + full + half], "little")
    rq = int.from_bytes(blob[off + full + half:off + full + 2 * half], "little")
    assert rn == n and rp == p and rq == q, "blob round-trip mismatch"
    assert p * q == n, "inconsistent RSA key"

    out = Path(__file__).with_name("NoOpPlugin.snk")
    out.write_bytes(blob)
    print(f"wrote {out} ({len(blob)} bytes)")
    print(f"public key token (cross-check): {_public_key_token(n, e, bits)}")


if __name__ == "__main__":
    main()
