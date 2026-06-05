"""High-level connection management: build profiles, resolve creds, test reachability.

Env vars (canonical D365_* and CRM_* aliases for compatibility with
existing Contoso-style PowerShell tooling):

    D365_URL          | CRM_BASE_URL          required
    D365_USERNAME     | CRM_USERNAME          required (DOMAIN\\user accepted)
    D365_PASSWORD     | CRM_PASSWORD          required
    D365_DOMAIN       | CRM_DOMAIN            optional (else parsed from username)
    D365_API_VERSION  | CRM_API_VERSION       optional, default v9.2
    D365_AUTH         | CRM_AUTH              optional, default ntlm
    D365_VERIFY_SSL   | CRM_VERIFY_SSL        optional, default 1

A `.env` file is auto-loaded from the current working directory, the directory
above, or the path in CRM_DOTENV. Existing real env vars take precedence.
"""

from __future__ import annotations

import os
import socket
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from crm.utils.d365_backend import (
    ConnectionProfile,
    D365Backend,
    D365Error,
    # Reuse the client's canonical OData headers so the raw doctor probes stay
    # faithful to a real request.
    DEFAULT_HEADERS,
)
from crm.core import session as session_mod


# ── Env var name groups (D365_* canonical, CRM_* alias) ─────────────────

ENV_URL = "D365_URL"
ENV_DOMAIN = "D365_DOMAIN"
ENV_USERNAME = "D365_USERNAME"
ENV_PASSWORD = "D365_PASSWORD"
ENV_API_VERSION = "D365_API_VERSION"
ENV_VERIFY_SSL = "D365_VERIFY_SSL"
ENV_AUTH = "D365_AUTH"
ENV_TENANT_ID = "D365_TENANT_ID"
ENV_CLIENT_ID = "D365_CLIENT_ID"
ENV_CLIENT_SECRET = "D365_CLIENT_SECRET"

_ENV_ALIASES = {
    ENV_URL: ("CRM_BASE_URL", "CRM_URL"),
    ENV_DOMAIN: ("CRM_DOMAIN",),
    ENV_USERNAME: ("CRM_USERNAME", "CRM_USER"),
    ENV_PASSWORD: ("CRM_PASSWORD", "CRM_PASS"),
    ENV_API_VERSION: ("CRM_API_VERSION",),
    ENV_VERIFY_SSL: ("CRM_VERIFY_SSL",),
    ENV_AUTH: ("CRM_AUTH",),
    ENV_TENANT_ID: ("CRM_TENANT_ID",),
    ENV_CLIENT_ID: ("CRM_CLIENT_ID",),
    ENV_CLIENT_SECRET: ("CRM_CLIENT_SECRET",),
}


def _env(name: str, default: str = "") -> str:
    """Lookup an env var, falling back to known aliases."""
    val = os.environ.get(name)
    if val is not None and val.strip():
        return val
    for alias in _ENV_ALIASES.get(name, ()):
        val = os.environ.get(alias)
        if val is not None and val.strip():
            return val
    return default


def env_api_version() -> str:
    """The api_version explicitly set via D365_API_VERSION / CRM_API_VERSION, else ''.

    Public so command modules can detect an env-pinned version (to decide whether
    to negotiate) without reaching into the private _env() helper. Callers should
    load_dotenv() first if a .env file may carry the value.
    """
    return _env(ENV_API_VERSION).strip()


# ── .env autoload ───────────────────────────────────────────────────────


def load_dotenv(path: str | os.PathLike[str] | None = None, *, override: bool = False) -> Path | None:
    """Load KEY=VALUE pairs from a `.env` file into os.environ.

    Lookup when path is None:
        - If CRM_DOTENV is set, it is authoritative: load exactly that file,
          or load nothing (return None) if it does not exist. No fallback.
        - Otherwise auto-discover ./.env in cwd, then ../.env (one level up).

    Returns the resolved path actually loaded, or None if no file was found.
    """
    if path is None:
        env_override = os.environ.get("CRM_DOTENV")
        if env_override:
            candidates: list[Path] = [Path(env_override)]
        else:
            cwd = Path.cwd()
            candidates = [cwd / ".env", cwd.parent / ".env"]
        chosen = next((p for p in candidates if p.is_file()), None)
        if chosen is None:
            return None
        path = chosen
    p = Path(path)
    if not p.is_file():
        return None

    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        key, _, raw = s.partition("=")
        key = key.strip()
        if key.lower().startswith("export "):
            key = key[7:].strip()
        if not key:
            continue
        raw_value = raw.strip()
        if len(raw_value) >= 2 and raw_value[0] == raw_value[-1] and raw_value[0] in ('"', "'"):
            value = raw_value[1:-1]
        else:
            value = raw_value
        if override or key not in os.environ or not os.environ[key]:
            os.environ[key] = value
    return p


