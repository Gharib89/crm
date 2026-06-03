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
import re
import sys as _sys
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Sequence, cast

import logging as _logging

import requests
from requests.auth import AuthBase

from crm.utils.d365_types import BatchOperation, BatchResult

_http_logger = _logging.getLogger("crm.http")

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
        # Optional partial-failure context — set by multi-stage callers
        # (e.g. `optionsets.update_optionset`) to record which steps
        # already landed on the server before this exception fired.
        self.completed_steps: list[str] | None = None
        self.stage: str | None = None


@dataclass
class ConnectionProfile:
    """A reusable D365 connection profile (no secrets)."""

    name: str
    url: str                      # e.g. https://crm.contoso.local/contoso
    domain: str
    username: str
    api_version: str = "v9.2"
    verify_ssl: bool = True
    auth_scheme: str = "ntlm"      # ntlm | kerberos | negotiate | oauth
    tenant_id: str | None = None   # oauth: AAD tenant (non-secret)
    client_id: str | None = None   # oauth: app-registration id (non-secret)
    default_solution: str | None = None  # uniquename for MSCRM.SolutionUniqueName
    publisher_prefix: str | None = None  # schema-name prefix, e.g. "new"
    timeout: int = 120
    retry_max: int = 5
    retry_base_delay: float = 1.0
    retry_max_delay: float = 60.0
    retry_jitter: bool = True
    async_poll_initial: float = 2.0
    async_poll_max: float = 30.0
    async_timeout: int = 1800

    def __post_init__(self) -> None:
        if self.auth_scheme not in ("ntlm", "kerberos", "negotiate", "oauth"):
            raise D365Error(
                f"ConnectionProfile.auth_scheme must be ntlm|kerberos|negotiate|oauth, "
                f"got {self.auth_scheme!r}"
            )
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
            "auth_scheme": self.auth_scheme,
            "tenant_id": self.tenant_id,
            "client_id": self.client_id,
            "default_solution": self.default_solution,
            "publisher_prefix": self.publisher_prefix,
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
            auth_scheme=d.get("auth_scheme", "ntlm"),
            tenant_id=d.get("tenant_id"),
            client_id=d.get("client_id"),
            default_solution=d.get("default_solution"),
            publisher_prefix=d.get("publisher_prefix"),
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


def _oauth_cache_path() -> str | None:
    """Persistent msal token-cache path, or None if CRM_HOME is unwritable."""
    root = _os.path.expanduser(
        _os.environ.get("CRM_HOME") or _os.path.join(_os.path.expanduser("~"), ".crm")
    )
    try:
        _os.makedirs(root, exist_ok=True)
    except OSError:
        return None
    return _os.path.join(root, "msal_token_cache.json")


