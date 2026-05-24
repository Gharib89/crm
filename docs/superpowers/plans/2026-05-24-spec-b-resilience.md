# Spec B — Resilience Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a transparent retry + async-poll layer to the `crm` D365 on-prem CLI: `Retry-After`-honoring retry on `D365Backend.request`, a generic async-operation poll helper, and a replacement of synchronous `ImportSolution` / `ExportSolution` with their `*Async` counterparts. Ship as three sequential PRs and bump the package to `0.3.0`.

**Architecture:** Three PRs against `main`. PR1 lands retry + rate-limit-header logging — pure additive, no caller behavior change on a 2xx-only server. PR2 lands `poll_async_operation` as a new `D365Backend` method, also additive — no caller is wired to it yet. PR3 rewrites `import_solution` + `export_solution` to use the async actions, adds CLI flags (`--timeout`, `--no-retry`, `--quiet`), updates the existing solution tests, bumps the version, and writes the CHANGELOG entry. Merge order is strict: PR1 → PR2 → PR3, each rebased on the prior.

**Tech Stack:** Python 3.9+, `requests` + `requests_ntlm` for HTTP, Click 8.x for CLI, `pytest` + `requests_mock` for tests, pyright (strict on `crm/utils/d365_backend.py` and `crm/core/*`) for type checking.

**Spec reference:** `docs/superpowers/specs/2026-05-24-spec-b-resilience-design.md` (commit `68dc42e`).

---

## File Structure

### Files created

| Path | Purpose |
|---|---|
| `crm/tests/test_resilience.py` | Unit tests for the retry loop, `_parse_retry_after`, `_compute_delay`, `_is_response_retryable`, `_is_transport_retryable`, `_log_rate_limit_headers`, and `poll_async_operation`. |

### Files modified

| Path | Why |
|---|---|
| `crm/utils/d365_backend.py` | New `ConnectionProfile` retry/poll fields, `_effective_retry_max` resolution in `D365Backend.__init__`, retry loop inside `request`, six private helpers, `poll_async_operation` method. |
| `crm/core/solution.py` | Rewrite `import_solution` to call `ImportSolutionAsync` + `poll_async_operation`. Rewrite `export_solution` to call `ExportSolutionAsync` + `poll_async_operation` + `DownloadSolutionExportData`. |
| `crm/cli.py` | Add `--timeout`, `--no-retry`, `--quiet` flags to `solution import` and `solution export`. |
| `crm/tests/test_core.py` | Update existing `import_solution` / `export_solution` tests for the new mock interaction sequence and return shape. |
| `crm/tests/TEST.md` | Append an entry for `test_resilience.py` in the test inventory; add a manual smoke-test note for the async flow. |
| `setup.py` | Bump version from `0.2.0` to `0.3.0` in PR3. |
| `CHANGELOG.md` | Append `0.3.0` section in PR3. |

---

# PR1 — `feat/spec-b-retry`

**Branch:** `feat/spec-b-retry` off `main`.
**Goal:** Retry loop active on every `D365Backend.request` call, with rate-limit headers logged on 429 (and on every response under `CRM_VERBOSE=1`). No CLI flag changes; no caller behavior change on a server that never returns 429 / 5xx-transient.

---

### Task 1: Create branch and add the retry/poll fields to `ConnectionProfile`

**Files:**
- Modify: `crm/utils/d365_backend.py:37-70`

- [ ] **Step 1: Create branch**

```bash
git switch -c feat/spec-b-retry
```

- [ ] **Step 2: Add retry + poll fields to `ConnectionProfile` dataclass**

In `crm/utils/d365_backend.py`, replace the existing `@dataclass class ConnectionProfile:` block (lines 37-70) with:

```python
@dataclass
class ConnectionProfile:
    """A reusable D365 connection profile (no secrets)."""

    name: str
    url: str                      # e.g. https://crm.contoso.local/contoso
    domain: str
    username: str
    api_version: str = "v9.2"
    verify_ssl: bool = True
    timeout: int = 120
    retry_max: int = 5
    retry_base_delay: float = 1.0
    retry_max_delay: float = 60.0
    retry_jitter: bool = True
    async_poll_initial: float = 2.0
    async_poll_max: float = 30.0
    async_timeout: int = 1800

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "url": self.url.rstrip("/"),
            "domain": self.domain,
            "username": self.username,
            "api_version": self.api_version,
            "verify_ssl": self.verify_ssl,
            "timeout": self.timeout,
            "retry_max": self.retry_max,
            "retry_base_delay": self.retry_base_delay,
            "retry_max_delay": self.retry_max_delay,
            "retry_jitter": self.retry_jitter,
            "async_poll_initial": self.async_poll_initial,
            "async_poll_max": self.async_poll_max,
            "async_timeout": self.async_timeout,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ConnectionProfile":
        return cls(
            name=d["name"],
            url=d["url"].rstrip("/"),
            domain=d.get("domain", ""),
            username=d["username"],
            api_version=d.get("api_version", "v9.2"),
            verify_ssl=d.get("verify_ssl", True),
            timeout=d.get("timeout", 120),
            retry_max=d.get("retry_max", 5),
            retry_base_delay=d.get("retry_base_delay", 1.0),
            retry_max_delay=d.get("retry_max_delay", 60.0),
            retry_jitter=d.get("retry_jitter", True),
            async_poll_initial=d.get("async_poll_initial", 2.0),
            async_poll_max=d.get("async_poll_max", 30.0),
            async_timeout=d.get("async_timeout", 1800),
        )

    @property
    def api_base(self) -> str:
        """Full Web API base URL, e.g. https://host/org/api/data/v9.2/."""
        return f"{self.url.rstrip('/')}/api/data/{self.api_version}/"
```

- [ ] **Step 3: Run existing tests to confirm round-trip still works**

```bash
pytest crm/tests/test_core.py -v -k "connection or profile"
```

Expected: all PASS. Profile fields not yet exercised by the new tests; old code still round-trips.

- [ ] **Step 4: Commit**

```bash
git add crm/utils/d365_backend.py
git commit -m "feat(backend): add retry + async-poll fields to ConnectionProfile"
```

---

### Task 2: Write failing tests for `_parse_retry_after`

**Files:**
- Create: `crm/tests/test_resilience.py`

- [ ] **Step 1: Create the test file with the first set of failing tests**

```python
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
```

- [ ] **Step 2: Run the new tests to verify they fail with ImportError**

```bash
pytest crm/tests/test_resilience.py -v
```

Expected: FAIL with `ImportError: cannot import name '_parse_retry_after' from 'crm.utils.d365_backend'`. (None of the helpers exist yet.)

