# pyright: basic
"""Unit tests for the core `connection_doctor` probe chain (#74, Task 1).

Each layer's failure is mocked independently. The socket step issues a RAW
socket (not via requests), so `requests_mock` does NOT intercept it — we
monkeypatch `socket.create_connection` (looked up as
`crm.core.connection.socket.create_connection`) in every test that needs to
reach steps 2-5.
"""
from __future__ import annotations

import socket

import pytest
import requests
import requests_mock

from crm.core import connection as conn
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error

_BASE = "https://internalcrm.contoso.local/Contoso"


def _backend(url: str = _BASE, *, api_version: str = "v9.2", verify_ssl: bool = True) -> D365Backend:
    profile = ConnectionProfile(
        name="t",
        url=url,
        domain="CONTOSO",
        username="alice",
        api_version=api_version,
        verify_ssl=verify_ssl,
    )
    return D365Backend(profile, "pw")


class _DummySock:
    """Stand-in for the socket returned by create_connection; .close() is a no-op."""

    def close(self) -> None:
        return None


@pytest.fixture
def socket_ok(monkeypatch):
    """Make the raw socket step succeed so tests can reach the HTTP layers."""
    monkeypatch.setattr(
        conn.socket, "create_connection", lambda *a, **k: _DummySock()
    )


def _by_name(result):
    return {c["check"]: c for c in result["checks"]}


def _check_names(result):
    return [c["check"] for c in result["checks"]]


_EXPECTED_ORDER = ["dns_tcp", "tls", "version", "auth", "rate_limit"]


# ── shape ────────────────────────────────────────────────────────────────


def test_always_five_checks_in_order(socket_ok):
    base = _BASE
    with requests_mock.Mocker() as m:
        m.get(f"{base}/api/data/v9.2/", status_code=401)
        m.get(f"{base}/api/data/v9.2/RetrieveVersion()", json={"Version": "9.1.0.1"})
        m.get(f"{base}/api/data/v9.2/WhoAmI", json={"UserId": "u"})
        result = conn.connection_doctor(_backend())
    assert _check_names(result) == _EXPECTED_ORDER
    for c in result["checks"]:
        assert set(c) == {"check", "ok", "detail", "hint"}
        assert isinstance(c["hint"], str)


# ── step 1: dns_tcp ────────────────────────────────────────────────────────


def test_dns_failure_short_circuits(monkeypatch):
    def _raise(*a, **k):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(conn.socket, "create_connection", _raise)
    result = conn.connection_doctor(_backend())

    checks = _by_name(result)
    assert checks["dns_tcp"]["ok"] is False
    assert "DNS" in checks["dns_tcp"]["detail"]
    assert checks["dns_tcp"]["hint"]  # non-empty hint
    for name in ("tls", "version", "auth"):
        assert checks[name]["ok"] is False
        assert checks[name]["detail"] == "not checked (network unreachable)"
        assert checks[name]["hint"] == ""
    assert checks["rate_limit"]["ok"] is True
    assert result["ok"] is False


def test_tcp_refused_short_circuits(monkeypatch):
    def _raise(*a, **k):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(conn.socket, "create_connection", _raise)
    result = conn.connection_doctor(_backend())

    checks = _by_name(result)
    assert checks["dns_tcp"]["ok"] is False
    assert "cannot reach" in checks["dns_tcp"]["detail"]
    assert checks["dns_tcp"]["hint"]
    assert checks["tls"]["detail"] == "not checked (network unreachable)"
    assert result["ok"] is False


def test_socket_uses_default_https_port(monkeypatch):
    captured = {}

    def _capture(addr, timeout=None):
        captured["addr"] = addr
        captured["timeout"] = timeout
        return _DummySock()

    monkeypatch.setattr(conn.socket, "create_connection", _capture)
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/", status_code=401)
        m.get(f"{_BASE}/api/data/v9.2/RetrieveVersion()", json={"Version": "9.1.0.1"})
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", json={"UserId": "u"})
        conn.connection_doctor(_backend())
    assert captured["addr"] == ("internalcrm.contoso.local", 443)
    assert captured["timeout"] == 120  # default profile timeout