# ── Username parsing ────────────────────────────────────────────────────


def _split_domain_user(username: str, explicit_domain: str) -> tuple[str, str]:
    """Accept `DOMAIN\\user`, `user@domain`, or bare `user`.

    Explicit domain (from env) wins if provided.
    """
    if explicit_domain:
        return explicit_domain, username
    if "\\" in username:
        d, u = username.split("\\", 1)
        return d, u
    # We do NOT split user@domain → that's a UPN; NTLM with a UPN works fine
    # and many on-prem servers prefer it. Leave it intact, domain empty.
    return "", username


# ── Profile + credential resolution ─────────────────────────────────────


@dataclass
class ResolvedCredentials:
    profile: ConnectionProfile
    password: str


def profile_from_env(name: str = "env") -> ConnectionProfile:
    """Build a ConnectionProfile from environment variables (or `.env`)."""
    load_dotenv()

    url = _env(ENV_URL).strip()
    if not url:
        raise D365Error(
            f"Environment variable {ENV_URL} (or alias CRM_BASE_URL) is not set. "
            "Required: D365_URL/CRM_BASE_URL=https://<host>/<org>. Then for NTLM "
            "set D365_USERNAME/D365_PASSWORD; for OAuth set D365_AUTH=oauth with "
            "D365_TENANT_ID/D365_CLIENT_ID/D365_CLIENT_SECRET."
        )

    auth = _env(ENV_AUTH, "ntlm").strip().lower()
    verify_ssl = _env(ENV_VERIFY_SSL, "1").strip().lower() not in (
        "0", "false", "no", "off",
    )
    api_version = _env(ENV_API_VERSION, "v9.2").strip() or "v9.2"

    if auth == "oauth":
        # Cloud / Dataverse online: client-credentials. No on-prem
        # username/password/domain — those are not required in this mode.
        tenant_id = _env(ENV_TENANT_ID).strip()
        if not tenant_id:
            raise D365Error(f"Environment variable {ENV_TENANT_ID} (or alias CRM_TENANT_ID) is not set.")
        client_id = _env(ENV_CLIENT_ID).strip()
        if not client_id:
            raise D365Error(f"Environment variable {ENV_CLIENT_ID} (or alias CRM_CLIENT_ID) is not set.")
        return ConnectionProfile(
            name=name,
            url=url,
            domain="",
            username="",
            api_version=api_version,
            verify_ssl=verify_ssl,
            auth_scheme="oauth",
            tenant_id=tenant_id,
            client_id=client_id,
        )

    if auth != "ntlm":
        raise D365Error(
            f"Only auth=ntlm or auth=oauth is supported via environment "
            f"(got {auth!r}). Set D365_AUTH=ntlm or D365_AUTH=oauth "
            "(or the CRM_AUTH alias)."
        )

    raw_user = _env(ENV_USERNAME).strip()
    if not raw_user:
        raise D365Error(f"Environment variable {ENV_USERNAME} (or alias CRM_USERNAME) is not set.")

    explicit_domain = _env(ENV_DOMAIN).strip()
    domain, username = _split_domain_user(raw_user, explicit_domain)

    return ConnectionProfile(
        name=name,
        url=url,
        domain=domain,
        username=username,
        api_version=api_version,
        verify_ssl=verify_ssl,
    )


def resolve_credentials(
    profile_name: str | None = None,
    password_override: str | None = None,
) -> ResolvedCredentials:
    """Resolve a ConnectionProfile + password.

    Resolution order:
    1. If profile_name is given, load it from disk; password from env or override.
    2. Else build a profile entirely from env (and .env autoload).

    The "password" is scheme-dependent: the NTLM password for ntlm, or the
    OAuth client secret (D365_CLIENT_SECRET) for oauth. Either way it is the
    one secret the backend needs and is never stored on the profile.
    """
    load_dotenv()
    if profile_name:
        profile = session_mod.load_profile(profile_name)
    else:
        profile = profile_from_env()

    if profile.auth_scheme == "oauth":
        secret = password_override or _env(ENV_CLIENT_SECRET)
        if not secret:
            raise D365Error(
                f"No client secret supplied. Set {ENV_CLIENT_SECRET} "
                "(or CRM_CLIENT_SECRET) in the environment, in a .env file, "
                "or pass --password."
            )
        return ResolvedCredentials(profile=profile, password=secret)

    password = password_override or _env(ENV_PASSWORD)
    if not password:
        raise D365Error(
            f"No password supplied. Set {ENV_PASSWORD} (or CRM_PASSWORD) "
            "in the environment, in a .env file, or pass --password."
        )

    return ResolvedCredentials(profile=profile, password=password)