class _OAuthBearerAuth(AuthBase):
    """Injects an AAD bearer token (client-credentials) on every request.

    Token acquisition is delegated to msal, which serves a valid token from
    its cache and only round-trips to AAD when none is available. The cache is
    persisted to disk (0600) so the token survives across `crm` invocations.
    """

    def __init__(self, msal_mod: Any, client_id: str, authority: str, secret: str,
                 scope: str, cache: Any = None, cache_path: str | None = None) -> None:
        self._msal = msal_mod
        self._client_id = client_id
        self._authority = authority
        self._secret = secret
        self._scope = scope
        self._cache = cache
        self._cache_path = cache_path
        self._app: Any = None

    def _ensure_app(self) -> Any:
        # Built lazily on first request: msal validates the authority over the
        # network at construction, so this must not run when the backend is built.
        if self._app is None:
            try:
                self._app = self._msal.ConfidentialClientApplication(
                    self._client_id,
                    authority=self._authority,
                    client_credential=self._secret,
                    token_cache=self._cache,
                )
            except Exception as exc:
                raise D365Error(
                    f"OAuth setup failed: {exc}\n"
                    "Verify D365_TENANT_ID and that the org URL is a reachable "
                    "public-cloud Dataverse host.",
                    status=401,
                ) from exc
        return self._app

    def __call__(self, request: requests.PreparedRequest) -> requests.PreparedRequest:
        app = self._ensure_app()
        try:
            result: dict[str, Any] = app.acquire_token_for_client(scopes=[self._scope])
        except Exception as exc:
            raise D365Error(f"OAuth token acquisition failed: {exc}", status=401) from exc
        token = result.get("access_token")
        if not token:
            err = result.get("error", "unknown_error")
            desc = result.get("error_description", "")
            # No automatic refresh-retry: a rejected credential is operational.
            raise D365Error(
                f"OAuth token acquisition failed ({err}). {desc}\n"
                "Check the app registration (client id/secret, tenant) and that "
                "an application user for this app exists in Dynamics with the "
                "right security role.",
                status=401,
            )
        request.headers["Authorization"] = f"Bearer {token}"
        self._persist_cache()
        return request

    def _persist_cache(self) -> None:
        cache = self._cache
        path = self._cache_path
        if cache is None or path is None or not getattr(cache, "has_state_changed", False):
            return
        try:
            fd = _os.open(path, _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600)
            try:
                _os.write(fd, cache.serialize().encode("utf-8"))
            finally:
                _os.close(fd)
            _os.chmod(path, 0o600)  # enforce 0600 even if the file pre-existed
        except OSError:
            pass  # best-effort: the in-memory token is still valid this run