- [ ] **Step 3: Commit the failing test file**

```bash
git add crm/tests/test_resilience.py
git commit -m "test(resilience): add failing tests for _parse_retry_after"
```

---

### Task 3: Implement `_parse_retry_after`

**Files:**
- Modify: `crm/utils/d365_backend.py` (add to module, near the bottom alongside `_parse_response`)

- [ ] **Step 1: Add the helper**

Append to `crm/utils/d365_backend.py` after `_parse_response` (before `as_dict`):

```python
def _parse_retry_after(header: str | None) -> float | None:
    """Parse an HTTP Retry-After header value.

    Accepts integer/float seconds or an HTTP-date. Returns float seconds or
    None if the header is missing or unparseable. Negative values clamp to 0.
    """
    if not header:
        return None
    raw = header.strip()
    if not raw:
        return None
    # Try numeric seconds first.
    try:
        secs = float(raw)
        return max(0.0, secs)
    except ValueError:
        pass
    # Fall back to HTTP-date.
    try:
        from email.utils import parsedate_to_datetime
        import datetime as _dt
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        now = _dt.datetime.now(_dt.timezone.utc)
        delta = (dt - now).total_seconds()
        return max(0.0, delta)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 2: Run the tests to verify they pass**

```bash
pytest crm/tests/test_resilience.py::TestParseRetryAfter -v
```

Expected: 7/7 PASS.

- [ ] **Step 3: Commit**

```bash
git add crm/utils/d365_backend.py
git commit -m "feat(backend): add _parse_retry_after helper"
```

---

### Task 4: Write failing tests for `_compute_delay`

**Files:**
- Modify: `crm/tests/test_resilience.py`

- [ ] **Step 1: Append the test class**

Append to `crm/tests/test_resilience.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest crm/tests/test_resilience.py::TestComputeDelay -v
```

Expected: FAIL with `ImportError: cannot import name '_compute_delay'`.

- [ ] **Step 3: Commit**

```bash
git add crm/tests/test_resilience.py
git commit -m "test(resilience): add failing tests for _compute_delay"
```

---

### Task 5: Implement `_compute_delay` + `_is_response_retryable` + `_is_transport_retryable`

**Files:**
- Modify: `crm/utils/d365_backend.py`

- [ ] **Step 1: Add the three helpers**

Append to `crm/utils/d365_backend.py` after `_parse_retry_after`:

```python
def _compute_delay(
    attempt: int,
    profile: ConnectionProfile,
    *,
    retry_after: float | None,
) -> float:
    """Compute the sleep duration before the next retry attempt."""
    import random
    if retry_after is not None:
        return min(retry_after, profile.retry_max_delay)
    base = min(profile.retry_base_delay * (2 ** attempt), profile.retry_max_delay)
    if profile.retry_jitter:
        return random.uniform(0.0, base)
    return base


def _is_response_retryable(resp: requests.Response, method: str) -> bool:
    """Return True if the response status warrants a retry for this method."""
    status = resp.status_code
    if status == 429:
        return True
    method_upper = method.upper()
    if status == 503 and method_upper == "POST":
        return True
    if status in (502, 503, 504) and method_upper in ("GET", "PUT", "PATCH", "DELETE"):
        return True
    return False


_RETRYABLE_TRANSPORT_TYPES = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def _is_transport_retryable(exc: BaseException) -> bool:
    """Return True if the transport exception is worth retrying."""
    # SSL errors are a subclass of ConnectionError; reject them explicitly.
    if isinstance(exc, requests.exceptions.SSLError):
        return False
    return isinstance(exc, _RETRYABLE_TRANSPORT_TYPES)
```

- [ ] **Step 2: Run the `_compute_delay` tests**

```bash
pytest crm/tests/test_resilience.py::TestComputeDelay -v
```

Expected: 5/5 PASS.

- [ ] **Step 3: Append `_is_response_retryable` + `_is_transport_retryable` tests**

Append to `crm/tests/test_resilience.py`:

```python
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
```

- [ ] **Step 4: Run the truth-table tests**

```bash
pytest crm/tests/test_resilience.py::TestIsResponseRetryable crm/tests/test_resilience.py::TestIsTransportRetryable -v
```

Expected: 23/23 PASS (17 parametrized + 6).

- [ ] **Step 5: Commit**

```bash
git add crm/utils/d365_backend.py crm/tests/test_resilience.py
git commit -m "feat(backend): add _compute_delay, _is_response_retryable, _is_transport_retryable"
```

---

### Task 6: Write failing tests for `_log_rate_limit_headers`

**Files:**
- Modify: `crm/tests/test_resilience.py`

- [ ] **Step 1: Append the test class**

```python
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
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest crm/tests/test_resilience.py::TestLogRateLimitHeaders -v
```

Expected: FAIL with `ImportError: cannot import name '_log_rate_limit_headers'`.

- [ ] **Step 3: Commit**

```bash
git add crm/tests/test_resilience.py
git commit -m "test(resilience): add failing tests for _log_rate_limit_headers"
```

---

### Task 7: Implement `_log_rate_limit_headers` + `_log_retry`

**Files:**
- Modify: `crm/utils/d365_backend.py`

- [ ] **Step 1: Add the helpers**

Append to `crm/utils/d365_backend.py` after `_is_transport_retryable`:

```python
import os as _os
import sys as _sys


_RATE_LIMIT_HEADER_MAP = (
    ("x-ms-ratelimit-time-remaining-xrm-requests", "time-remaining"),
    ("x-ms-ratelimit-burst-remaining-xrm-requests", "burst-remaining"),
    ("x-ms-ratelimit-limit-xrm-requests", "limit"),
    ("Retry-After", "retry-after"),
)


def _log_rate_limit_headers(resp: requests.Response, *, on_429: bool) -> None:
    """Emit one stderr line with x-ms-ratelimit-* + Retry-After values present.

    on_429=True: always emit if any header is present.
    on_429=False: only emit if CRM_VERBOSE=1 in env.
    """
    if not on_429 and _os.environ.get("CRM_VERBOSE") != "1":
        return
    parts: list[str] = []
    for header_name, short_name in _RATE_LIMIT_HEADER_MAP:
        val = resp.headers.get(header_name)
        if val is not None and val != "":
            parts.append(f"{short_name}={val}")
    if not parts:
        return
    _sys.stderr.write(f"[crm] ratelimit {' '.join(parts)}\n")


def _log_retry(method: str, url: str, attempt: int, delay: float, *, effective_max: int, reason: str) -> None:
    """One-line stderr trace of a retry decision."""
    _sys.stderr.write(
        f"[crm] retry {method} {url} attempt={attempt + 1}/{effective_max} "
        f"delay={delay:.1f}s reason={reason}\n"
    )
