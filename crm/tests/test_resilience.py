"""Unit tests for the retry + async-poll resilience layer.

All HTTP is mocked. No live D365 server needed.
"""
# pyright: basic

from __future__ import annotations

import time
from typing import Any
from unittest.mock import patch

import pytest
import requests
import requests_mock

from crm.utils.d365_backend import (
    ConnectionProfile,
    D365Backend,
    D365Error,
    _compute_delay,
    _is_response_retryable,
    _is_transport_retryable,
    _log_rate_limit_headers,
    _parse_retry_after,
)


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def profile() -> ConnectionProfile:
    return ConnectionProfile(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
        verify_ssl=False,
        retry_max=3,
        retry_base_delay=0.1,
        retry_max_delay=2.0,
        retry_jitter=False,
        async_poll_initial=0.05,
        async_poll_max=0.2,
        async_timeout=2,
    )


@pytest.fixture
def backend(profile, monkeypatch):
    # Disable any inherited env vars so the profile drives behavior.
    for var in (
        "CRM_RETRY_MAX",
        "CRM_RETRY_BASE_DELAY",
        "CRM_RETRY_MAX_DELAY",
        "CRM_RETRY_JITTER",
        "CRM_ASYNC_TIMEOUT",
        "CRM_NO_RETRY",
        "CRM_VERBOSE",
    ):
        monkeypatch.delenv(var, raising=False)
    return D365Backend(profile, password="pw", dry_run=False)


# ── _parse_retry_after ──────────────────────────────────────────────────


class TestParseRetryAfter:
    def test_missing_returns_none(self):
        assert _parse_retry_after(None) is None

    def test_empty_returns_none(self):
        assert _parse_retry_after("") is None

    def test_integer_seconds(self):
        assert _parse_retry_after("30") == 30.0

    def test_float_seconds(self):
        assert _parse_retry_after("12.5") == 12.5

    def test_negative_clamped_to_zero(self):
        assert _parse_retry_after("-5") == 0.0

    def test_http_date(self):
        # 2026-05-24T12:00:30Z — exact value depends on parser; just assert > 0
        result = _parse_retry_after("Sun, 24 May 2026 12:00:30 GMT")
        assert result is not None and result >= 0.0

    def test_garbage_returns_none(self):
        assert _parse_retry_after("not-a-date") is None


# ── _compute_delay ──────────────────────────────────────────────────────


class TestComputeDelay:
    def test_retry_after_honored_below_cap(self, profile):
        # retry_max_delay=2.0; retry_after=1.0 → return 1.0
        assert _compute_delay(0, profile, retry_after=1.0) == 1.0

    def test_retry_after_clamped_to_max(self, profile):
        # retry_max_delay=2.0; retry_after=99.0 → clamp to 2.0
        assert _compute_delay(0, profile, retry_after=99.0) == 2.0

    def test_no_jitter_exponential(self, profile):
        # base_delay=0.1; attempt 0 → 0.1; attempt 1 → 0.2; attempt 2 → 0.4
        assert _compute_delay(0, profile, retry_after=None) == 0.1
        assert _compute_delay(1, profile, retry_after=None) == 0.2
        assert _compute_delay(2, profile, retry_after=None) == 0.4

    def test_no_jitter_caps_at_max(self, profile):
        # base_delay=0.1, 2**10=1024 → 102.4, but cap is 2.0
        assert _compute_delay(10, profile, retry_after=None) == 2.0

    def test_jitter_bounded(self, profile):
        profile.retry_jitter = True
        # 1000 draws at attempt=2 should all sit in [0, 0.4]
        for _ in range(1000):
            d = _compute_delay(2, profile, retry_after=None)
            assert 0.0 <= d <= 0.4


# ── _is_response_retryable ──────────────────────────────────────────────


