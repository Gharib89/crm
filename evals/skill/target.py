"""Live-target selection — reuses the e2e ``D365_E2E_PROFILE`` mechanism.

The harness re-uses the same opt-in contract as the pytest e2e suite so a maintainer
points it at a target the same way: ``D365_E2E_PROFILE=<name>`` names a profile in
their real ``CRM_HOME``; its creds are read read-only and re-seeded into the
throwaway ``CRM_HOME`` the isolated agent uses, so the real profile store is never
mutated. The ``D365_E2E_ALLOW_HOST`` prod-host guard is honored identically.
"""
from __future__ import annotations

import dataclasses
import os
import re
from pathlib import Path

_E2E_PROFILE_ENV = "D365_E2E_PROFILE"
_ALLOW_HOST_ENV = "D365_E2E_ALLOW_HOST"
_PROD_HOST_MARKERS = ("prod", "live")
_PROD_HOST_RE = re.compile(r"\.crm\d*\.dynamics\.com")


class TargetError(RuntimeError):
    """Raised when no live target is configured or the prod-host guard trips."""


def assert_not_production(url: str) -> None:
    """Refuse a prod/live host unless ``D365_E2E_ALLOW_HOST`` names it exactly.

    Mirrors the e2e conftest guard: cloud orgs are ``*.dynamics.com`` and must be
    opted in by exact host, so a stray run can't mutate production.
    """
    host = url.split("//", 1)[-1].split("/", 1)[0].lower()
    allow = os.environ.get(_ALLOW_HOST_ENV, "").lower()
    if allow and allow == host:
        return
    matched = next((m for m in _PROD_HOST_MARKERS if m in host), None)
    if matched is None and _PROD_HOST_RE.search(host):
        matched = "*.crm*.dynamics.com"
    if matched is not None:
        raise TargetError(
            f"Refusing to run against host {host!r} (matched {matched!r}). "
            f"Set {_ALLOW_HOST_ENV} to the exact host {host!r} to override."
        )


def resolve_profile_name() -> str:
    """The profile name from ``D365_E2E_PROFILE``, or raise with guidance."""
    name = os.environ.get(_E2E_PROFILE_ENV, "").strip()
    if not name:
        raise TargetError(
            f"no live target: set {_E2E_PROFILE_ENV}=<an existing profile> "
            f"(e.g. agent-cloud) and {_ALLOW_HOST_ENV}=<its host> for a cloud org"
        )
    return name


def seed_target(crm_home: Path) -> str:
    """Seed the configured target's creds into ``crm_home`` and activate it.

    Reads the named profile + secret from the real ``CRM_HOME`` (read-only), applies
    the prod-host guard, then writes the profile/secret into the throwaway
    ``crm_home`` under its original name and marks it active — so the agent's verbatim
    ``--profile <name>`` resolves against the isolated home. Returns the profile name.
    """
    from crm.core import session as session_mod
    from crm.core.connection import resolve_credentials
    from crm.utils.d365_backend import D365Error

    name = resolve_profile_name()
    try:
        resolved = resolve_credentials(name)
    except D365Error as exc:
        raise TargetError(f"{_E2E_PROFILE_ENV}={name!r}: {exc}") from exc

    assert_not_production(resolved.profile.url)

    saved = os.environ.get("CRM_HOME")
    os.environ["CRM_HOME"] = str(crm_home)
    try:
        session_mod.save_profile(dataclasses.replace(resolved.profile, name=name))
        session_mod.save_profile_secret_plaintext(name, resolved.password)
        state = session_mod.load_session("default")
        state["active_profile"] = name
        session_mod.save_session(state, "default")
    finally:
        if saved is None:
            os.environ.pop("CRM_HOME", None)
        else:
            os.environ["CRM_HOME"] = saved
    return name
