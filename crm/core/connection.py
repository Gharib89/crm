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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from crm.utils.d365_backend import (
    ConnectionProfile,
    D365Backend,
    D365Error,
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