```

- [ ] **Step 2: Run the tests**

```bash
pytest crm/tests/test_resilience.py::TestLogRateLimitHeaders -v
```

Expected: 5/5 PASS.

- [ ] **Step 3: Commit**

```bash
git add crm/utils/d365_backend.py
git commit -m "feat(backend): add _log_rate_limit_headers and _log_retry"
```

---

### Task 8: Write failing integration tests for the retry loop

**Files:**
- Modify: `crm/tests/test_resilience.py`

- [ ] **Step 1: Append the retry-loop test class**

```python
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
```

- [ ] **Step 2: Run to verify failure**

```bash
pytest crm/tests/test_resilience.py::TestRetryLoop -v
```

Expected: FAIL — the request method does not retry yet (or some tests pass accidentally on the happy path; the 429-retry tests will definitely fail because the current code raises `D365Error` on the first 429).

- [ ] **Step 3: Commit**

```bash
git add crm/tests/test_resilience.py
git commit -m "test(resilience): add failing integration tests for retry loop"
```

---

### Task 9: Implement the retry loop in `D365Backend.request`

**Files:**
- Modify: `crm/utils/d365_backend.py:94-166`

- [ ] **Step 1: Add `_effective_retry_max` resolution to `__init__`**

Insert the env-resolution block at the end of `D365Backend.__init__` (after `self._session.verify = profile.verify_ssl`):

```python
        self._effective_retry_max = _resolve_retry_max(profile)
```

- [ ] **Step 2: Add `_resolve_retry_max` helper near other helpers**

Append after `_log_retry`:

```python
def _resolve_retry_max(profile: ConnectionProfile) -> int:
    """Resolve the effective retry max from profile + env overrides.

    CRM_NO_RETRY=1 forces 0. Otherwise CRM_RETRY_MAX overrides profile.retry_max.
    """
    if _env_truthy("CRM_NO_RETRY"):
        return 0
    override = _os.environ.get("CRM_RETRY_MAX")
    if override is not None and override.strip() != "":
        try:
            value = int(override)
        except ValueError as exc:
            raise D365Error(
                f"CRM_RETRY_MAX must be an integer; got {override!r}"
            ) from exc
        if value < 0:
            raise D365Error(f"CRM_RETRY_MAX must be >= 0; got {value}")
        return value
    return profile.retry_max


def _env_truthy(name: str) -> bool:
    val = _os.environ.get(name)
    return val is not None and val.strip().lower() in ("1", "true", "yes", "on")
```

- [ ] **Step 3: Add `import time` at the top of the module**

Add `import time` next to the existing `import json` line.

- [ ] **Step 4: Rewrite the `request` method body**

Replace the existing `request` method body (the try/except around `self._session.request` and the call to `_parse_response`) with the retry loop. The full new method:

```python
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        extra_headers: dict[str, str] | None = None,
        expect_json: bool = True,
    ) -> dict[str, Any] | str | None:
        """Issue an HTTP request and return parsed JSON (or None for 204).

        Retries on 429, idempotent 5xx, and retryable transport errors per the
        backend's profile + env config. Honors self.dry_run by returning a
        preview dict instead of issuing the call.

        Raises D365Error on transport failure or non-2xx response after retries
        are exhausted.
        """
        url = self.url_for(path)
        headers = dict(_DEFAULT_HEADERS)
        if extra_headers:
            headers.update(extra_headers)

        if self.dry_run:
            return {
                "_dry_run": True,
                "method": method,
                "url": url,
                "params": params or {},
                "headers": {k: v for k, v in headers.items() if k.lower() != "authorization"},
                "body": json_body,
            }

        max_retries = self._effective_retry_max
        attempt = 0
        while True:
            try:
                resp = self._session.request(  # pyright: ignore[reportUnknownMemberType]
                    method,
                    url,
                    params=params,
                    data=json.dumps(json_body) if json_body is not None else None,
                    headers=headers,
                    timeout=self.profile.timeout,
                )
            except requests.RequestException as exc:
                if attempt >= max_retries or not _is_transport_retryable(exc):
                    raise D365Error(f"HTTP transport failure: {exc}") from exc
                delay = _compute_delay(attempt, self.profile, retry_after=None)
                _log_retry(method, url, attempt, delay,
                           effective_max=max_retries, reason=str(exc))
                time.sleep(delay)
                attempt += 1
                continue

            # Verbose-mode header log on every response.
            _log_rate_limit_headers(resp, on_429=False)

            if not _is_response_retryable(resp, method):
                return _parse_response(resp, expect_json=expect_json)

            if attempt >= max_retries:
                _log_rate_limit_headers(resp, on_429=True)
                return _parse_response(resp, expect_json=expect_json)  # raises D365Error

            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            delay = _compute_delay(attempt, self.profile, retry_after=retry_after)
            _log_rate_limit_headers(resp, on_429=True)
            _log_retry(method, url, attempt, delay,
                       effective_max=max_retries, reason=f"HTTP {resp.status_code}")
            time.sleep(delay)
            attempt += 1
```

- [ ] **Step 5: Run the retry-loop tests**

```bash
pytest crm/tests/test_resilience.py::TestRetryLoop -v
```

Expected: 9/9 PASS.

- [ ] **Step 6: Run the full test suite to verify no regressions**

```bash
pytest crm/tests/ -v
```

Expected: existing tests all PASS. Total = existing tests + 9 (TestRetryLoop) + 5 (TestLogRateLimitHeaders) + 6 (TestIsTransportRetryable) + 17 (TestIsResponseRetryable, parametrized) + 5 (TestComputeDelay) + 7 (TestParseRetryAfter).

- [ ] **Step 7: Run pyright to confirm strict zone clean**

```bash
pyright crm/utils/d365_backend.py
```

Expected: 0 errors.

- [ ] **Step 8: Commit**

```bash
git add crm/utils/d365_backend.py
git commit -m "feat(backend): retry on 429 + idempotent 5xx + transport errors