# ── step 2: tls ────────────────────────────────────────────────────────────


def test_tls_certificate_untrusted_short_circuits(socket_ok):
    with requests_mock.Mocker() as m:
        m.get(
            f"{_BASE}/api/data/v9.2/",
            exc=requests.exceptions.SSLError("certificate verify failed"),
        )
        result = conn.connection_doctor(_backend())

    checks = _by_name(result)
    assert checks["tls"]["ok"] is False
    assert "certificate" in checks["tls"]["detail"].lower()
    assert "--no-verify-ssl" in checks["tls"]["hint"]
    for name in ("version", "auth"):
        assert checks[name]["ok"] is False
        assert checks[name]["detail"] == "not checked (TLS/connection failed)"
    assert checks["rate_limit"]["ok"] is True
    assert result["ok"] is False


def test_tls_connection_error_short_circuits(socket_ok):
    with requests_mock.Mocker() as m:
        m.get(
            f"{_BASE}/api/data/v9.2/",
            exc=requests.exceptions.ConnectionError("connection reset"),
        )
        result = conn.connection_doctor(_backend())

    checks = _by_name(result)
    assert checks["tls"]["ok"] is False
    # not the SSL cert hint
    assert "--no-verify-ssl" not in checks["tls"]["hint"]
    assert checks["version"]["detail"] == "not checked (TLS/connection failed)"
    assert result["ok"] is False


@pytest.mark.parametrize(
    "exc_cls",
    [requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout],
)
def test_tls_timeout_short_circuits(socket_ok, exc_cls):
    # A reachable-but-slow HTTPS server (read/connect timeout) must NOT crash
    # the probe — Timeout is a sibling of ConnectionError under RequestException.
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/", exc=exc_cls("timed out"))
        result = conn.connection_doctor(_backend())

    # _abort still emits all five checks in the canonical order
    assert _check_names(result) == _EXPECTED_ORDER
    checks = _by_name(result)
    assert checks["tls"]["ok"] is False
    assert "--no-verify-ssl" not in checks["tls"]["hint"]  # not the SSL cert hint
    for name in ("version", "auth"):
        assert checks[name]["ok"] is False
        assert checks[name]["detail"] == "not checked (TLS/connection failed)"
    assert checks["rate_limit"]["ok"] is True
    assert result["ok"] is False


def test_tls_any_http_response_is_ok(socket_ok):
    # Even a 404 on api_base means the TLS handshake worked.
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/", status_code=404)
        m.get(f"{_BASE}/api/data/v9.2/RetrieveVersion()", json={"Version": "9.1.0.1"})
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", json={"UserId": "u"})
        result = conn.connection_doctor(_backend())
    checks = _by_name(result)
    assert checks["tls"]["ok"] is True
    assert checks["tls"]["detail"] == "TLS handshake OK"


def test_tls_probes_even_when_verify_disabled(socket_ok):
    # verify_ssl=False must STILL issue the api_base GET (so a genuinely broken
    # handshake/connection is caught). On success the detail notes verification
    # is disabled.
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/", status_code=401)
        m.get(f"{_BASE}/api/data/v9.2/RetrieveVersion()", json={"Version": "9.1.0.1"})
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", json={"UserId": "u"})
        result = conn.connection_doctor(_backend(verify_ssl=False))
        # the api_base GET was actually issued
        api_base_hits = [
            # match the path case-insensitively — don't depend on requests_mock's
            # path-casing (it lowercases r.path); URLs are registered cased.
            r for r in m.request_history if r.path.lower().endswith("/api/data/v9.2/")
        ]
        assert len(api_base_hits) == 1
    checks = _by_name(result)
    assert checks["tls"]["ok"] is True
    assert "verification disabled" in checks["tls"]["detail"]


def test_tls_connection_error_caught_even_when_verify_disabled(socket_ok):
    # With verify off no SSLError fires, but a ConnectionError still must mark
    # tls as failed — proving the probe is no longer skipped.
    with requests_mock.Mocker() as m:
        m.get(
            f"{_BASE}/api/data/v9.2/",
            exc=requests.exceptions.ConnectionError("connection reset"),
        )
        result = conn.connection_doctor(_backend(verify_ssl=False))
    checks = _by_name(result)
    assert checks["tls"]["ok"] is False
    assert checks["version"]["detail"] == "not checked (TLS/connection failed)"
    assert result["ok"] is False