# ── Live probes ─────────────────────────────────────────────────────────

# api_version negotiation (issue #51): the optimistic default is v9.2, which
# cloud/Dataverse accepts. D365 CE on-premises v9.x caps at v9.1 and returns
# HTTP 501 for /api/data/v9.2/, so a probe at the default downgrades one step.
DEFAULT_API_VERSION = "v9.2"  # public: also the omitted-flag default in connect
_ONPREM_API_VERSION = "v9.1"
_VERSION_UNSUPPORTED_STATUS = 501


def whoami(backend: D365Backend) -> dict[str, Any]:
    """Call WhoAmI() — the canonical D365 identity probe."""
    from crm.utils.d365_backend import as_dict
    return as_dict(backend.get("WhoAmI"))


def test_connection(backend: D365Backend, *, negotiate: bool = False) -> dict[str, Any]:
    """Lightweight reachability test: WhoAmI + report API base.

    When ``negotiate`` is True and the backend is on the optimistic default
    api_version (``v9.2``), a 501 from the server (on-prem caps at v9.1) triggers
    one downgrade to v9.1 and a single re-probe. The backend's api_version is
    mutated in place so the caller can persist the negotiated value (and so the
    in-memory profile is correct for the rest of an env-derived run). If the
    re-probe also fails, the original 501 is surfaced unchanged.

    ``negotiate`` is left False for an explicitly-supplied version, which is then
    respected as-is and never auto-downgraded — even if it 501s.
    """
    try:
        info = whoami(backend)
    except D365Error as exc:
        if not (
            negotiate
            and exc.status == _VERSION_UNSUPPORTED_STATUS
            and backend.profile.api_version == DEFAULT_API_VERSION
        ):
            raise
        backend.profile.api_version = _ONPREM_API_VERSION
        try:
            info = whoami(backend)
        except D365Error:
            backend.profile.api_version = DEFAULT_API_VERSION
            # `from None`: surface the original 501 without chaining the v9.1
            # failure as "During handling of the above exception...".
            raise exc from None
    return {
        "ok": True,
        "user_id": info.get("UserId"),
        "business_unit_id": info.get("BusinessUnitId"),
        "organization_id": info.get("OrganizationId"),
        "api_base": backend.profile.api_base,
        "api_version": backend.profile.api_version,
    }


# ── connection doctor ────────────────────────────────────────────────────

# Renegotiate hint reused for a 404/501 on the configured api_version.
_RENEGOTIATE_HINT = (
    "on-prem caps at v9.1 — re-run `crm connection connect` without --api-version "
    "to auto-negotiate (tries v9.2, downgrades to v9.1)"
)

# Rate-limit headers Dataverse may emit; any header starting with the prefix
# below is also surfaced. Retry-After is the generic throttling signal.
_RATELIMIT_PREFIX = "x-ms-ratelimit"

# Canonical check order — defined in exactly ONE place. The four diagnostic
# checks (their AND is the overall ``ok``) come first, then the informational
# rate_limit step, which is never a diagnostic.
_DIAGNOSTIC_CHECKS = ("dns_tcp", "tls", "version", "auth")
_RATE_LIMIT_CHECK = "rate_limit"
_CHECK_ORDER = (*_DIAGNOSTIC_CHECKS, _RATE_LIMIT_CHECK)


def _check(check: str, ok: bool, detail: str, hint: str = "") -> dict[str, Any]:
    return {"check": check, "ok": ok, "detail": detail, "hint": hint}