Honors Retry-After when present, falls back to capped exponential backoff
with full jitter. POST retries only on 429/503. Configurable via
ConnectionProfile fields + CRM_RETRY_* env overrides. CRM_NO_RETRY=1
disables. Logs x-ms-ratelimit-* headers on 429 (and on every response
under CRM_VERBOSE=1)."
```

---

### Task 10: Open PR1

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feat/spec-b-retry
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "Spec B PR1: retry loop + rate-limit logging" --body "$(cat <<'EOF'
## Summary

- Retry loop on `D365Backend.request` honoring `Retry-After` + capped exponential backoff with full jitter
- Retries 429, idempotent 5xx (502/503/504 on GET/PUT/PATCH/DELETE; 503-only on POST), and retryable transport errors
- Six new private helpers in `crm/utils/d365_backend.py`: `_parse_retry_after`, `_compute_delay`, `_is_response_retryable`, `_is_transport_retryable`, `_log_rate_limit_headers`, `_log_retry`
- `ConnectionProfile` gains `retry_max`, `retry_base_delay`, `retry_max_delay`, `retry_jitter`, `async_poll_initial`, `async_poll_max`, `async_timeout`
- Env overrides: `CRM_RETRY_MAX`, `CRM_RETRY_BASE_DELAY`, `CRM_RETRY_MAX_DELAY`, `CRM_RETRY_JITTER`, `CRM_ASYNC_TIMEOUT`, `CRM_NO_RETRY`
- Pure additive — no caller behavior change on a 2xx-only server

Refs Spec B (`docs/superpowers/specs/2026-05-24-spec-b-resilience-design.md`) §3 + §7. PR2 follows with the async-poll helper; PR3 wires it into solution import/export.

## Test plan

- [ ] `pytest crm/tests/test_resilience.py -v` — 49 new unit tests pass
- [ ] `pytest crm/tests/ -v` — full suite green, no regressions
- [ ] `pyright crm/utils/d365_backend.py` — 0 errors
- [ ] Manual smoke against MOCE 9.1.44.15: `crm whoami` succeeds; `CRM_VERBOSE=1 crm whoami` prints one `[crm] ratelimit ...` stderr line

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

# PR2 — `feat/spec-b-async-poll`

**Branch:** `feat/spec-b-async-poll` off PR1's branch (`feat/spec-b-retry`) once PR1 lands.
**Goal:** `D365Backend.poll_async_operation` available as a public method. No callers wired up.

---

### Task 11: Branch off PR1 and write failing tests for `poll_async_operation`

**Files:**
- Modify: `crm/tests/test_resilience.py`

- [ ] **Step 1: Branch**

After PR1 merges to `main`:

```bash
git switch main
git pull
git switch -c feat/spec-b-async-poll
```

- [ ] **Step 2: Append the poll test class to `crm/tests/test_resilience.py`**

```python
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
```

- [ ] **Step 3: Run to verify failure**

```bash
pytest crm/tests/test_resilience.py::TestPollAsyncOperation -v
```

Expected: FAIL with `AttributeError: 'D365Backend' object has no attribute 'poll_async_operation'`.

- [ ] **Step 4: Commit**

```bash
git add crm/tests/test_resilience.py
git commit -m "test(resilience): add failing tests for poll_async_operation"
```

---

### Task 12: Implement `D365Backend.poll_async_operation`

**Files:**
- Modify: `crm/utils/d365_backend.py`

- [ ] **Step 1: Add the method to `D365Backend`**

Append the method to the `D365Backend` class, after the `delete` convenience verb:

```python
    def poll_async_operation(
        self,
        async_operation_id: str,
        *,
        timeout: int | None = None,
        import_job_id: str | None = None,
        on_progress: Callable[[float, str], None] | None = None,
    ) -> dict[str, Any]:
        """Block until the async operation completes, then return its row.

        Polls asyncoperations(<async_operation_id>) at an increasing interval
        (profile.async_poll_initial → profile.async_poll_max, doubling each tick).
        Each poll itself benefits from the retry loop on transient errors.

        If import_job_id is given and on_progress is set, also reads
        importjobs(<id>).progress on every tick and forwards
        (percent, status_message) to the callback.

        Raises:
            D365Error on operation failure (statuscode != 30) or timeout.
        """
        effective_timeout = timeout if timeout is not None else self.profile.async_timeout
        deadline = time.monotonic() + effective_timeout
        interval = self.profile.async_poll_initial
        while True:
            op = cast(dict[str, Any], self.get(f"asyncoperations({async_operation_id})"))
            state = op.get("statecode")
            status = op.get("statuscode")

            if import_job_id is not None and on_progress is not None:
                job_row = cast(dict[str, Any], self.get(
                    f"importjobs({import_job_id})",
                    params={"$select": "progress,solutionname,startedon,completedon"},
                ))
                pct = float(job_row.get("progress") or 0.0)
                msg = op.get("message") or ""
                on_progress(pct, msg)

            if state == 3:
                if status == 30:
                    return op
                raise D365Error(
                    f"Async operation {async_operation_id} ended with statuscode={status}: "
                    f"{op.get('friendlymessage') or op.get('message') or '(no message)'}",
                    status=status if isinstance(status, int) else None,
                    response_body=op,
                )

            if time.monotonic() >= deadline:
                raise D365Error(
                    f"Async operation {async_operation_id} did not complete within "
                    f"{effective_timeout}s (last statecode={state})",
                    response_body=op,
                )

            sleep_for = min(interval, max(0.0, deadline - time.monotonic()))
            time.sleep(sleep_for)
            interval = min(interval * 2, self.profile.async_poll_max)
```

- [ ] **Step 2: Add the `Callable` + `cast` imports**

At the top of `crm/utils/d365_backend.py`, update the typing import line:

```python
from typing import Any, Callable, cast
```

- [ ] **Step 3: Run the poll tests**

```bash
pytest crm/tests/test_resilience.py::TestPollAsyncOperation -v
```

Expected: 6/6 PASS.

- [ ] **Step 4: Run the full suite**

```bash
pytest crm/tests/ -v
```

Expected: all green.

- [ ] **Step 5: Pyright clean**

```bash
pyright crm/utils/d365_backend.py
```

Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add crm/utils/d365_backend.py
git commit -m "feat(backend): add poll_async_operation helper

Blocking poll of asyncoperations(<id>) with capped exponential backoff
(profile.async_poll_initial → async_poll_max). Returns the final row on
success (statuscode=30); raises D365Error on failure (31), cancellation
(32), or timeout. Optional importjobs progress callback for the solution
import flow."
```

---

### Task 13: Open PR2

- [ ] **Step 1: Push**