class TestIsResponseRetryable:
    @pytest.mark.parametrize("method,status,expected", [
        # 429 retryable on any method
        ("GET", 429, True),
        ("POST", 429, True),
        ("PATCH", 429, True),
        # 503 retryable on any method
        ("GET", 503, True),
        ("POST", 503, True),
        # 502/504 retryable on idempotent methods only
        ("GET", 502, True),
        ("PATCH", 502, True),
        ("DELETE", 504, True),
        ("POST", 502, False),
        ("POST", 504, False),
        # 2xx, 4xx never retryable
        ("GET", 200, False),
        ("GET", 400, False),
        ("GET", 401, False),
        ("GET", 404, False),
        ("POST", 200, False),
        # 5xx other than 502/503/504 never retryable
        ("GET", 500, False),
        ("GET", 505, False),
    ])
    def test_truth_table(self, method, status, expected):
        resp = requests.Response()
        resp.status_code = status
        assert _is_response_retryable(resp, method) is expected


# ── _is_transport_retryable ─────────────────────────────────────────────


class TestIsTransportRetryable:
    def test_connection_error_retryable(self):
        assert _is_transport_retryable(requests.exceptions.ConnectionError()) is True

    def test_timeout_retryable(self):
        assert _is_transport_retryable(requests.exceptions.Timeout()) is True

    def test_chunked_encoding_error_retryable(self):
        assert _is_transport_retryable(requests.exceptions.ChunkedEncodingError()) is True

    def test_ssl_error_not_retryable(self):
        assert _is_transport_retryable(requests.exceptions.SSLError()) is False

    def test_invalid_url_not_retryable(self):
        assert _is_transport_retryable(requests.exceptions.InvalidURL()) is False

    def test_generic_runtime_error_not_retryable(self):
        assert _is_transport_retryable(RuntimeError("boom")) is False


# ── _log_rate_limit_headers ─────────────────────────────────────────────


class TestLogRateLimitHeaders:
    def _make_resp(self, headers: dict[str, str]) -> requests.Response:
        resp = requests.Response()
        resp.status_code = 429
        for k, v in headers.items():
            resp.headers[k] = v
        return resp

    def test_on_429_logs_all_present_headers(self, capsys):
        resp = self._make_resp({
            "x-ms-ratelimit-time-remaining-xrm-requests": "30",
            "x-ms-ratelimit-burst-remaining-xrm-requests": "5",
            "x-ms-ratelimit-limit-xrm-requests": "6000",
            "Retry-After": "12",
        })
        _log_rate_limit_headers(resp, on_429=True)
        err = capsys.readouterr().err
        assert "ratelimit" in err
        assert "time-remaining=30" in err
        assert "burst-remaining=5" in err
        assert "limit=6000" in err
        assert "retry-after=12" in err

    def test_on_429_no_headers_emits_no_line(self, capsys):
        resp = self._make_resp({})
        _log_rate_limit_headers(resp, on_429=True)
        assert capsys.readouterr().err == ""

    def test_verbose_off_silent_on_2xx(self, capsys, monkeypatch):
        monkeypatch.delenv("CRM_VERBOSE", raising=False)
        resp = self._make_resp({"x-ms-ratelimit-time-remaining-xrm-requests": "30"})
        resp.status_code = 200
        _log_rate_limit_headers(resp, on_429=False)
        assert capsys.readouterr().err == ""

    def test_verbose_on_logs_2xx(self, capsys, monkeypatch):
        monkeypatch.setenv("CRM_VERBOSE", "1")
        resp = self._make_resp({"x-ms-ratelimit-time-remaining-xrm-requests": "30"})
        resp.status_code = 200
        _log_rate_limit_headers(resp, on_429=False)
        assert "time-remaining=30" in capsys.readouterr().err

    def test_partial_headers_only_logs_present(self, capsys):
        resp = self._make_resp({"x-ms-ratelimit-time-remaining-xrm-requests": "30"})
        _log_rate_limit_headers(resp, on_429=True)
        err = capsys.readouterr().err
        assert "time-remaining=30" in err
        assert "burst-remaining" not in err
        assert "limit=" not in err
        assert "retry-after" not in err
