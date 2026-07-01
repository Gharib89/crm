"""High-level connection management: resolve a profile's secret, store secrets,
test reachability, and run the connection doctor.

Credentials and connection config come ONLY from a saved profile (under
``~/.crm/profiles``) or an explicit ``--password``. There is no ``.env`` autoload
and no ``D365_*`` / ``CRM_*`` environment-variable reading — run ``crm profile add``
once to configure a profile.
"""

from __future__ import annotations

import socket
import urllib.parse
from dataclasses import dataclass
from typing import Any

# requests is imported lazily inside the doctor probes (the only functions here
# that touch the wire) so importing this module never imports the transport
# stack — `crm profile`/`connection` command modules import it for D365Error,
# DEFAULT_HEADERS and credential resolution, none of which need requests (#247).
from crm.utils.d365_backend import (
    ConnectionProfile,
    D365Backend,
    D365Error,
    # Reuse the client's canonical OData headers so the raw doctor probes stay
    # faithful to a real request.
    DEFAULT_HEADERS,
)
from crm.core import session as session_mod
from crm.core import keyring_store


# ── Profile + credential resolution ─────────────────────────────────────


@dataclass
class ResolvedCredentials:
    profile: ConnectionProfile
    password: str


def resolve_credentials(
    profile_name: str | None = None,
    password_override: str | None = None,
    *,
    allow_prompt: bool = False,
) -> ResolvedCredentials:
    """Resolve a saved ConnectionProfile + the one secret its scheme needs.

    Secret order: ``password_override`` → on-disk store (plaintext ``_secret``,
    then OS keyring) → TTY prompt (only when ``allow_prompt``) → raise.

    A profile name is now REQUIRED — there is no env-derived fallback. A None
    name raises, steering the caller to ``crm profile add`` (the CLI turns this
    into an auto-launched wizard on a TTY).
    """
    if not profile_name:
        raise D365Error(
            "No profile configured. Run `crm profile add` to create one, "
            "or pass --profile <name>."
        )
    try:
        profile = session_mod.load_profile(profile_name)
    except FileNotFoundError as exc:
        raise D365Error(f"Profile {profile_name!r} not found.") from exc

    secret = password_override
    if not secret:
        secret = session_mod.load_profile_secret(profile_name)
    if not secret:
        secret = keyring_store.get_secret(profile_name)
    if not secret and allow_prompt:
        import getpass
        is_oauth = profile.auth_scheme == "oauth"
        label = "client secret" if is_oauth else "password"
        secret = getpass.getpass(
            f"D365 {label} for profile {profile.name!r}: "
        ) or None
    if not secret:
        is_oauth = profile.auth_scheme == "oauth"
        label = "client secret" if is_oauth else "password"
        raise D365Error(
            f"No {label} stored for profile {profile_name!r}. "
            f"Run `crm profile set-password --profile {profile_name}`."
        )
    return ResolvedCredentials(profile=profile, password=secret)


def save_secret(
    profile_name: str, secret: str, *, force_plaintext: bool = False,
) -> str:
    """Persist *secret* for an existing profile and return the store used
    ('keyring' | 'plaintext'). Always saves: tries the OS keyring first, then
    falls back to a 0600 plaintext ``_secret`` in the profile file when the
    keyring is unavailable (typical WSL/headless) or ``force_plaintext`` is set.
    Maintains the single-store invariant by clearing the other store."""
    if not force_plaintext and keyring_store.is_available():
        keyring_store.set_secret(profile_name, secret)
        session_mod.clear_profile_secret(profile_name)
        return "keyring"
    session_mod.save_profile_secret_plaintext(profile_name, secret)
    keyring_store.delete_secret(profile_name)
    return "plaintext"


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


def _org_friendly_name(backend: D365Backend, org_id: str | None) -> str | None:
    """Friendly name of the current org, or None when unavailable.

    The Organization table's primary-name attribute is `name` (set at install).
    Best-effort: any read failure yields None rather than masking the caller's
    identity probe. Kept out of the lightweight `whoami()` so the reachability
    probe (`connection test`/`doctor`) never pays this extra round-trip.
    """
    if not org_id:
        return None
    from crm.utils.d365_backend import as_dict
    try:
        rec = as_dict(backend.get(f"organizations({org_id})?$select=name"))
    except D365Error:
        return None
    return rec.get("name")


