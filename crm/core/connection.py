"""High-level connection management: build profiles, resolve creds, test reachability.

Env vars (canonical D365_* and CRM_* aliases for compatibility with existing
Moce-style PowerShell tooling):

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

_ENV_ALIASES = {
    ENV_URL: ("CRM_BASE_URL", "CRM_URL"),
    ENV_DOMAIN: ("CRM_DOMAIN",),
    ENV_USERNAME: ("CRM_USERNAME", "CRM_USER"),
    ENV_PASSWORD: ("CRM_PASSWORD", "CRM_PASS"),
    ENV_API_VERSION: ("CRM_API_VERSION",),
    ENV_VERIFY_SSL: ("CRM_VERIFY_SSL",),
    ENV_AUTH: ("CRM_AUTH",),
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


# ── .env autoload ───────────────────────────────────────────────────────


def load_dotenv(path: str | os.PathLike | None = None, *, override: bool = False) -> Path | None:
    """Load KEY=VALUE pairs from a `.env` file into os.environ.

    Lookup order when path is None:
        1. CRM_DOTENV env var
        2. ./.env in cwd
        3. ../.env (one level up)

    Returns the resolved path actually loaded, or None if no file was found.
    """
    if path is None:
        candidates: list[Path] = []
        env_override = os.environ.get("CRM_DOTENV")
        if env_override:
            candidates.append(Path(env_override))
        cwd = Path.cwd()
        candidates.append(cwd / ".env")
        candidates.append(cwd.parent / ".env")
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
        value = raw.strip().strip('"').strip("'")
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
            "Required: D365_URL/CRM_BASE_URL=https://<host>/<org>, "
            "D365_USERNAME/CRM_USERNAME=<user>, D365_PASSWORD/CRM_PASSWORD=<pw>."
        )
    raw_user = _env(ENV_USERNAME).strip()
    if not raw_user:
        raise D365Error(f"Environment variable {ENV_USERNAME} (or alias CRM_USERNAME) is not set.")

    auth = _env(ENV_AUTH, "ntlm").strip().lower()
    if auth != "ntlm":
        raise D365Error(
            f"Only auth=ntlm is supported in this harness (got {auth!r}). "
            "Set D365_AUTH=ntlm (or CRM_AUTH=ntlm)."
        )

    verify_ssl = _env(ENV_VERIFY_SSL, "1").strip().lower() not in (
        "0", "false", "no", "off",
    )

    explicit_domain = _env(ENV_DOMAIN).strip()
    domain, username = _split_domain_user(raw_user, explicit_domain)

    return ConnectionProfile(
        name=name,
        url=url,
        domain=domain,
        username=username,
        api_version=_env(ENV_API_VERSION, "v9.2").strip() or "v9.2",
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
    """
    load_dotenv()
    if profile_name:
        profile = session_mod.load_profile(profile_name)
    else:
        profile = profile_from_env()

    password = password_override or _env(ENV_PASSWORD)
    if not password:
        raise D365Error(
            f"No password supplied. Set {ENV_PASSWORD} (or CRM_PASSWORD) "
            "in the environment, in a .env file, or pass --password."
        )

    return ResolvedCredentials(profile=profile, password=password)


# ── Live probes ─────────────────────────────────────────────────────────


def whoami(backend: D365Backend) -> dict:
    """Call WhoAmI() — the canonical D365 identity probe."""
    return backend.get("WhoAmI") or {}


def test_connection(backend: D365Backend) -> dict:
    """Lightweight reachability test: WhoAmI + report API base."""
    info = whoami(backend)
    return {
        "ok": True,
        "user_id": info.get("UserId"),
        "business_unit_id": info.get("BusinessUnitId"),
        "organization_id": info.get("OrganizationId"),
        "api_base": backend.profile.api_base,
        "api_version": backend.profile.api_version,
    }