def test_tls_not_applicable_for_http(socket_ok):
    base = "http://internalcrm.contoso.local/Contoso"
    with requests_mock.Mocker() as m:
        m.get(f"{base}/api/data/v9.2/", status_code=401)
        m.get(f"{base}/api/data/v9.2/RetrieveVersion()", json={"Version": "9.1.0.1"})
        m.get(f"{base}/api/data/v9.2/WhoAmI", json={"UserId": "u"})
        result = conn.connection_doctor(_backend(url=base))
    checks = _by_name(result)
    assert checks["tls"]["ok"] is True
    assert "http" in checks["tls"]["detail"].lower()


# ── step 3: version ────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [501, 404])
def test_wrong_api_version_no_sweep(socket_ok, status):
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/", status_code=401)
        m.get(f"{_BASE}/api/data/v9.2/RetrieveVersion()", status_code=status)
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", json={"UserId": "u"})
        result = conn.connection_doctor(_backend())
        # exactly one version probe; no sweep across other api_versions
        version_hits = [
            r for r in m.request_history if r.path.lower().endswith("/retrieveversion()")
        ]
        assert len(version_hits) == 1
        assert "/v9.1/" not in "".join(r.url for r in m.request_history)

    checks = _by_name(result)
    assert checks["version"]["ok"] is False
    assert "v9.2" in checks["version"]["detail"]
    assert str(status) in checks["version"]["detail"]
    assert checks["version"]["hint"]  # renegotiate hint
    # auth is still attempted (version failing does NOT block auth)
    assert checks["auth"]["ok"] is True
    # version is informational-excluded? No — version counts; overall must be False.
    assert result["ok"] is False


def test_version_transport_failure_is_nonfatal(socket_ok):
    # Socket + TLS pass, but the connection drops on the RetrieveVersion GET
    # (server reset/restart). The probe must NOT crash: version is a failed
    # check with a transport detail, and auth is STILL attempted.
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/", status_code=401)  # TLS ok
        m.get(
            f"{_BASE}/api/data/v9.2/RetrieveVersion()",
            exc=requests.exceptions.ConnectionError("connection reset by peer"),
        )
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", json={"UserId": "u"})
        result = conn.connection_doctor(_backend())

    # full five-check result returned (no exception propagated)
    assert _check_names(result) == _EXPECTED_ORDER
    checks = _by_name(result)
    assert checks["tls"]["ok"] is True
    assert checks["version"]["ok"] is False
    assert "RetrieveVersion" in checks["version"]["detail"]
    assert "connection reset by peer" in checks["version"]["detail"]
    # auth still attempted and succeeded
    assert checks["auth"]["ok"] is True
    assert result["ok"] is False


def test_version_success_surfaces_version(socket_ok):
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/", status_code=401)
        m.get(f"{_BASE}/api/data/v9.2/RetrieveVersion()", json={"Version": "9.2.1.2"})
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", json={"UserId": "u"})
        result = conn.connection_doctor(_backend())
    checks = _by_name(result)
    assert checks["version"]["ok"] is True
    assert "9.2.1.2" in checks["version"]["detail"]


def test_version_unexpected_status_no_hint(socket_ok):
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/", status_code=401)
        m.get(f"{_BASE}/api/data/v9.2/RetrieveVersion()", status_code=401)
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", json={"UserId": "u"})
        result = conn.connection_doctor(_backend())
    checks = _by_name(result)
    assert checks["version"]["ok"] is False
    assert "401" in checks["version"]["detail"]
    assert checks["version"]["hint"] == ""


# ── step 4: auth ───────────────────────────────────────────────────────────