```bash
git push -u origin feat/spec-b-async-poll
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "Spec B PR2: poll_async_operation helper" --body "$(cat <<'EOF'
## Summary

- New `D365Backend.poll_async_operation(async_operation_id, *, timeout, import_job_id, on_progress)` method
- Polls `asyncoperations(<id>)` at an increasing interval (capped at `profile.async_poll_max`)
- Returns the final row on success (`statecode=3`, `statuscode=30`)
- Raises `D365Error` on failure (`statuscode=31`), cancellation (`32`), or timeout
- Optional `importjobs` progress callback for the upcoming solution-import wiring
- No callers wired up in this PR — pure additive

Refs Spec B §4. PR3 wires this into `import_solution` + `export_solution`.

## Test plan

- [ ] `pytest crm/tests/test_resilience.py::TestPollAsyncOperation -v` — 6/6 pass
- [ ] `pytest crm/tests/ -v` — full suite green
- [ ] `pyright crm/utils/d365_backend.py` — 0 errors

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

# PR3 — `feat/spec-b-solution-async`

**Branch:** `feat/spec-b-solution-async` off PR2's branch once PR2 lands.
**Goal:** `import_solution` + `export_solution` use the async actions. New CLI flags. Version 0.3.0. CHANGELOG entry.

---

### Task 14: Branch + update `test_core.py` for the new `export_solution` flow (failing)

**Files:**
- Modify: `crm/tests/test_core.py`

- [ ] **Step 1: Branch**

After PR2 merges:

```bash
git switch main && git pull
git switch -c feat/spec-b-solution-async
```

- [ ] **Step 2: Find existing export_solution + import_solution tests**

```bash
grep -n "export_solution\|import_solution" crm/tests/test_core.py
```

If tests exist with the old mocks (single `POST ExportSolution` + single `POST ImportSolution`), they will need rewriting. If no such tests exist yet, add them now in the new (async) shape.

- [ ] **Step 3: Add or replace the solution tests**

In `crm/tests/test_core.py`, locate the `# ── solution.py ──` section header (or add it if missing, between the existing sections). Replace any existing `test_export_solution_*` / `test_import_solution_*` tests with:

```python
# ── solution.py — async flow ────────────────────────────────────────────


class TestExportSolutionAsync:
    OP_ID = "33333333-3333-3333-3333-333333333333"
    EXPORT_JOB_ID = "44444444-4444-4444-4444-444444444444"

    def test_export_calls_async_then_poll_then_download(
        self, backend, tmp_path, monkeypatch
    ):
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        out = tmp_path / "mysol.zip"
        # 5-byte zip stub, base64-encoded
        encoded = "UEsBAh4D"

        with requests_mock.Mocker() as m:
            m.post(backend.url_for("ExportSolutionAsync"), json={
                "AsyncOperationId": self.OP_ID,
                "ExportJobId": self.EXPORT_JOB_ID,
            })
            m.get(backend.url_for(f"asyncoperations({self.OP_ID})"), json={
                "statecode": 3, "statuscode": 30, "message": "Done",
            })
            m.post(backend.url_for("DownloadSolutionExportData"), json={
                "ExportSolutionFile": encoded,
            })
            from crm.core import solution as sol_mod
            info = sol_mod.export_solution(
                backend, "MySolution", out, managed=True,
            )

        assert info["solution"] == "MySolution"
        assert info["managed"] is True
        assert info["output"] == str(out)
        assert info["async_operation_id"] == self.OP_ID
        assert info["export_job_id"] == self.EXPORT_JOB_ID
        assert info["bytes"] == 3  # base64 "UEsBAh4D" decodes to 6 bytes
        assert "duration_ms" in info
        assert out.exists()

    def test_export_raises_on_async_failure(
        self, backend, tmp_path, monkeypatch
    ):
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        out = tmp_path / "mysol.zip"

        with requests_mock.Mocker() as m:
            m.post(backend.url_for("ExportSolutionAsync"), json={
                "AsyncOperationId": self.OP_ID,
                "ExportJobId": self.EXPORT_JOB_ID,
            })
            m.get(backend.url_for(f"asyncoperations({self.OP_ID})"), json={
                "statecode": 3, "statuscode": 31,
                "friendlymessage": "Solution export failed",
            })
            from crm.core import solution as sol_mod
            with pytest.raises(D365Error, match="export failed"):
                sol_mod.export_solution(backend, "MySolution", out)

        assert not out.exists()

    def test_export_dry_run_short_circuits(self, profile, tmp_path):
        dry = D365Backend(profile, password="pw", dry_run=True)
        from crm.core import solution as sol_mod
        info = sol_mod.export_solution(dry, "MySolution", tmp_path / "x.zip")
        assert info["_dry_run"] is True
        assert info["action"] == "ExportSolutionAsync"


class TestImportSolutionAsync:
    OP_ID = "55555555-5555-5555-5555-555555555555"

    def test_import_calls_async_then_polls(
        self, backend, tmp_path, monkeypatch
    ):
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        zip_path = tmp_path / "in.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")

        with requests_mock.Mocker() as m:
            m.post(backend.url_for("ImportSolutionAsync"), json={
                "AsyncOperationId": self.OP_ID,
                "ImportJobKey": "00000000-0000-0000-0000-000000000abc",
            })
            m.get(
                requests_mock.ANY,
                json={"statecode": 3, "statuscode": 30, "message": "Done"},
            )
            from crm.core import solution as sol_mod
            info = sol_mod.import_solution(backend, zip_path, quiet=True)

        assert info["async_operation_id"] == self.OP_ID
        assert info["status"] == "succeeded"
        assert "import_job_id" in info
        assert "duration_ms" in info
        assert "started_on" in info or info.get("started_on") is None  # tolerant

    def test_import_missing_file_raises(self, backend, tmp_path):
        from crm.core import solution as sol_mod
        with pytest.raises(D365Error, match="not found"):
            sol_mod.import_solution(backend, tmp_path / "missing.zip")

    def test_import_raises_on_async_failure(
        self, backend, tmp_path, monkeypatch
    ):
        import time as _t
        monkeypatch.setattr(_t, "sleep", lambda s: None)
        zip_path = tmp_path / "in.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")

        with requests_mock.Mocker() as m:
            m.post(backend.url_for("ImportSolutionAsync"), json={
                "AsyncOperationId": self.OP_ID,
                "ImportJobKey": "00000000-0000-0000-0000-000000000abc",
            })
            m.get(
                requests_mock.ANY,
                json={"statecode": 3, "statuscode": 31,
                      "friendlymessage": "Import failed: missing dependency"},
            )
            from crm.core import solution as sol_mod
            with pytest.raises(D365Error, match="missing dependency"):
                sol_mod.import_solution(backend, zip_path, quiet=True)

    def test_import_dry_run_short_circuits(self, profile, tmp_path):
        zip_path = tmp_path / "in.zip"
        zip_path.write_bytes(b"PK\x03\x04stub")
        dry = D365Backend(profile, password="pw", dry_run=True)
        from crm.core import solution as sol_mod
        info = sol_mod.import_solution(dry, zip_path)
        assert info["_dry_run"] is True
        assert info["action"] == "ImportSolutionAsync"
        assert "import_job_id" in info
```

