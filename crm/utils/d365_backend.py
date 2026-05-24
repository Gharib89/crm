"""D365 on-prem 9.x Web API HTTP backend.

Wraps `requests` + `requests_ntlm` to talk to the live Dataverse Web API at
`https://<server>/<org>/api/data/v9.x/`.

This module is the **only** place in the harness that talks HTTP to the server.
Every other core module asks the backend to issue a request and gets back the
parsed JSON or a raised `D365Error`.
"""

from __future__ import annotations

import json
import os as _os
import random
import sys as _sys
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable, cast

import requests

try:
    from requests_ntlm import HttpNtlmAuth
except ImportError:
    HttpNtlmAuth = None  # type: ignore


class D365Error(RuntimeError):
    """Raised when the Web API returns an error or the request itself fails."""

    def __init__(self, message: str, *, status: int | None = None,
                 code: str | None = None, response_body: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.response_body = response_body


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

    def __post_init__(self) -> None:
        for _field, _value in (
            ("retry_max", self.retry_max),
            ("retry_base_delay", self.retry_base_delay),
            ("retry_max_delay", self.retry_max_delay),
            ("async_timeout", self.async_timeout),
        ):
            if _value < 0:
                raise D365Error(
                    f"ConnectionProfile.{_field} must be >= 0, got {_value!r}"
                )
        for _field, _value in (
            ("async_poll_initial", self.async_poll_initial),
            ("async_poll_max", self.async_poll_max),
        ):
            if _value <= 0:
                raise D365Error(
                    f"ConnectionProfile.{_field} must be > 0, got {_value!r}"
                )
        if self.async_poll_max < self.async_poll_initial:
            raise D365Error(
                f"ConnectionProfile.async_poll_max ({self.async_poll_max}) must be "
                f">= async_poll_initial ({self.async_poll_initial})"
            )

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


# ── Default headers per Web API spec ────────────────────────────────────

_DEFAULT_HEADERS: dict[str, str] = {
    "OData-MaxVersion": "4.0",
    "OData-Version": "4.0",
    "Accept": "application/json",
    "Content-Type": "application/json; charset=utf-8",
}


class D365Backend:
    """HTTP client for the D365 on-prem 9.x Web API.

    Stateless aside from the requests.Session it holds for keep-alive + auth.
    """

    def __init__(self, profile: ConnectionProfile, password: str,
                 dry_run: bool = False):
        if HttpNtlmAuth is None:
            raise D365Error(
                "requests_ntlm is not installed. Install with: pip install requests_ntlm"
            )
        if not profile.url:
            raise D365Error("Profile is missing the server URL.")
        if not profile.username:
            raise D365Error("Profile is missing the username.")

        self.profile = profile
        self.dry_run = dry_run
        self._session: requests.Session = requests.Session()
        user_principal = (
            f"{profile.domain}\\{profile.username}" if profile.domain else profile.username
        )
        self._session.auth = HttpNtlmAuth(user_principal, password)
        self._session.verify = profile.verify_ssl
        self._effective_retry_max = _resolve_retry_max(profile)

    # ── URL helpers ─────────────────────────────────────────────────────

    def url_for(self, path: str) -> str:
        """Resolve a relative API path against the profile base URL."""
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urllib.parse.urljoin(self.profile.api_base, path.lstrip("/"))

    # ── Core request ────────────────────────────────────────────────────

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

            # One log per response: always emit when status warrants retry
            # (i.e., a 429/5xx that has rate-limit headers); otherwise emit
            # only when CRM_VERBOSE=1 is set.
            retryable = _is_response_retryable(resp, method)
            _log_rate_limit_headers(resp, on_retryable=retryable)

            if not retryable:
                return _parse_response(resp, expect_json=expect_json)

            if attempt >= max_retries:
                return _parse_response(resp, expect_json=expect_json)  # raises D365Error

            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            delay = _compute_delay(attempt, self.profile, retry_after=retry_after)
            _log_retry(method, url, attempt, delay,
                       effective_max=max_retries, reason=f"HTTP {resp.status_code}")
            resp.close()
            time.sleep(delay)
            attempt += 1

    # ── Convenience verbs ───────────────────────────────────────────────

    def get(self, path: str, **kw: Any) -> dict[str, Any] | str | None:
        return self.request("GET", path, **kw)

    def post(self, path: str, json_body: Any = None, **kw: Any) -> dict[str, Any] | str | None:
        return self.request("POST", path, json_body=json_body, **kw)

    def patch(self, path: str, json_body: Any = None, **kw: Any) -> dict[str, Any] | str | None:
        return self.request("PATCH", path, json_body=json_body, **kw)

    def delete(self, path: str, **kw: Any) -> dict[str, Any] | str | None:
        return self.request("DELETE", path, expect_json=False, **kw)

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


# ── Response parsing ────────────────────────────────────────────────────


def _parse_response(resp: requests.Response, *, expect_json: bool) -> dict[str, Any] | str | None:
    """Parse a Web API response. Raises D365Error on non-2xx."""
    if 200 <= resp.status_code < 300:
        if resp.status_code == 204 or not resp.content:
            entity_id = resp.headers.get("OData-EntityId")
            if entity_id:
                return {"_entity_id_url": entity_id}
            return None
        if not expect_json:
            # Return text/plain bodies as a stripped string; otherwise None as before.
            ctype = resp.headers.get("Content-Type", "")
            if ctype.startswith("text/plain"):
                text = resp.text.strip()
                return text if text else None
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise D365Error(
                f"Server returned 2xx but body was not JSON: {resp.text[:500]}"
            ) from exc

    # Error path
    body: Any = None
    code: str | None = None
    message: str = f"HTTP {resp.status_code}"
    try:
        body = resp.json()
        err = cast(dict[str, Any], body).get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            err_d = cast(dict[str, Any], err)
            code_val = err_d.get("code")
            if isinstance(code_val, str):
                code = code_val
            msg_val = err_d.get("message")
            if isinstance(msg_val, str):
                message = msg_val
    except ValueError:
        body = resp.text
        message = f"HTTP {resp.status_code}: {resp.text[:500]}"

    raise D365Error(message, status=resp.status_code, code=code, response_body=body)


def _compute_delay(
    attempt: int,
    profile: "ConnectionProfile",
    *,
    retry_after: float | None,
) -> float:
    """Compute the sleep duration before the next retry attempt."""
    if retry_after is not None:
        return min(retry_after, profile.retry_max_delay)
    base = min(profile.retry_base_delay * (2 ** attempt), profile.retry_max_delay)
    if profile.retry_jitter:
        return random.uniform(0.0, base)
    return base


def _is_response_retryable(resp: "requests.Response", method: str) -> bool:
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
    if isinstance(exc, requests.exceptions.SSLError):
        return False
    return isinstance(exc, _RETRYABLE_TRANSPORT_TYPES)


_RATE_LIMIT_HEADER_MAP = (
    ("x-ms-ratelimit-time-remaining-xrm-requests", "time-remaining"),
    ("x-ms-ratelimit-burst-remaining-xrm-requests", "burst-remaining"),
    ("x-ms-ratelimit-limit-xrm-requests", "limit"),
    ("Retry-After", "retry-after"),
)


def _log_rate_limit_headers(resp: requests.Response, *, on_retryable: bool) -> None:
    """Emit one stderr line with x-ms-ratelimit-* + Retry-After values present.

    on_retryable=True: log always when any header is present (used on 429/5xx retry responses).
    on_retryable=False: log only when CRM_VERBOSE=1 in env (used on every other response).
    """
    if not on_retryable and not _env_truthy("CRM_VERBOSE"):
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


def as_dict(result: dict[str, Any] | str | None) -> dict[str, Any]:
    """Narrow a backend response to a dict (treat str/None as empty).

    Used by core/* callers that need dict semantics — preserves the existing
    `result or {}` idiom in a type-safe way under pyright strict.
    """
    return result if isinstance(result, dict) else {}