def test_auth_401_with_credentials_hint(socket_ok):
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/", status_code=401)
        m.get(f"{_BASE}/api/data/v9.2/RetrieveVersion()", json={"Version": "9.1.0.1"})
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", status_code=401)
        result = conn.connection_doctor(_backend())
    checks = _by_name(result)
    assert checks["auth"]["ok"] is False
    assert "401" in checks["auth"]["detail"]
    assert "credentials" in checks["auth"]["hint"].lower()
    assert result["ok"] is False


def test_auth_403_privileges_hint(socket_ok):
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/", status_code=401)
        m.get(f"{_BASE}/api/data/v9.2/RetrieveVersion()", json={"Version": "9.1.0.1"})
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", status_code=403)
        result = conn.connection_doctor(_backend())
    checks = _by_name(result)
    assert checks["auth"]["ok"] is False
    assert "403" in checks["auth"]["detail"]
    assert "privilege" in checks["auth"]["hint"].lower() or "role" in checks["auth"]["hint"].lower()


def test_auth_transport_failure_is_nonfatal(socket_ok):
    # Connection drops on the WhoAmI GET (read timeout / reset) after TLS +
    # version passed. auth becomes a failed check with a transport detail and
    # the full five-check result is still returned (no exception).
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/", status_code=401)  # TLS ok
        m.get(f"{_BASE}/api/data/v9.2/RetrieveVersion()", json={"Version": "9.2.1.2"})
        m.get(
            f"{_BASE}/api/data/v9.2/WhoAmI",
            exc=requests.exceptions.ReadTimeout("read timed out"),
        )
        result = conn.connection_doctor(_backend())

    assert _check_names(result) == _EXPECTED_ORDER
    checks = _by_name(result)
    assert checks["version"]["ok"] is True
    assert checks["auth"]["ok"] is False
    assert "WhoAmI" in checks["auth"]["detail"]
    assert "read timed out" in checks["auth"]["detail"]
    assert result["ok"] is False


def test_auth_success_surfaces_user(socket_ok):
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/", status_code=401)
        m.get(f"{_BASE}/api/data/v9.2/RetrieveVersion()", json={"Version": "9.1.0.1"})
        m.get(
            f"{_BASE}/api/data/v9.2/WhoAmI",
            json={"UserId": "00000000-0000-0000-0000-000000000001"},
        )
        result = conn.connection_doctor(_backend())
    checks = _by_name(result)
    assert checks["auth"]["ok"] is True
    assert "00000000-0000-0000-0000-000000000001" in checks["auth"]["detail"]


# ── step 5: rate_limit ─────────────────────────────────────────────────────


def test_rate_limit_headers_surfaced(socket_ok):
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/", status_code=401)
        m.get(f"{_BASE}/api/data/v9.2/RetrieveVersion()", json={"Version": "9.1.0.1"})
        m.get(
            f"{_BASE}/api/data/v9.2/WhoAmI",
            json={"UserId": "u"},
            headers={
                "Retry-After": "30",
                "x-ms-ratelimit-burst-remaining-xrm-requests": "5000",
            },
        )
        result = conn.connection_doctor(_backend())
    checks = _by_name(result)
    assert checks["rate_limit"]["ok"] is True
    assert "30" in checks["rate_limit"]["detail"]
    assert "5000" in checks["rate_limit"]["detail"]


def test_rate_limit_absent(socket_ok):
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/", status_code=401)
        m.get(f"{_BASE}/api/data/v9.2/RetrieveVersion()", json={"Version": "9.1.0.1"})
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", json={"UserId": "u"})
        result = conn.connection_doctor(_backend())
    checks = _by_name(result)
    assert checks["rate_limit"]["ok"] is True
    assert checks["rate_limit"]["detail"] == "no rate-limit headers present"


# ── happy path ─────────────────────────────────────────────────────────────


def test_all_green(socket_ok):
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/", status_code=401)  # TLS-only probe; 401 is fine
        m.get(f"{_BASE}/api/data/v9.2/RetrieveVersion()", json={"Version": "9.2.1.2"})
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", json={"UserId": "u"})
        result = conn.connection_doctor(_backend())
    assert result["ok"] is True
    for c in result["checks"]:
        assert c["ok"] is True, c


# ── standard OData headers on the raw GETs ───────────────────────────────────