def whoami_identity(backend: D365Backend) -> dict[str, Any]:
    """WhoAmI plus the connection identity that served it (#624).

    Layers onto the raw WhoAmI GUIDs the resolved profile name and Web API base
    (already on the backend) and the org friendly name (one extra read). For the
    diagnostic `connection whoami` verb only — answers "which org am I hitting?"
    from the output alone, without eyeball-matching OrganizationId GUIDs.
    """
    info = whoami(backend)
    info["profile"] = backend.profile.name
    info["url"] = backend.profile.api_base
    info["org_name"] = _org_friendly_name(backend, info.get("OrganizationId"))
    return info


def test_connection(backend: D365Backend, *, negotiate: bool = False) -> dict[str, Any]:
    """Lightweight reachability test: WhoAmI + report API base.

    When ``negotiate`` is True and the backend is on the optimistic default
    api_version (``v9.2``), a 501 from the server (on-prem caps at v9.1) triggers
    one downgrade to v9.1 and a single re-probe. The backend's api_version is
    mutated in place so the caller can persist the negotiated value (and so the
    in-memory profile is correct for the rest of the run). If the
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
    "on-prem caps at v9.1 — re-run `crm profile add` without --api-version "
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
    import requests  # deferred transport import (#247)

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
                    "set a valid profile URL with `crm profile edit`, e.g. https://host/org",
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
                    "fix the :port in the profile URL with `crm profile edit`",
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
        except D365Error as exc:
            # OAuth profiles acquire the bearer token inside the auth handler
            # *during* the request, so a token/setup failure raises D365Error
            # here (before any HTTP response) — not a requests exception.
            # Degrade gracefully instead of crashing the non-fatal probe.
            return _abort(
                [
                    dns_tcp,
                    _check(
                        "tls", False, f"could not authenticate the request: {exc}",
                        "for an OAuth profile, check tenant_id/client_id "
                        "(crm profile edit) and re-store the client secret "
                        "(crm profile set-password)",
                    ),
                ],
                "request auth/setup failed",
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
    import requests  # deferred transport import (#247)

    try:
        # share the client's standard OData headers so the diagnostic stays
        # faithful to a real request (some Dataverse endpoints 406/415 / return
        # non-JSON without them).
        resp = backend.session.get(  # pyright: ignore[reportUnknownMemberType]
            backend.url_for("RetrieveVersion()"),
            headers=DEFAULT_HEADERS,
            timeout=backend.profile.timeout,
        )
    except (requests.exceptions.RequestException, D365Error) as exc:
        # A transport failure mid-probe (server reset/restart, read timeout) or a
        # D365Error from the auth handler (e.g. OAuth token expiry) AFTER TLS
        # passed — degrade to a failed check, do not crash the non-fatal probe.
        return _check(
            "version", False, f"RetrieveVersion request failed: {exc}",
            "the request failed after TLS — the server may have reset/restarted "
            "or the credentials/token expired; retry and check server stability",
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
    import requests  # deferred transport import (#247)

    try:
        # share the client's standard OData headers (see _doctor_version).
        resp = backend.session.get(  # pyright: ignore[reportUnknownMemberType]
            backend.url_for("WhoAmI"),
            headers=DEFAULT_HEADERS,
            timeout=backend.profile.timeout,
        )
    except (requests.exceptions.RequestException, D365Error) as exc:
        # A transport failure mid-probe (server reset/read timeout) or a
        # D365Error from the auth handler (e.g. OAuth token expiry) AFTER TLS
        # passed — degrade to a failed check, do not crash the probe.
        return _check(
            "auth", False, f"WhoAmI request failed: {exc}",
            "the request failed after TLS — the server may have reset/restarted "
            "or the credentials/token expired; retry and check server stability",
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
            "check the stored secret — re-store it with "
            f"`crm profile set-password --profile {backend.profile.name}`",
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
