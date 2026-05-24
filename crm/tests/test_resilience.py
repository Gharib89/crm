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