class D365Backend:
    """HTTP client for the D365 on-prem 9.x Web API.

    Stateless aside from the requests.Session it holds for keep-alive + auth.
    """

    def __init__(self, profile: ConnectionProfile, password: str,
                 dry_run: bool = False):
        if not profile.url:
            raise D365Error("Profile is missing the server URL.")
        if profile.auth_scheme != "oauth" and not profile.username:
            raise D365Error("Profile is missing the username.")

        self.profile = profile
        self.dry_run = dry_run
        self._session: requests.Session = requests.Session()
        self._session.auth = self._make_auth(password)
        self._session.verify = profile.verify_ssl
        self._effective_retry_max = _resolve_retry_max(profile)
        self._default_caller_id: str | None = _resolve_caller_id()
        self._default_suppress_dup: bool = _resolve_bool_env("CRM_SUPPRESS_DUP")
        self._default_bypass_plugins: bool = _resolve_bool_env("CRM_BYPASS_PLUGINS")

    # ── Auth helpers ─────────────────────────────────────────────────────

    def _make_auth(self, password: str) -> Any:
        """Pick the auth adapter based on profile.auth_scheme."""
        scheme = self.profile.auth_scheme
        if scheme == "ntlm":
            if HttpNtlmAuth is None:
                raise D365Error(
                    "requests_ntlm is not installed. "
                    "Install with: pip install requests_ntlm"
                )
            user_principal = (
                f"{self.profile.domain}\\{self.profile.username}"
                if self.profile.domain else self.profile.username
            )
            return HttpNtlmAuth(user_principal, password)
        if scheme in ("kerberos", "negotiate"):
            try:
                from requests_negotiate_sspi import HttpNegotiateAuth  # type: ignore[import-untyped]
            except ImportError as exc:
                raise D365Error(
                    "Kerberos/Negotiate auth requires 'requests_negotiate_sspi'. "
                    "Install it: pip install crm[kerberos]"
                ) from exc
            return HttpNegotiateAuth()  # type: ignore[no-any-return]
        if scheme == "oauth":
            return self._make_oauth_auth(password)
        raise D365Error(
            f"Unknown auth_scheme {scheme!r}; expected ntlm|kerberos|negotiate|oauth"
        )

    def _make_oauth_auth(self, secret: str) -> AuthBase:
        """Build a bearer-token auth via OAuth 2.0 client-credentials.

        msal is lazy-imported (startup perf) though it is a base dependency.
        Scope is derived from the org host; authority is the public cloud.
        """
        try:
            import msal  # type: ignore[import-untyped]
        except ImportError as exc:
            raise D365Error(
                "OAuth auth requires 'msal'. Install it: pip install msal"
            ) from exc
        _msal: Any = msal  # msal ships no type stubs; launder member access to Any

        tenant = self.profile.tenant_id
        client = self.profile.client_id
        if not tenant or not client:
            raise D365Error(
                "OAuth requires tenant_id and client_id on the profile "
                "(set D365_TENANT_ID and D365_CLIENT_ID)."
            )

        host = urllib.parse.urlparse(self.profile.url).netloc
        scope = f"https://{host}/.default"
        authority = f"https://login.microsoftonline.com/{tenant}"

        cache: Any = _msal.SerializableTokenCache()
        cache_path = _oauth_cache_path()
        if cache_path is not None and _os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as fh:
                    cache.deserialize(fh.read())
            except (OSError, ValueError):
                pass  # corrupt/unreadable cache → start fresh, overwrite on next save

        return _OAuthBearerAuth(_msal, client, authority, secret, scope, cache, cache_path)

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
        caller_id: str | None = None,
        suppress_duplicate_detection: bool | None = None,
        bypass_custom_plugin_execution: bool | None = None,
        etag: str | None = None,
    ) -> dict[str, Any] | str | None:
        """Issue an HTTP request and return parsed JSON (or None for 204).

        Retries on 429, idempotent 5xx, and retryable transport errors per the
        backend's profile + env config. Honors self.dry_run by returning a
        preview dict instead of issuing the call.

        caller_id tri-state:
          - None (default): use the CRM_AS_USER env default (self._default_caller_id).
          - ""  (empty str): disable impersonation for this call, even if CRM_AS_USER is set.
          - any other str:  use this GUID directly; must be a valid UUID.

        Raises D365Error on transport failure or non-2xx response after retries
        are exhausted.
        """
        url = self.url_for(path)
        headers = dict(_DEFAULT_HEADERS)
        if extra_headers:
            headers.update(extra_headers)

        if caller_id is None:
            effective_caller = self._default_caller_id
        elif caller_id == "":
            effective_caller = None
        else:
            effective_caller = caller_id

        if effective_caller is not None:
            try:
                uuid.UUID(effective_caller)
            except ValueError as exc:
                raise D365Error(
                    f"Invalid GUID for caller_id: {effective_caller!r}"
                ) from exc
            headers["MSCRMCallerID"] = effective_caller
        else:
            headers.pop("MSCRMCallerID", None)

        effective_suppress = (
            suppress_duplicate_detection
            if suppress_duplicate_detection is not None
            else self._default_suppress_dup
        )
        if effective_suppress:
            headers["MSCRM.SuppressDuplicateDetection"] = "true"
        else:
            headers.pop("MSCRM.SuppressDuplicateDetection", None)

        effective_bypass = (
            bypass_custom_plugin_execution
            if bypass_custom_plugin_execution is not None
            else self._default_bypass_plugins
        )
        if effective_bypass:
            headers["MSCRM.BypassCustomPluginExecution"] = "true"
        else:
            headers.pop("MSCRM.BypassCustomPluginExecution", None)

        if etag is not None:
            if etag == "":
                raise D365Error("etag must be non-empty")
            if method.upper() not in ("PATCH", "DELETE"):
                raise D365Error(f"etag not valid on {method.upper()}")
            headers["If-Match"] = etag

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
                _http_logger.debug("request", extra={"event": "request", "method": method, "url": url})
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

            elapsed_ms = int((resp.elapsed.total_seconds() if resp.elapsed else 0) * 1000)
            _http_logger.debug("response", extra={"event": "response", "status": resp.status_code, "ms": elapsed_ms})

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

    def put(self, path: str, json_body: Any = None, **kw: Any) -> dict[str, Any] | str | None:
        return self.request("PUT", path, json_body=json_body, **kw)

    def delete(self, path: str, **kw: Any) -> dict[str, Any] | str | None:
        return self.request("DELETE", path, expect_json=False, **kw)

    def batch(
        self,
        operations: "Sequence[BatchOperation]",
        *,
        transactional: bool = True,
        continue_on_error: bool = False,
        timeout: int | None = None,
    ) -> list[BatchResult]:
        """Execute a list of operations via POST $batch.

        See spec C §4 for transactional semantics, request shape, and
        size/count limits. Returns one BatchResult per input op in input order.
        """
        validated: list[dict[str, Any]] = []
        for i, op in enumerate(operations):
            if "method" not in op or "url" not in op:
                raise D365Error(f"batch op #{i} missing method or url: {op!r}")
            m_upper = op["method"].upper()
            if m_upper not in ("GET", "POST", "PATCH", "DELETE"):
                raise D365Error(f"batch op #{i} invalid method: {op['method']!r}")
            if m_upper in ("GET", "DELETE") and op.get("body") is not None:
                raise D365Error(
                    f"batch op #{i}: body not allowed on {m_upper}"
                )
            if m_upper in ("POST", "PATCH") and op.get("body") is None:
                raise D365Error(
                    f"batch op #{i}: body required on {m_upper}"
                )
            cid = op.get("content_id")
            if cid is not None:
                if isinstance(cid, bool) or not isinstance(cid, (str, int)):  # pyright: ignore[reportUnnecessaryIsInstance]
                    raise D365Error(
                        f"batch op #{i}: content_id must be str or int; got {type(cid).__name__}"
                    )
                if isinstance(cid, str) and not cid:
                    raise D365Error(f"batch op #{i}: content_id must be non-empty")
            validated.append({**op, "method": m_upper})

        if self.dry_run:
            return cast(list[BatchResult], [
                {
                    "method": op["method"],
                    "url": op["url"],
                    "status": 0,
                    "headers": {},
                    "body": None,
                    "error": "dry-run",
                }
                for op in validated
            ])

        body_text, content_type = _assemble_batch_body(
            validated, transactional=transactional,
        )

        headers = dict(_DEFAULT_HEADERS)
        headers["Content-Type"] = content_type
        if continue_on_error:
            headers["Prefer"] = "odata.continue-on-error"

        effective_timeout = timeout if timeout is not None else self.profile.timeout
        url = self.url_for("$batch")
        max_attempts = self._effective_retry_max + 1
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self._session.request(  # pyright: ignore[reportUnknownMemberType]
                    "POST", url,
                    data=body_text.encode("utf-8"),
                    headers=headers,
                    timeout=effective_timeout,
                )
            except requests.RequestException as exc:
                raise D365Error(f"HTTP transport failure on $batch: {exc}") from exc

            if resp.status_code in (429, 503) and attempt < max_attempts:
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                delay = retry_after if retry_after is not None else _compute_delay(
                    attempt - 1, self.profile, retry_after=None
                )
                _log_retry("POST", url, attempt - 1, delay,
                           effective_max=self._effective_retry_max, reason=f"HTTP {resp.status_code}")
                resp.close()
                time.sleep(delay)
                continue
            break

        if not (200 <= resp.status_code < 300):
            raise D365Error(
                f"$batch failed: HTTP {resp.status_code}: {resp.text[:500]}",
                status=resp.status_code,
                response_body=resp.text,
            )

        return cast(list[BatchResult], _parse_batch_response(
            resp.content, resp.headers.get("Content-Type", ""), validated,
            transactional=transactional,
        ))

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
        (percent, asyncoperations.message) to the callback.

        In dry-run mode this short-circuits and returns a preview dict instead
        of polling — request() can only produce a preview without statecode,
        which would otherwise hang until async_timeout.

        Raises:
            D365Error on operation failure (statuscode != 30) or timeout.
        """
        if timeout is not None and timeout < 0:
            raise D365Error(
                f"poll_async_operation timeout must be >= 0, got {timeout!r}"
            )
        if self.dry_run:
            return {
                "_dry_run": True,
                "async_operation_id": async_operation_id,
                "import_job_id": import_job_id,
                "timeout": timeout if timeout is not None else self.profile.async_timeout,
            }
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
            # Return text/plain and XML bodies as a stripped string; otherwise None.
            ctype = resp.headers.get("Content-Type", "")
            if ctype.startswith(("text/plain", "application/xml", "text/xml")):
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

    if resp.status_code == 412:
        code = "PreconditionFailed"
    elif (
        resp.status_code == 403
        and "prvBypassCustomPluginExecution" in message
    ):
        code = "MissingPrivilege"

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


def _resolve_caller_id() -> str | None:
    """Resolve CRM_AS_USER env into a validated GUID string or None.

    Raises D365Error if the env value is present but not a valid GUID.
    """
    raw = _os.environ.get("CRM_AS_USER")
    if raw is None or raw.strip() == "":
        return None
    value = raw.strip()
    try:
        uuid.UUID(value)
    except ValueError as exc:
        raise D365Error(
            f"CRM_AS_USER must be a GUID; got {value!r}"
        ) from exc
    return value


def _resolve_bool_env(name: str) -> bool:
    """Resolve a boolean-style env var. Empty/unset returns False."""
    return _env_truthy(name)


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


def _format_http_part(op: dict[str, Any], content_id: int | str | None = None) -> str:
    """Render one operation as an `application/http` MIME part body."""
    method = op["method"].upper()
    url = op["url"]
    extra: dict[str, str] = op.get("headers") or {}
    lines: list[str] = ["Content-Type: application/http",
                        "Content-Transfer-Encoding: binary"]
    if content_id is not None:
        lines.append(f"Content-ID: {content_id}")
    lines.append("")
    lines.append(f"{method} {url} HTTP/1.1")
    if method in ("POST", "PATCH"):
        lines.append("Content-Type: application/json")
    for hk, hv in extra.items():
        lines.append(f"{hk}: {hv}")
    lines.append("")
    if "body" in op and op["body"] is not None:
        lines.append(json.dumps(op["body"]))
    return "\r\n".join(lines)


def _assemble_batch_body(
    operations: Sequence[dict[str, Any]],
    *,
    transactional: bool,
) -> tuple[str, str]:
    """Assemble a multipart/mixed batch body. Returns (body_text, content_type)."""
    batch_boundary = f"batch_{uuid.uuid4().hex}"
    out: list[str] = []

    def _emit_top_get(op: dict[str, Any]) -> None:
        out.append(f"--{batch_boundary}")
        out.append(_format_http_part(op))

    def _emit_top_write(op: dict[str, Any]) -> None:
        out.append(f"--{batch_boundary}")
        out.append(_format_http_part(op))

    def _emit_changeset(write_ops: list[dict[str, Any]]) -> None:
        cs_boundary = f"changeset_{uuid.uuid4().hex}"
        out.append(f"--{batch_boundary}")
        out.append(f"Content-Type: multipart/mixed; boundary={cs_boundary}")
        out.append("")
        seen_ids: set[int | str] = set()
        for i, op in enumerate(write_ops, start=1):
            caller_cid: int | str | None = op.get("content_id")
            cid: int | str = caller_cid if caller_cid is not None else i
            if cid in seen_ids:
                raise D365Error(f"batch changeset: duplicate content_id {cid!r}")
            seen_ids.add(cid)
            out.append(f"--{cs_boundary}")
            out.append(_format_http_part(op, content_id=cid))
        out.append(f"--{cs_boundary}--")

    write_buffer: list[dict[str, Any]] = []
    for op in operations:
        method = op["method"].upper()
        is_write = method in ("POST", "PATCH", "DELETE")
        if transactional and is_write:
            write_buffer.append(op)
            continue
        if write_buffer:
            _emit_changeset(write_buffer)
            write_buffer = []
        if method == "GET":
            _emit_top_get(op)
        else:
            _emit_top_write(op)
    if write_buffer:
        _emit_changeset(write_buffer)

    out.append(f"--{batch_boundary}--")
    body_text = "\r\n".join(out) + "\r\n"
    return body_text, f"multipart/mixed; boundary={batch_boundary}"


def _split_mime_parts(body: bytes, boundary: str) -> list[bytes]:
    """Split a multipart body on its boundary, ignoring preamble/epilogue."""
    sep = f"--{boundary}".encode("utf-8")
    chunks = body.split(sep)
    # First chunk is preamble (often empty); last is "--\r\n" epilogue marker.
    parts: list[bytes] = []
    for c in chunks[1:]:
        c = c.lstrip(b"\r\n")
        if c.startswith(b"--"):
            break
        if c.endswith(b"\r\n"):
            c = c[:-2]
        parts.append(c)
    return parts


def _parse_http_subpart(raw: bytes) -> dict[str, Any]:
    """Parse one application/http subpart into a BatchResult dict."""
    # Strip the leading MIME headers (Content-Type: application/http, etc.)
    sep = raw.find(b"\r\n\r\n")
    if sep < 0:
        return {"method": "", "url": "", "status": 0, "headers": {}, "body": None,
                "error": "malformed batch subpart"}
    mime_headers_raw = raw[:sep].decode("utf-8", errors="replace")
    http_block = raw[sep + 4:]

    # First line of http_block: "HTTP/1.1 <code> <reason>"
    status_sep = http_block.find(b"\r\n")
    if status_sep < 0:
        return {"method": "", "url": "", "status": 0, "headers": {}, "body": None,
                "error": "malformed status line"}
    status_line = http_block[:status_sep].decode("utf-8", errors="replace").strip()
    rest = http_block[status_sep + 2:]
    m = re.match(r"^HTTP/[\d.]+\s+(\d+)", status_line)
    status = int(m.group(1)) if m else 0

    # Parse remaining headers + body
    body_sep = rest.find(b"\r\n\r\n")
    if body_sep < 0:
        header_text = rest.decode("utf-8", errors="replace")
        body_text = ""
    else:
        header_text = rest[:body_sep].decode("utf-8", errors="replace")
        body_text = rest[body_sep + 4:].decode("utf-8", errors="replace").strip()

    headers: dict[str, str] = {}
    for line in header_text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()

    parsed_body: Any
    if body_text:
        try:
            parsed_body = json.loads(body_text)
        except ValueError:
            parsed_body = body_text
    else:
        parsed_body = None

    content_id = None
    for line in mime_headers_raw.splitlines():
        if line.lower().startswith("content-id:"):
            content_id = line.split(":", 1)[1].strip()
            break

    error: str | None = None
    if not (200 <= status < 300):
        if isinstance(parsed_body, dict):
            pb = cast(dict[str, Any], parsed_body)
            err: dict[str, Any] | None = pb.get("error") if isinstance(pb.get("error"), dict) else None
            if err is not None:
                error = str(err.get("message") or f"HTTP {status}")
            else:
                error = f"HTTP {status}"
        else:
            error = f"HTTP {status}: {body_text[:200]}" if body_text else f"HTTP {status}"

    return {
        "method": "",
        "url": "",
        "status": status,
        "headers": headers,
        "body": parsed_body,
        "error": error,
        "_content_id": content_id,
    }


def _parse_batch_response(
    body: bytes,
    content_type: str,
    operations: "Sequence[dict[str, Any]]",
    *,
    transactional: bool = True,
) -> list[dict[str, Any]]:
    """Parse a multipart/mixed $batch response into one BatchResult per input op."""
    m = re.search(r'boundary=([^;\s]+)', content_type)
    if not m:
        raise D365Error(f"Cannot find boundary in $batch response Content-Type: {content_type!r}")
    boundary = m.group(1).strip('"')

    # Walk input ops to determine the top-level slot order, changeset groups,
    # and per-changeset cid→op_index maps (supports both int and str content_id).
    # transactional=True:  GETs are top-level; consecutive writes go into changesets.
    # transactional=False: ALL ops are top-level (no changeset wrapping).
    top_level_indexes: list[int] = []
    changeset_groups: list[list[int]] = []
    changeset_cid_maps: list[dict[int | str, int]] = []
    current_group: list[int] = []
    current_cid_map: dict[int | str, int] = {}
    seq_counter = 0
    for i, op in enumerate(operations):
        if not transactional or op["method"].upper() == "GET":
            if current_group:
                changeset_groups.append(current_group)
                changeset_cid_maps.append(current_cid_map)
                current_group = []
                current_cid_map = {}
                seq_counter = 0
            top_level_indexes.append(i)
        else:
            current_group.append(i)
            seq_counter += 1
            _raw_cid: object = op.get("content_id")
            cid: int | str = cast("int | str", _raw_cid) if _raw_cid is not None else seq_counter
            current_cid_map[cid] = i
    if current_group:
        changeset_groups.append(current_group)
        changeset_cid_maps.append(current_cid_map)

    results: list[dict[str, Any] | None] = [None] * len(operations)
    top_level_cursor = 0
    changeset_cursor = 0

    for part in _split_mime_parts(body, boundary):
        ctype_match = re.search(rb"Content-Type:\s*([^\r\n;]+)", part, re.IGNORECASE)
        ctype_val = ctype_match.group(1).decode("utf-8").strip() if ctype_match else ""
        if ctype_val.lower() == "multipart/mixed":
            # Changeset response
            inner_m = re.search(rb"boundary=([^\r\n;]+)", part, re.IGNORECASE)
            if not inner_m:
                continue
            inner_boundary = inner_m.group(1).decode("utf-8").strip('"')
            if changeset_cursor >= len(changeset_groups):
                continue
            group = changeset_groups[changeset_cursor]
            cid_map = changeset_cid_maps[changeset_cursor]
            changeset_cursor += 1
            inner_parts = _split_mime_parts(part, inner_boundary)
            # Match inner parts to group by Content-ID; fall back to positional.
            for sub_idx, inner in enumerate(inner_parts):
                parsed = _parse_http_subpart(inner)
                cid_raw = parsed.get("_content_id")
                resolved_cid: int | str | None = None
                if cid_raw is not None:
                    try:
                        resolved_cid = int(cid_raw)
                    except ValueError:
                        resolved_cid = cid_raw  # keep as string
                if resolved_cid is not None and resolved_cid in cid_map:
                    op_index = cid_map[resolved_cid]
                elif 0 <= sub_idx < len(group):
                    op_index = group[sub_idx]
                else:
                    continue
                parsed["method"] = operations[op_index]["method"]
                parsed["url"] = operations[op_index]["url"]
                parsed.pop("_content_id", None)
                results[op_index] = parsed
        else:
            parsed = _parse_http_subpart(part)
            parsed.pop("_content_id", None)
            if top_level_cursor < len(top_level_indexes):
                op_index = top_level_indexes[top_level_cursor]
                parsed["method"] = operations[op_index]["method"]
                parsed["url"] = operations[op_index]["url"]
                results[op_index] = parsed
                top_level_cursor += 1

    # Backfill any missing slots with an error placeholder.
    for i, r in enumerate(results):
        if r is None:
            results[i] = {
                "method": operations[i]["method"],
                "url": operations[i]["url"],
                "status": 0,
                "headers": {},
                "body": None,
                "error": "no matching response part",
            }
    return [r for r in results if r is not None]


def as_dict(result: dict[str, Any] | str | None) -> dict[str, Any]:
    """Narrow a backend response to a dict (treat str/None as empty).

    Used by core/* callers that need dict semantics — preserves the existing
    `result or {}` idiom in a type-safe way under pyright strict.
    """
    return result if isinstance(result, dict) else {}