def _abort(completed: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    """Build a failed checklist from the checks already run + filler for the rest.

    ``completed`` is the ordered prefix of diagnostic checks that actually ran
    (e.g. ``[dns_tcp]`` when TLS short-circuits). Every remaining check (by the
    canonical ``_CHECK_ORDER``) is filled: missing diagnostics get a failed
    ``not checked ({reason})`` entry; the informational ``rate_limit`` step gets
    an ok=True "not checked". Overall ``ok`` is always False.

    This collapses the short-circuit envelopes to one place so the canonical
    check order lives in exactly ``_CHECK_ORDER``.
    """
    done = {c["check"]: c for c in completed}
    checks: list[dict[str, Any]] = []
    for name in _CHECK_ORDER:
        if name in done:
            checks.append(done[name])
        elif name == _RATE_LIMIT_CHECK:
            checks.append(_check(name, True, "not checked"))
        else:
            checks.append(_check(name, False, f"not checked ({reason})"))
    return {"ok": False, "checks": checks}


def connection_doctor(backend: D365Backend) -> dict[str, Any]:
    """Run an ordered, non-fatal probe chain and return a structured checklist.

    The engine behind `crm connection doctor`. Issues RAW session GETs
    (bypassing ``D365Backend.request()``'s retry-and-wrap path, which collapses
    every transport failure into one generic error) so each layer's failure —
    DNS, TCP, TLS, api_version, auth — is classified distinctly.

    Returns ``{"ok": bool, "checks": [{"check", "ok", "detail", "hint"}, ...]}``.
    The checklist always contains all five checks in a fixed order. Overall
    ``ok`` is the AND of the four diagnostic checks (dns_tcp, tls, version,
    auth); ``rate_limit`` is informational and never affects it.

    Works regardless of ``backend.dry_run`` (raw session GETs ignore dry_run),
    which is the correct behaviour for a diagnostic.
    """
    profile = backend.profile
    parsed = urllib.parse.urlparse(profile.url)
    host = parsed.hostname or ""
    is_https = parsed.scheme == "https"

    # Validate the URL up front so a malformed profile yields a structured
    # dns_tcp failure instead of probing localhost (host == "") or crashing on
    # urlparse(...).port (raises ValueError on a non-numeric :port).
    if not host:
        return _abort(
            [
                _check(
                    "dns_tcp", False, f"profile URL has no hostname: {profile.url!r}",
                    "set a valid D365_URL / CRM_BASE_URL, e.g. https://host/org",
                )
            ],
            "invalid profile URL",
        )
    try:
        port = parsed.port or (443 if is_https else 80)
    except ValueError:
        return _abort(
            [
                _check(
                    "dns_tcp", False, f"invalid port in profile URL: {profile.url!r}",
                    "fix the :port in D365_URL / CRM_BASE_URL",
                )
            ],
            "invalid profile URL",
        )

    # collected so the rate_limit step can inspect them
    seen_headers: list[Any] = []

    # ── step 1: dns_tcp ──────────────────────────────────────────────────
    try:
        sock = socket.create_connection((host, port), timeout=profile.timeout)
        sock.close()
        dns_tcp = _check(
            "dns_tcp", True, f"TCP connection to {host}:{port} succeeded"
        )
    except socket.gaierror:
        return _abort(
            [
                _check(
                    "dns_tcp", False, f"DNS resolution failed for {host}",
                    "check the hostname spelling, your VPN connection, and DNS resolution",
                )
            ],
            "network unreachable",
        )
    except (OSError, TimeoutError) as exc:
        return _abort(
            [
                _check(
                    "dns_tcp", False, f"cannot reach {host}:{port}: {exc}",
                    "check the port, firewall rules, and that the server is up",
                )
            ],
            "network unreachable",
        )

    # ── step 2: tls ──────────────────────────────────────────────────────
    if not is_https:
        tls = _check("tls", True, "not applicable (plain http)")
    else:
        # Always issue the GET, even when verify_ssl is False, so a genuinely
        # broken handshake/connection is caught (a disabled verify only skips
        # the SSLError cert path; ConnectionError/Timeout still fire).
        try:
            # share the client's standard OData headers — see _doctor_version.
            resp = backend.session.get(profile.api_base, headers=DEFAULT_HEADERS, timeout=profile.timeout)  # pyright: ignore[reportUnknownMemberType]
            seen_headers.append(resp.headers)
            detail = (
                "TLS handshake OK"
                if profile.verify_ssl
                else "TLS handshake OK (verification disabled)"
            )
            tls = _check("tls", True, detail)
        except requests.exceptions.SSLError as exc:
            return _abort(
                [
                    dns_tcp,
                    _check(
                        "tls", False, f"TLS certificate not trusted: {exc}",
                        "certificate not trusted — pass --no-verify-ssl to skip "
                        "verification, or install the server's CA certificate",
                    ),
                ],
                "TLS/connection failed",
            )
        except requests.exceptions.Timeout as exc:
            return _abort(
                [
                    dns_tcp,
                    _check(
                        "tls", False, f"timed out establishing HTTPS connection: {exc}",
                        "the server accepted the TCP connection but did not complete "
                        "the HTTPS handshake in time — check server load, a proxy, or "
                        "raise the timeout",
                    ),
                ],
                "TLS/connection failed",
            )
        except requests.exceptions.ConnectionError as exc:
            return _abort(
                [
                    dns_tcp,
                    _check(
                        "tls", False, f"cannot establish HTTPS connection: {exc}",
                        "check that the server is reachable over HTTPS on this port",
                    ),
                ],
                "TLS/connection failed",
            )
        except requests.exceptions.RequestException as exc:
            return _abort(
                [
                    dns_tcp,
                    _check(
                        "tls", False, f"HTTPS request to api_base failed: {exc}",
                        "check that the server is reachable over HTTPS on this port",
                    ),
                ],
                "TLS/connection failed",
            )

    # ── step 3: version ──────────────────────────────────────────────────
    # Validate only the CONFIGURED api_version — no sweep across v9.0/v9.1/v9.2.
    version = _doctor_version(backend, seen_headers)

    # ── step 4: auth ─────────────────────────────────────────────────────
    # version failing (wrong api_version) does NOT block auth.
    auth = _doctor_auth(backend, seen_headers)

    # ── step 5: rate_limit (informational) ───────────────────────────────
    rate_limit = _doctor_rate_limit(seen_headers)

    diagnostics = [dns_tcp, tls, version, auth]
    return {
        "ok": all(c["ok"] for c in diagnostics),
        "checks": [dns_tcp, tls, version, auth, rate_limit],
    }


def _doctor_version(backend: D365Backend, seen_headers: list[Any]) -> dict[str, Any]:
    try:
        # share the client's standard OData headers so the diagnostic stays
        # faithful to a real request (some Dataverse endpoints 406/415 / return
        # non-JSON without them).
        resp = backend.session.get(  # pyright: ignore[reportUnknownMemberType]
            backend.url_for("RetrieveVersion()"),
            headers=DEFAULT_HEADERS,
            timeout=backend.profile.timeout,
        )
    except requests.exceptions.RequestException as exc:
        # Connection dropped mid-probe (server reset/restart, read timeout)
        # AFTER TLS passed — classify as a transport failure, do not crash.
        return _check(
            "version", False, f"RetrieveVersion request failed: {exc}",
            "the connection dropped after TLS — the server may have reset or "
            "restarted; retry, and check server stability / read timeouts",
        )
    seen_headers.append(resp.headers)
    status = resp.status_code
    if 200 <= status < 300:
        try:
            version = resp.json().get("Version", "?")
        except ValueError:
            version = "?"
        return _check("version", True, f"server version {version}")
    if status in (404, _VERSION_UNSUPPORTED_STATUS):
        return _check(
            "version", False,
            f"api_version '{backend.profile.api_version}' not served (HTTP {status})",
            _RENEGOTIATE_HINT,
        )
    return _check("version", False, f"unexpected HTTP {status} from RetrieveVersion")


def _doctor_auth(backend: D365Backend, seen_headers: list[Any]) -> dict[str, Any]:
    try:
        # share the client's standard OData headers (see _doctor_version).
        resp = backend.session.get(  # pyright: ignore[reportUnknownMemberType]
            backend.url_for("WhoAmI"),
            headers=DEFAULT_HEADERS,
            timeout=backend.profile.timeout,
        )
    except requests.exceptions.RequestException as exc:
        # Connection dropped mid-probe AFTER TLS passed — transport failure,
        # not an auth rejection; do not crash the probe.
        return _check(
            "auth", False, f"WhoAmI request failed: {exc}",
            "the connection dropped after TLS — the server may have reset or "
            "restarted; retry, and check server stability / read timeouts",
        )
    seen_headers.append(resp.headers)
    status = resp.status_code
    if status == 200:
        try:
            user_id = resp.json().get("UserId", "?")
        except ValueError:
            user_id = "?"
        return _check("auth", True, f"authenticated as {user_id}")
    if status == 401:
        return _check(
            "auth", False, "authentication failed (HTTP 401)",
            "check credentials — NTLM needs DOMAIN\\username + D365_PASSWORD "
            "(alias CRM_PASSWORD); OAuth needs D365_CLIENT_ID / D365_CLIENT_SECRET "
            "/ D365_TENANT_ID",
        )
    if status == 403:
        return _check(
            "auth", False, "forbidden (HTTP 403)",
            "authenticated but the user lacks privileges / has no security role",
        )
    return _check("auth", False, f"unexpected HTTP {status} from WhoAmI")


def _doctor_rate_limit(seen_headers: list[Any]) -> dict[str, Any]:
    found: list[str] = []
    for headers in seen_headers:
        for name, value in headers.items():
            lname = name.lower()
            if lname == "retry-after" or lname.startswith(_RATELIMIT_PREFIX):
                entry = f"{name}: {value}"
                if entry not in found:
                    found.append(entry)
    if found:
        return _check("rate_limit", True, "; ".join(found))
    return _check("rate_limit", True, "no rate-limit headers present")
