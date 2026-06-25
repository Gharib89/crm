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

# The prod-host guard below is a small, deliberate copy of the e2e conftest's
# `_assert_not_production` rather than an import: that helper is a private symbol in a
# pytest conftest (test-only collection machinery), and this is a runtime harness —
# importing across that boundary would couple the harness to the test tree. ADR 0015
# calls for the guard to be "honored identically"; the duplication is ~10 lines.
_E2E_PROFILE_ENV = "D365_E2E_PROFILE"
#: Public alias of the profile-selection env var: the both-targets runner (#573) sets it
#: per leg to point the set runner + seed step at each profile in turn.
E2E_PROFILE_ENV = _E2E_PROFILE_ENV
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


def _scheme_target(auth_scheme: str) -> str:
    """Map a profile's auth scheme to the eval target it represents."""
    return "cloud" if auth_scheme == "oauth" else "onprem"


def active_target() -> str:
    """The eval target (``cloud``/``onprem``) the configured profile represents.

    Resolves ``D365_E2E_PROFILE`` and reads its auth scheme **without** seeding or
    mutating anything — the set runner uses this to decide which tasks the active
    target can run (a ``cloud``-gated task is skipped on an on-prem target and vice
    versa) before paying for isolation. Raises :class:`TargetError` when no target is
    configured or the named profile cannot be resolved.
    """
    from crm.core.connection import resolve_credentials
    from crm.utils.d365_backend import D365Error

    name = resolve_profile_name()
    try:
        resolved = resolve_credentials(name)
    except D365Error as exc:
        raise TargetError(f"{_E2E_PROFILE_ENV}={name!r}: {exc}") from exc
    return _scheme_target(resolved.profile.auth_scheme)


# Reachability probe: short timeout so a downed VPN skips fast instead of hanging.
# Mirrors the e2e conftest's `_probe_or_skip`/`_is_unreachable` (a deliberate copy, not
# an import — see the prod-host guard note above): both treat any HTTP response, incl
# 401/403, as *reachable*, and only a status-less transport failure as unreachable.
_PROBE_TIMEOUT = 8


def _is_unreachable(exc: BaseException) -> bool:
    """True iff a failed probe means the host never answered (DNS/TCP/TLS/timeout).

    The backend wraps a connection-level failure as a status-less ``D365Error`` whose
    message carries the transport-failure prefix. Any HTTP response sets a status, so
    it is reachable (its auth/server error surfaces normally), not masked as down.
    """
    from crm.utils.d365_backend import _TRANSPORT_FAILURE_PREFIX

    return getattr(exc, "status", None) is None and str(exc).startswith(_TRANSPORT_FAILURE_PREFIX)


def probe_reachable(name: str | None = None) -> bool:
    """One short-timeout GET to the named profile's service root: is the host up?

    Resolves the profile (``name`` or ``D365_E2E_PROFILE``) read-only and fires a
    single no-retry request. A connection-level failure (host unreachable — VPN down)
    returns ``False`` so the caller can skip that target with a clear message (#573);
    any HTTP response, including auth errors, returns ``True``. Raises
    :class:`TargetError` only when the profile itself cannot be resolved (a config
    problem, not a reachability one).
    """
    import dataclasses

    from crm.core.connection import resolve_credentials
    from crm.utils.d365_backend import D365Backend, D365Error

    profile_name = name or resolve_profile_name()
    try:
        resolved = resolve_credentials(profile_name)
    except D365Error as exc:
        raise TargetError(f"{_E2E_PROFILE_ENV}={profile_name!r}: {exc}") from exc

    # retry_max=0 → a single shot, so a downed host skips fast instead of paying the
    # full retry/backoff budget; set via the public profile field, as the e2e conftest does.
    probe = D365Backend(dataclasses.replace(resolved.profile, retry_max=0), resolved.password, dry_run=False)
    try:
        probe.get("", timeout=_PROBE_TIMEOUT)
    except D365Error as exc:
        return not _is_unreachable(exc)
    return True


def seed_target(crm_home: Path, required_target: str = "either") -> str:
    """Seed the configured target's creds into ``crm_home`` and activate it.

    Reads the named profile + secret from the real ``CRM_HOME`` (read-only), enforces
    the task's ``required_target`` gate (a cloud-only task must not run against an
    on-prem profile, and vice versa), applies the prod-host guard, then writes the
    profile/secret into the throwaway ``crm_home`` under its original name and marks
    it active — so the agent's verbatim ``--profile <name>`` resolves against the
    isolated home. Returns the profile name.
    """
    from crm.core import session as session_mod
    from crm.core.connection import resolve_credentials
    from crm.utils.d365_backend import D365Error

    name = resolve_profile_name()
    try:
        resolved = resolve_credentials(name)
    except D365Error as exc:
        raise TargetError(f"{_E2E_PROFILE_ENV}={name!r}: {exc}") from exc

    actual = _scheme_target(resolved.profile.auth_scheme)
    if required_target != "either" and required_target != actual:
        raise TargetError(
            f"task requires a {required_target!r} target but profile {name!r} is "
            f"{actual!r} ({resolved.profile.auth_scheme}) — point {_E2E_PROFILE_ENV} "
            f"at a {required_target} profile"
        )

    assert_not_production(resolved.profile.url)

    # save_profile/save_session read CRM_HOME from the process env, so the throwaway
    # home must be active in os.environ for the duration of the writes (restored
    # below). Mirrors the e2e conftest seeding; single-process by design.
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