def test_probes_send_standard_odata_headers(socket_ok):
    # The raw doctor GETs must carry the same headers as the real client so they
    # don't diverge (406/415/non-JSON on some Dataverse endpoints).
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/", status_code=401)
        m.get(f"{_BASE}/api/data/v9.2/RetrieveVersion()", json={"Version": "9.2.1.2"})
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", json={"UserId": "u"})
        conn.connection_doctor(_backend())
        version_req = next(
            r for r in m.request_history if r.path.lower().endswith("/retrieveversion()")
        )
        auth_req = next(r for r in m.request_history if r.path.lower().endswith("/whoami"))
    # requests headers are case-insensitive
    for req in (version_req, auth_req):
        assert req.headers["Accept"] == "application/json"
        assert req.headers["OData-Version"] == "4.0"


# ── malformed profile URL (Fix 1) ────────────────────────────────────────────


def test_profile_url_no_hostname_fails_dns_tcp():
    # No socket monkeypatch: a hostname-less URL must be rejected up front,
    # never reaching socket.create_connection.
    result = conn.connection_doctor(_backend(url="not-a-url"))
    assert _check_names(result) == _EXPECTED_ORDER
    checks = _by_name(result)
    assert checks["dns_tcp"]["ok"] is False
    assert "no hostname" in checks["dns_tcp"]["detail"]
    assert checks["dns_tcp"]["hint"]
    for name in ("tls", "version", "auth"):
        assert checks[name]["ok"] is False
        assert checks[name]["detail"] == "not checked (invalid profile URL)"
    assert checks["rate_limit"]["ok"] is True
    assert result["ok"] is False


def test_profile_url_invalid_port_fails_dns_tcp():
    # urlparse(...).port raises ValueError on a bad port — must be caught and
    # reported as a dns_tcp failure, not crash the probe.
    result = conn.connection_doctor(_backend(url="https://host:bad/org"))
    assert _check_names(result) == _EXPECTED_ORDER
    checks = _by_name(result)
    assert checks["dns_tcp"]["ok"] is False
    assert "invalid port" in checks["dns_tcp"]["detail"]
    assert checks["dns_tcp"]["hint"]
    for name in ("tls", "version", "auth"):
        assert checks[name]["detail"] == "not checked (invalid profile URL)"
    assert result["ok"] is False


# ── auth-handler D365Error (OAuth token acquisition) is non-fatal ──────────
# An OAuth profile acquires its bearer token inside the requests auth handler
# *during* the GET, which raises D365Error (not a requests exception) on a
# token/setup failure. The probe must degrade, not crash.


def _raise_d365(*a, **k):
    raise D365Error("OAuth token acquisition failed", status=401)


def test_doctor_d365error_at_tls_is_nonfatal(socket_ok, monkeypatch):
    b = _backend()
    # session.get raising D365Error mirrors the OAuth auth handler failing on the
    # first (tls) GET — before any HTTP response.
    monkeypatch.setattr(b.session, "get", _raise_d365)
    result = conn.connection_doctor(b)  # must not raise
    assert _check_names(result) == _EXPECTED_ORDER
    checks = _by_name(result)
    assert checks["dns_tcp"]["ok"] is True
    assert checks["tls"]["ok"] is False
    assert "authenticate" in checks["tls"]["detail"].lower()
    assert "OAuth token acquisition failed" in checks["tls"]["detail"]
    for name in ("version", "auth"):
        assert checks[name]["detail"] == "not checked (request auth/setup failed)"
    assert result["ok"] is False


def test_doctor_version_d365error_is_nonfatal(monkeypatch):
    b = _backend()
    monkeypatch.setattr(b.session, "get", _raise_d365)
    c = conn._doctor_version(b, [])  # must not raise
    assert c["check"] == "version" and c["ok"] is False
    assert "OAuth token acquisition failed" in c["detail"]


def test_doctor_auth_d365error_is_nonfatal(monkeypatch):
    b = _backend()
    monkeypatch.setattr(b.session, "get", _raise_d365)
    c = conn._doctor_auth(b, [])  # must not raise
    assert c["check"] == "auth" and c["ok"] is False
    assert "OAuth token acquisition failed" in c["detail"]
