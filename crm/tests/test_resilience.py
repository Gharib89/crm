"""Unit tests for the retry resilience layer.

PR2 will add async-poll coverage. All HTTP is mocked; no live D365 server needed.
"""
# pyright: basic

from __future__ import annotations

import time
from typing import Any

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
    _resolve_retry_max,
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
    for var in ("CRM_RETRY_MAX", "CRM_NO_RETRY", "CRM_VERBOSE"):
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
        # Use a dynamic future date so the positive-delta branch is always exercised.
        import datetime as _dt
        from email.utils import format_datetime
        future = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=60)
        result = _parse_retry_after(format_datetime(future))
        assert result is not None and result > 0.0

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
        _log_rate_limit_headers(resp, on_retryable=True)
        err = capsys.readouterr().err
        assert "ratelimit" in err
        assert "time-remaining=30" in err
        assert "burst-remaining=5" in err
        assert "limit=6000" in err
        assert "retry-after=12" in err

    def test_on_429_no_headers_emits_no_line(self, capsys):
        resp = self._make_resp({})
        _log_rate_limit_headers(resp, on_retryable=True)
        assert capsys.readouterr().err == ""

    def test_verbose_off_silent_on_2xx(self, capsys, monkeypatch):
        monkeypatch.delenv("CRM_VERBOSE", raising=False)
        resp = self._make_resp({"x-ms-ratelimit-time-remaining-xrm-requests": "30"})
        resp.status_code = 200
        _log_rate_limit_headers(resp, on_retryable=False)
        assert capsys.readouterr().err == ""

    def test_verbose_on_logs_2xx(self, capsys, monkeypatch):
        monkeypatch.setenv("CRM_VERBOSE", "1")
        resp = self._make_resp({"x-ms-ratelimit-time-remaining-xrm-requests": "30"})
        resp.status_code = 200
        _log_rate_limit_headers(resp, on_retryable=False)
        assert "time-remaining=30" in capsys.readouterr().err

    def test_partial_headers_only_logs_present(self, capsys):
        resp = self._make_resp({"x-ms-ratelimit-time-remaining-xrm-requests": "30"})
        _log_rate_limit_headers(resp, on_retryable=True)
        err = capsys.readouterr().err
        assert "time-remaining=30" in err
        assert "burst-remaining" not in err
        assert "limit=" not in err
        assert "retry-after" not in err


# ── Retry loop integration ──────────────────────────────────────────────