- [ ] **Step 4: Run the new tests to verify failure**

```bash
pytest crm/tests/test_core.py::TestExportSolutionAsync crm/tests/test_core.py::TestImportSolutionAsync -v
```

Expected: FAIL — `import_solution` / `export_solution` still call the synchronous actions.

- [ ] **Step 5: Commit failing tests**

```bash
git add crm/tests/test_core.py
git commit -m "test(solution): add failing tests for async import/export"
```

---

### Task 15: Rewrite `export_solution`

**Files:**
- Modify: `crm/core/solution.py:46-93`

- [ ] **Step 1: Replace the `export_solution` body**

In `crm/core/solution.py`, replace the existing `def export_solution(...)` body with:

```python
def export_solution(
    backend: D365Backend,
    unique_name: str,
    output_path: str | Path,
    *,
    managed: bool = False,
    export_autonumbering: bool = False,
    export_calendar: bool = False,
    export_customizations: bool = False,
    export_email_tracking: bool = False,
    export_general: bool = False,
    export_isv_config: bool = False,
    export_marketing: bool = False,
    export_outlook_sync: bool = False,
    export_relationship_roles: bool = False,
    export_sales: bool = False,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Call ExportSolutionAsync, poll to completion, then DownloadSolutionExportData.

    Blocks until the async operation finishes (or timeout). Returns a dict with
    the on-disk path, byte count, async operation id, export job id, and total
    duration in ms.
    """
    import time as _time
    body: dict[str, Any] = {
        "SolutionName": unique_name,
        "Managed": managed,
        "ExportAutoNumberingSettings": export_autonumbering,
        "ExportCalendarSettings": export_calendar,
        "ExportCustomizationSettings": export_customizations,
        "ExportEmailTrackingSettings": export_email_tracking,
        "ExportGeneralSettings": export_general,
        "ExportIsvConfig": export_isv_config,
        "ExportMarketingSettings": export_marketing,
        "ExportOutlookSynchronizationSettings": export_outlook_sync,
        "ExportRelationshipRoles": export_relationship_roles,
        "ExportSales": export_sales,
    }

    started = _time.monotonic()
    resp = as_dict(backend.post("ExportSolutionAsync", json_body=body))
    if "_dry_run" in resp:
        return {**resp, "action": "ExportSolutionAsync"}

    async_op_id = resp.get("AsyncOperationId")
    export_job_id = resp.get("ExportJobId")
    if not async_op_id or not export_job_id:
        raise D365Error(
            "ExportSolutionAsync returned no AsyncOperationId / ExportJobId."
        )

    backend.poll_async_operation(async_op_id, timeout=timeout)

    dl = as_dict(backend.post(
        "DownloadSolutionExportData",
        json_body={"ExportJobId": export_job_id},
    ))
    encoded = dl.get("ExportSolutionFile")
    if not encoded:
        raise D365Error(
            "DownloadSolutionExportData returned no ExportSolutionFile payload."
        )
    data = base64.b64decode(encoded)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)

    duration_ms = int((_time.monotonic() - started) * 1000)
    return {
        "output": str(out),
        "bytes": len(data),
        "managed": managed,
        "solution": unique_name,
        "async_operation_id": async_op_id,
        "export_job_id": export_job_id,
        "duration_ms": duration_ms,
    }
```

- [ ] **Step 2: Run the export tests**

```bash
pytest crm/tests/test_core.py::TestExportSolutionAsync -v
```

Expected: 3/3 PASS.

- [ ] **Step 3: Commit**

```bash
git add crm/core/solution.py
git commit -m "feat(solution): rewrite export_solution on ExportSolutionAsync

Calls ExportSolutionAsync → poll_async_operation → DownloadSolutionExportData.
Return shape gains async_operation_id, export_job_id, duration_ms. Adds
timeout kwarg that overrides profile.async_timeout."
```

---

### Task 16: Rewrite `import_solution`

**Files:**
- Modify: `crm/core/solution.py:96-115`

- [ ] **Step 1: Replace the `import_solution` body**

In `crm/core/solution.py`, replace the existing `def import_solution(...)` body with:

```python
def import_solution(
    backend: D365Backend,
    zip_path: str | Path,
    *,
    publish_workflows: bool = True,
    overwrite_unmanaged_customizations: bool = True,
    timeout: int | None = None,
    quiet: bool = False,
) -> dict[str, Any]:
    """Call ImportSolutionAsync and block on the resulting ImportJob.

    Returns a dict with import_job_id, async_operation_id, status='succeeded',
    progress percent, started_on / completed_on (from the importjobs row), and
    total wall-clock duration in ms.

    Raises D365Error on file-not-found, async failure, or timeout.
    """
    import sys as _sys
    import time as _time

    p = Path(zip_path)
    if not p.is_file():
        raise D365Error(f"Solution file not found: {zip_path}")
    encoded = base64.b64encode(p.read_bytes()).decode("ascii")
    import_job_id = _new_guid()
    body: dict[str, Any] = {
        "CustomizationFile": encoded,
        "PublishWorkflows": publish_workflows,
        "OverwriteUnmanagedCustomizations": overwrite_unmanaged_customizations,
        "ImportJobId": import_job_id,
    }

    started = _time.monotonic()
    resp = as_dict(backend.post("ImportSolutionAsync", json_body=body))
    if "_dry_run" in resp:
        return {**resp, "action": "ImportSolutionAsync", "import_job_id": import_job_id}

    async_op_id = resp.get("AsyncOperationId")
    if not async_op_id:
        raise D365Error("ImportSolutionAsync returned no AsyncOperationId.")

    last_progress: dict[str, float] = {"pct": -1.0}
    last_emit: dict[str, float] = {"t": 0.0}

    def _on_progress(pct: float, msg: str) -> None:
        if quiet:
            return
        now = _time.monotonic()
        if pct == last_progress["pct"] and (now - last_emit["t"]) < 1.0:
            return
        last_progress["pct"] = pct
        last_emit["t"] = now
        _sys.stderr.write(f"[crm] import progress={pct:.1f}% status={msg}\n")

    try:
        backend.poll_async_operation(
            async_op_id,
            timeout=timeout,
            import_job_id=import_job_id,
            on_progress=_on_progress,
        )
    except D365Error as exc:
        raise D365Error(
            f"{exc} (import_job_id={import_job_id})",
            status=exc.status, code=exc.code, response_body=exc.response_body,
        ) from exc

    # Final importjobs read for the canonical progress + timestamps.
    job_row = as_dict(backend.get(
        f"importjobs({import_job_id})",
        params={"$select": "progress,startedon,completedon,solutionname"},
    ))
    duration_ms = int((_time.monotonic() - started) * 1000)
    return {
        "import_job_id": import_job_id,
        "async_operation_id": async_op_id,
        "status": "succeeded",
        "progress": float(job_row.get("progress") or 100.0),
        "started_on": job_row.get("startedon"),
        "completed_on": job_row.get("completedon"),
        "duration_ms": duration_ms,
    }
```