class TestRetryLoop:
    def test_429_then_success(self, backend, monkeypatch):
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        url = backend.url_for("WhoAmI")
        with requests_mock.Mocker() as m:
            m.get(url, [
                {"status_code": 429, "headers": {"Retry-After": "0"}, "text": ""},
                {"status_code": 200, "json": {"UserId": "00000000-0000-0000-0000-000000000001"}},
            ])
            result = backend.get("WhoAmI")
        assert isinstance(result, dict)
        assert result["UserId"] == "00000000-0000-0000-0000-000000000001"
        assert len(sleeps) == 1
        assert sleeps[0] == 0.0  # Retry-After: 0

    def test_429_exhausts_then_raises(self, backend, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        url = backend.url_for("WhoAmI")
        with requests_mock.Mocker() as m:
            m.get(url, status_code=429, headers={"Retry-After": "0"},
                  json={"error": {"code": "0x80072322", "message": "Rate limited"}})
            with pytest.raises(D365Error) as exc_info:
                backend.get("WhoAmI")
        assert exc_info.value.status == 429
        # retry_max=3 → 1 initial + 3 retries = 4 attempts
        assert m.call_count == 4

    def test_transport_error_retried(self, backend, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        url = backend.url_for("WhoAmI")
        with requests_mock.Mocker() as m:
            m.get(url, [
                {"exc": requests.exceptions.ConnectionError("boom")},
                {"exc": requests.exceptions.ConnectionError("boom")},
                {"status_code": 200, "json": {"ok": True}},
            ])
            result = backend.get("WhoAmI")
        assert isinstance(result, dict) and result["ok"] is True

    def test_non_retryable_4xx_raises_immediately(self, backend, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
        url = backend.url_for("WhoAmI")
        with requests_mock.Mocker() as m:
            m.get(url, status_code=404,
                  json={"error": {"code": "0x80040217", "message": "Not found"}})
            with pytest.raises(D365Error) as exc_info:
                backend.get("WhoAmI")
        assert exc_info.value.status == 404
        assert m.call_count == 1
        assert slept == []

    def test_post_does_not_retry_on_502(self, backend, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        url = backend.url_for("accounts")
        with requests_mock.Mocker() as m:
            m.post(url, status_code=502, json={"error": {"message": "Bad Gateway"}})
            with pytest.raises(D365Error):
                backend.post("accounts", json_body={"name": "Acme"})
        assert m.call_count == 1

    def test_post_does_retry_on_503(self, backend, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        url = backend.url_for("accounts")
        with requests_mock.Mocker() as m:
            m.post(url, [
                {"status_code": 503, "headers": {"Retry-After": "0"}},
                {"status_code": 200, "headers": {"OData-EntityId": "https://x/y(1)"},
                 "text": ""},
            ])
            result = backend.post("accounts", json_body={"name": "Acme"})
        assert isinstance(result, dict)
        assert m.call_count == 2

    def test_no_retry_env_disables_loop(self, profile, monkeypatch):
        monkeypatch.setenv("CRM_NO_RETRY", "1")
        be = D365Backend(profile, password="pw")
        slept: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
        url = be.url_for("WhoAmI")
        with requests_mock.Mocker() as m:
            m.get(url, status_code=429, headers={"Retry-After": "0"},
                  json={"error": {"message": "rate"}})
            with pytest.raises(D365Error):
                be.get("WhoAmI")
        assert m.call_count == 1
        assert slept == []

    def test_dry_run_skips_retry(self, profile, monkeypatch):
        be = D365Backend(profile, password="pw", dry_run=True)
        slept: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
        result = be.get("WhoAmI")
        assert isinstance(result, dict)
        assert result["_dry_run"] is True
        assert slept == []

    def test_429_emits_rate_limit_log(self, backend, monkeypatch, capsys):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        url = backend.url_for("WhoAmI")
        with requests_mock.Mocker() as m:
            m.get(url, [
                {"status_code": 429, "headers": {
                    "Retry-After": "0",
                    "x-ms-ratelimit-time-remaining-xrm-requests": "30",
                }, "text": ""},
                {"status_code": 200, "json": {"ok": True}},
            ])
            backend.get("WhoAmI")
        err = capsys.readouterr().err
        assert "ratelimit" in err
        assert "time-remaining=30" in err
        assert "retry " in err  # _log_retry line


# ── _resolve_retry_max ──────────────────────────────────────────────────


class TestResolveRetryMax:
    def test_no_env_returns_profile_default(self, profile, monkeypatch):
        for var in ("CRM_NO_RETRY", "CRM_RETRY_MAX"):
            monkeypatch.delenv(var, raising=False)
        assert _resolve_retry_max(profile) == profile.retry_max

    def test_crm_no_retry_forces_zero(self, profile, monkeypatch):
        monkeypatch.setenv("CRM_NO_RETRY", "1")
        monkeypatch.setenv("CRM_RETRY_MAX", "99")
        assert _resolve_retry_max(profile) == 0

    def test_crm_retry_max_overrides_profile(self, profile, monkeypatch):
        monkeypatch.delenv("CRM_NO_RETRY", raising=False)
        monkeypatch.setenv("CRM_RETRY_MAX", "7")
        assert _resolve_retry_max(profile) == 7

    def test_crm_retry_max_zero_allowed(self, profile, monkeypatch):
        monkeypatch.delenv("CRM_NO_RETRY", raising=False)
        monkeypatch.setenv("CRM_RETRY_MAX", "0")
        assert _resolve_retry_max(profile) == 0

    def test_crm_retry_max_non_integer_raises(self, profile, monkeypatch):
        monkeypatch.delenv("CRM_NO_RETRY", raising=False)
        monkeypatch.setenv("CRM_RETRY_MAX", "abc")
        with pytest.raises(D365Error, match="must be an integer"):
            _resolve_retry_max(profile)

    def test_crm_retry_max_negative_raises(self, profile, monkeypatch):
        monkeypatch.delenv("CRM_NO_RETRY", raising=False)
        monkeypatch.setenv("CRM_RETRY_MAX", "-1")
        with pytest.raises(D365Error, match=">= 0"):
            _resolve_retry_max(profile)

    def test_blank_crm_retry_max_falls_back_to_profile(self, profile, monkeypatch):
        monkeypatch.delenv("CRM_NO_RETRY", raising=False)
        monkeypatch.setenv("CRM_RETRY_MAX", "   ")
        assert _resolve_retry_max(profile) == profile.retry_max


# ── poll_async_operation ────────────────────────────────────────────────


class TestPollAsyncOperation:
    OP_ID = "11111111-1111-1111-1111-111111111111"
    JOB_ID = "22222222-2222-2222-2222-222222222222"

    def _op_url(self, backend):
        return backend.url_for(f"asyncoperations({self.OP_ID})")

    def _job_url(self, backend):
        return backend.url_for(f"importjobs({self.JOB_ID})")

    def test_completes_successfully(self, backend, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        with requests_mock.Mocker() as m:
            m.get(self._op_url(backend), [
                {"json": {"statecode": 0, "statuscode": 0, "message": "Ready"}},
                {"json": {"statecode": 3, "statuscode": 30, "message": "Succeeded"}},
            ])
            result = backend.poll_async_operation(self.OP_ID)
        assert result["statecode"] == 3
        assert result["statuscode"] == 30

    def test_raises_on_failure_status(self, backend, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        with requests_mock.Mocker() as m:
            m.get(self._op_url(backend), json={
                "statecode": 3, "statuscode": 31,
                "friendlymessage": "Solution import failed: missing dependency",
            })
            with pytest.raises(D365Error) as exc_info:
                backend.poll_async_operation(self.OP_ID)
        assert "missing dependency" in str(exc_info.value)
        assert exc_info.value.status == 31

    def test_raises_on_cancellation(self, backend, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        with requests_mock.Mocker() as m:
            m.get(self._op_url(backend), json={
                "statecode": 3, "statuscode": 32, "message": "User cancelled",
            })
            with pytest.raises(D365Error) as exc_info:
                backend.poll_async_operation(self.OP_ID)
        assert "32" in str(exc_info.value) or "cancelled" in str(exc_info.value).lower()

    def test_timeout_raises(self, backend, monkeypatch):
        # profile.async_timeout=2; profile.async_poll_initial=0.05 → many polls
        monkeypatch.setattr(time, "sleep", lambda s: None)
        # Fake monotonic clock that advances 5s per call → exceeds 2s timeout fast.
        ticks = iter([0.0, 0.1, 5.0, 10.0])
        monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
        with requests_mock.Mocker() as m:
            m.get(self._op_url(backend), json={
                "statecode": 0, "statuscode": 0, "message": "Pending",
            })
            with pytest.raises(D365Error, match="did not complete within"):
                backend.poll_async_operation(self.OP_ID, timeout=2)

    def test_progress_callback_invoked(self, backend, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        calls: list[tuple[float, str]] = []
        with requests_mock.Mocker() as m:
            m.get(self._op_url(backend), [
                {"json": {"statecode": 0, "statuscode": 0, "message": "Working"}},
                {"json": {"statecode": 3, "statuscode": 30, "message": "Done"}},
            ])
            m.get(self._job_url(backend), [
                {"json": {"progress": 50.0, "solutionname": "MySol"}},
                {"json": {"progress": 100.0, "solutionname": "MySol"}},
            ])
            backend.poll_async_operation(
                self.OP_ID, import_job_id=self.JOB_ID,
                on_progress=lambda pct, msg: calls.append((pct, msg)),
            )
        assert len(calls) == 2
        assert calls[0][0] == 50.0
        assert calls[1][0] == 100.0

    def test_no_progress_callback_skips_job_fetch(self, backend, monkeypatch):
        monkeypatch.setattr(time, "sleep", lambda s: None)
        with requests_mock.Mocker() as m:
            m.get(self._op_url(backend), json={
                "statecode": 3, "statuscode": 30, "message": "Done",
            })
            # No m.get for importjobs — would 404 if called.
            backend.poll_async_operation(self.OP_ID)
        # Pass if we get here without an unmatched-request exception.


# ── ConnectionProfile validation ────────────────────────────────────────


class TestConnectionProfileValidation:
    _BASE: dict[str, Any] = dict(
        name="testp",
        url="https://crm.contoso.local/contoso",
        domain="CONTOSO",
        username="alice",
    )

    @pytest.mark.parametrize("field", [
        "retry_max",
        "retry_base_delay",
        "retry_max_delay",
        "async_poll_initial",
        "async_poll_max",
        "async_timeout",
    ])
    def test_negative_value_raises(self, field):
        kwargs = {**self._BASE, field: -1}
        with pytest.raises(D365Error, match=field):
            ConnectionProfile(**kwargs)

    def test_zero_is_allowed_for_retry_and_timeout(self):
        # retry_max=0 (no retries) is an explicit supported value via env override.
        # retry_base_delay/retry_max_delay/async_timeout tolerate zero too.
        # async_poll_initial/async_poll_max must be > 0 (zero would tight-loop) —
        # covered separately by test_async_poll_zero_raises.
        profile = ConnectionProfile(
            **self._BASE,
            retry_max=0,
            retry_base_delay=0.0,
            retry_max_delay=0.0,
            async_timeout=0,
        )
        assert profile.retry_max == 0

    def test_defaults_pass_validation(self):
        # Sanity: shipped defaults must not trip the validator.
        profile = ConnectionProfile(**self._BASE)
        assert profile.retry_max >= 0
        assert profile.async_timeout >= 0

    def test_from_dict_negative_raises(self):
        bad = {**self._BASE, "async_poll_max": -2.0}
        with pytest.raises(D365Error, match="async_poll_max"):
            ConnectionProfile.from_dict(bad)

    @pytest.mark.parametrize("field", ["async_poll_initial", "async_poll_max"])
    def test_async_poll_zero_raises(self, field):
        # Zero poll intervals tight-loop in poll_async_operation (sleep(0) +
        # immediate re-request). Require > 0.
        kwargs = {**self._BASE, field: 0.0}
        with pytest.raises(D365Error, match=field):
            ConnectionProfile(**kwargs)

    def test_async_poll_max_below_initial_raises(self):
        # backoff doubles interval up to async_poll_max; if max < initial the
        # first sleep already exceeds the cap.
        kwargs = {
            **self._BASE,
            "async_poll_initial": 5.0,
            "async_poll_max": 2.0,
        }
        with pytest.raises(D365Error, match="async_poll_max"):
            ConnectionProfile(**kwargs)