- [ ] **Step 2: Run the import tests**

```bash
pytest crm/tests/test_core.py::TestImportSolutionAsync -v
```

Expected: 4/4 PASS. The mock uses `requests_mock.ANY` for all GETs so the importjobs final read returns the same `{statecode: 3, statuscode: 30}` row — that's OK because `as_dict` tolerates extra keys.

- [ ] **Step 3: Run the full suite**

```bash
pytest crm/tests/ -v
```

Expected: green.

- [ ] **Step 4: Pyright**

```bash
pyright crm/core/solution.py crm/utils/d365_backend.py
```

Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add crm/core/solution.py
git commit -m "feat(solution): rewrite import_solution on ImportSolutionAsync

Calls ImportSolutionAsync, polls asyncoperations + importjobs, returns
final row with import_job_id, async_operation_id, status, progress,
started_on, completed_on, duration_ms. Progress callback writes
one stderr line per change (deduped + 1s throttle); --quiet suppresses.

Breaking: return shape no longer mirrors the sync ImportSolution
response. Callers must read the new field names."
```

---

### Task 17: Add CLI flags `--timeout`, `--no-retry`, `--quiet`

**Files:**
- Modify: `crm/cli.py:864-885` (solution export command) and `crm/cli.py:941-956` (solution import command)

- [ ] **Step 1: Update `solution_export_cmd`**

Replace the `solution_export_cmd` block (lines ~864-885):

```python
@solution.command("export")
@click.argument("unique_name")
@click.option("--output", "-o", required=True, type=click.Path(dir_okay=False))
@click.option("--managed", is_flag=True)
@click.option(
    "--export-setting",
    "export_settings",
    multiple=True,
    type=click.Choice(sorted(_EXPORT_SETTING_KEYS.keys())),
    help="Repeatable; include a named export setting in the solution payload.",
)
@click.option("--timeout", type=int, default=None,
              help="Async operation timeout in seconds. Overrides profile.async_timeout.")
@click.option("--no-retry", is_flag=True,
              help="Disable the 429/5xx retry loop for this invocation.")
@pass_ctx
def solution_export_cmd(ctx, unique_name, output, managed, export_settings, timeout, no_retry):
    if no_retry:
        os.environ["CRM_NO_RETRY"] = "1"
    kwargs = {_EXPORT_SETTING_KEYS[name]: True for name in export_settings}
    try:
        info = sol_mod.export_solution(
            ctx.backend(), unique_name, output, managed=managed,
            timeout=timeout, **kwargs,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
```

- [ ] **Step 2: Update `solution_import_cmd`**

Replace the `solution_import_cmd` block (lines ~941-956):

```python
@solution.command("import")
@click.argument("zip_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--no-publish", is_flag=True)
@click.option("--no-overwrite", is_flag=True)
@click.option("--timeout", type=int, default=None,
              help="Async operation timeout in seconds. Overrides profile.async_timeout.")
@click.option("--no-retry", is_flag=True,
              help="Disable the 429/5xx retry loop for this invocation.")
@click.option("--quiet", "-q", is_flag=True,
              help="Suppress per-tick import-progress lines on stderr.")
@pass_ctx
def solution_import_cmd(ctx, zip_path, no_publish, no_overwrite, timeout, no_retry, quiet):
    if no_retry:
        os.environ["CRM_NO_RETRY"] = "1"
    try:
        info = sol_mod.import_solution(
            ctx.backend(), zip_path,
            publish_workflows=not no_publish,
            overwrite_unmanaged_customizations=not no_overwrite,
            timeout=timeout,
            quiet=quiet,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
```

`--no-retry` sets the env var so the next `ctx.backend()` (or current one if already cached) reads it on construction. The REPL caches the backend across commands per Spec A §3.7, so `--no-retry` only affects newly constructed backends — `crm.cli:CLIContext.backend()` already invalidates on connection change. Document this in the CLI help if surprising.

- [ ] **Step 3: Smoke-test the CLI parses both commands**

```bash
crm solution import --help
crm solution export --help
```

Expected: both print the help text including `--timeout`, `--no-retry`, and (import-only) `--quiet`.

- [ ] **Step 4: Run the full test suite**

```bash
pytest crm/tests/ -v
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add crm/cli.py
git commit -m "feat(cli): add --timeout / --no-retry / --quiet to solution import/export"
```

---

### Task 18: Bump version + write CHANGELOG entry

**Files:**
- Modify: `setup.py:5`
- Modify: `CHANGELOG.md`
- Modify: `crm/tests/TEST.md`

- [ ] **Step 1: Bump version in `setup.py`**

```python
setup(
    name="crm",
    version="0.3.0",
    ...
```

- [ ] **Step 2: Prepend the new section to `CHANGELOG.md`**

Insert after the `# Changelog` heading (line 6, before the `## [0.2.0]` section):

```markdown
## [0.3.0] — 2026-05-24

This release lands Spec B from the post-code-review roadmap: a retry
layer on every HTTP call plus a switch to the asynchronous variants of
`ImportSolution` and `ExportSolution`. See
`docs/superpowers/specs/2026-05-24-spec-b-resilience-design.md` for the
full design.

### Breaking

- **`crm.core.solution.import_solution` return shape changes.** Now
  returns `{import_job_id, async_operation_id, status, progress,
  started_on, completed_on, duration_ms}`. Any caller reading the old
  ImportSolution response keys (`ImportJobKey`, etc.) must switch.
- **`crm.core.solution.export_solution` return shape gains keys.**
  New fields: `async_operation_id`, `export_job_id`, `duration_ms`. The
  existing `output`, `bytes`, `managed`, `solution` keys are preserved.
- **Both functions can now block for up to `CRM_ASYNC_TIMEOUT` seconds
  (default 1800).** The sync versions blocked for up to
  `profile.timeout` seconds per HTTP call (default 120) with no
  client-side polling.

### Added

- `D365Backend.request` now retries on `429`, idempotent `5xx`
  (`502`/`503`/`504` on `GET`/`PUT`/`PATCH`/`DELETE`; `503` only on
  `POST`), and retryable transport errors (`ConnectionError`,
  `Timeout`, `ChunkedEncodingError`). Honors `Retry-After`; falls back
  to capped exponential backoff with full jitter.
- `D365Backend.poll_async_operation(async_operation_id, *, timeout,
  import_job_id, on_progress)` — blocks until an
  `asyncoperations(<id>)` row reaches `statecode=3`. Raises
  `D365Error` on failure (`statuscode=31`), cancellation (`32`), or
  timeout.
- `ConnectionProfile` gains seven new fields: `retry_max`,
  `retry_base_delay`, `retry_max_delay`, `retry_jitter`,
  `async_poll_initial`, `async_poll_max`, `async_timeout`.
- Env overrides: `CRM_RETRY_MAX`, `CRM_RETRY_BASE_DELAY`,
  `CRM_RETRY_MAX_DELAY`, `CRM_RETRY_JITTER`, `CRM_ASYNC_TIMEOUT`,
  `CRM_NO_RETRY`. Env wins over profile.
- New CLI flags on `crm solution export` and `crm solution import`:
  `--timeout N` (override `async_timeout` for this call), `--no-retry`
  (set `CRM_NO_RETRY=1` for this call). `crm solution import` also
  gets `--quiet` / `-q` to suppress per-tick progress lines.
- `x-ms-ratelimit-*` headers are logged to stderr on every retried 429,
  and on every response under `CRM_VERBOSE=1`.

### Changed

- `crm solution import` and `crm solution export` now block until the
  async operation reports completion, emitting per-tick progress to
  stderr (import only; suppress with `--quiet`).

[0.3.0]: https://github.com/Gharib89/crm/releases/tag/v0.3.0
```

Update the link reference at the bottom of the file accordingly (leave the existing `[0.2.0]` link in place).

- [ ] **Step 3: Update `crm/tests/TEST.md` test inventory**

In the `## Test Inventory` table, add a row:

```
| `test_resilience.py` | Unit | 49            | None (HTTP mocked with `requests_mock`)    |
```

At the bottom of `crm/tests/TEST.md` (or in an existing "manual smoke" section if present), append:

```markdown
## Manual smoke test — Spec B async solution flow

Pre-req: `D365_URL` / `D365_USERNAME` / `D365_PASSWORD` set against a
MOCE 9.1.44.15 (or any on-prem 9.x) target.

1. Pick a managed solution on the server (e.g. `MySolution`).
2. Export it:
   ```bash
   crm solution export MySolution -o /tmp/MySolution.zip --managed
   ```
   Expected: command blocks, emits `[crm] ratelimit ...` lines only if
   the server rate-limits, exits 0 with JSON containing
   `async_operation_id`, `export_job_id`, `duration_ms`, `bytes > 0`.
3. Re-import it to a sibling org (or the same org after a delete):
   ```bash
   crm solution import /tmp/MySolution.zip
   ```
   Expected: command blocks, emits `[crm] import progress=…%` lines on
   stderr, exits 0 with JSON containing `status=succeeded`,
   `import_job_id`, `async_operation_id`, `progress=100.0`.
4. `--quiet` suppresses the progress lines; `--timeout 60` lowers the
   ceiling; `--no-retry` disables transient retries for the invocation.
```

- [ ] **Step 4: Run the full test suite one last time**

```bash
pytest crm/tests/ -v
```

Expected: green. Count = (Spec A baseline) + 49 (test_resilience.py) + 7 (TestExportSolutionAsync + TestImportSolutionAsync).

- [ ] **Step 5: Pyright clean across the strict zone**

```bash
pyright crm/core/solution.py crm/utils/d365_backend.py
```

Expected: 0 errors.

- [ ] **Step 6: Commit**

```bash
git add setup.py CHANGELOG.md crm/tests/TEST.md
git commit -m "release: bump to 0.3.0; CHANGELOG for Spec B"
```

---

### Task 19: Open PR3

- [ ] **Step 1: Push**

```bash
git push -u origin feat/spec-b-solution-async
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --title "Spec B PR3: async solution import/export + 0.3.0" --body "$(cat <<'EOF'
## Summary

- Rewrite `crm.core.solution.import_solution` on `ImportSolutionAsync` + `poll_async_operation` + `importjobs`
- Rewrite `crm.core.solution.export_solution` on `ExportSolutionAsync` + `poll_async_operation` + `DownloadSolutionExportData`
- New CLI flags: `--timeout`, `--no-retry` on both commands; `--quiet` / `-q` on import
- Per-tick import progress lines on stderr (deduped + 1s throttle)
- Bump to **0.3.0** + CHANGELOG entry covering all three Spec B PRs
- Manual smoke-test note added to `crm/tests/TEST.md`

**Breaking:** `import_solution` return shape now uses `import_job_id`, `async_operation_id`, `status`, `progress`, `started_on`, `completed_on`, `duration_ms`. `export_solution` return gains the same async fields (preserves the prior keys).

Refs Spec B §5, §6, §8 + Spec B PR sequencing §10.

## Test plan

- [ ] `pytest crm/tests/ -v` — full suite green, including 7 new solution tests
- [ ] `pyright crm/core/solution.py crm/utils/d365_backend.py` — 0 errors
- [ ] Manual smoke on MOCE 9.1.44.15 per `crm/tests/TEST.md` (export + import a real solution; verify stderr progress lines and JSON envelope fields)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review checklist (executor)

Before declaring PR3 done:

1. **Spec coverage:**
   - §3 retry mechanism — Tasks 2-9
   - §4 async-op polling — Tasks 11-12
   - §5 `import_solution` rewrite — Task 16
   - §6 `export_solution` rewrite — Task 15
   - §7 rate-limit header surface — Tasks 6-7, 9 step 4
   - §8 CLI surface — Task 17
   - §9 testing — Tasks 2, 4, 6, 8, 11, 14 + §9.2 test_core updates in Task 14
   - §10 PR sequencing — branch + PR structure mirrors §10 exactly
   - §11 out of scope — nothing here; out-of-scope items not touched

2. **No placeholders:** every task has a full code block where code is needed. No "implement X" without showing X.

3. **Type consistency:**
   - `poll_async_operation` signature is identical in spec §4.1, Task 12 step 1, and the tests in Task 11.
   - `import_solution(backend, zip_path, *, publish_workflows, overwrite_unmanaged_customizations, timeout, quiet)` — identical in spec §5.1, Task 16, Task 17.
   - `export_solution(backend, unique_name, output_path, *, managed, ..., timeout)` — identical in spec §6.1, Task 15, Task 17.
   - Helper names match across the spec, tests, and implementation: `_parse_retry_after`, `_compute_delay`, `_is_response_retryable`, `_is_transport_retryable`, `_log_rate_limit_headers`, `_log_retry`.

4. **Frequent commits:** every task ends in one or more commits. No multi-task batches.
