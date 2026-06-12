"""Shared fixtures and auto-marking for the live e2e suite.

Every test collected under this package is marked `e2e` so the default
`addopts = -m 'not e2e'` filter excludes it. The coverage gate lives OUTSIDE
this package (crm/tests/test_e2e_coverage_gate.py) so it is never marked and
runs in the fast offline CI.
"""
# pyright: basic
from __future__ import annotations

import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        if "/crm/tests/e2e/" in path:
            item.add_marker(pytest.mark.e2e)


import os
import warnings

# Names of the env vars the live_profile fixture reads to SEED a throwaway profile.
# Values are never hardcoded here — they come from the environment at run time.
_E_URL = "D365_URL"
_E_USERNAME = "D365_USERNAME"
_E_PW = "D365_PASSWORD"
_E_CLIENT_ID = "D365_CLIENT_ID"
_E_CLIENT_SECRET = "D365_CLIENT_SECRET"

_REQUIRED = (_E_URL, _E_USERNAME, _E_PW)        # NTLM / on-prem
_REQUIRED_OAUTH = (_E_URL, _E_CLIENT_ID)        # when D365_AUTH=oauth
# Hosts that must never receive a destructive e2e run. Extend per environment.
_PROD_HOST_MARKERS = ("prod", "live", ".crm.dynamics.com")  # gov/online prod patterns


def _e2e_opted_in() -> bool:
    if os.environ.get("D365_E2E", "").strip() != "1":
        return False
    # OAuth/Dataverse authenticates via client_id + secret + tenant, NOT a
    # username — so its opt-in check must not demand D365_USERNAME.
    if os.environ.get("D365_AUTH", "ntlm").lower() == "oauth":
        cred = os.environ.get(_E_PW) or os.environ.get(_E_CLIENT_SECRET)
        return bool(cred) and all(os.environ.get(k) for k in _REQUIRED_OAUTH)
    return all(os.environ.get(k) for k in _REQUIRED)


def _assert_not_production(url: str) -> None:
    host = url.split("//", 1)[-1].split("/", 1)[0].lower()
    allow = os.environ.get("D365_E2E_ALLOW_HOST", "").lower()
    if allow and allow in host:
        return
    for marker in _PROD_HOST_MARKERS:
        if marker in host:
            raise RuntimeError(
                f"Refusing to run destructive e2e against host {host!r} "
                f"(matched {marker!r}). Set D365_E2E_ALLOW_HOST to override."
            )


_LIVE_PROFILE = "e2e"


@pytest.fixture(scope="session", autouse=True)
def live_profile(tmp_path_factory):
    """Seed a throwaway profile from D365_* env under an isolated CRM_HOME and
    activate it. The CLI resolves from THIS profile, never the developer's real
    CRM_HOME. Hard-skips unless D365_E2E=1 and credentials are present."""
    if not _e2e_opted_in():
        pytest.skip("e2e opt-in required: set D365_E2E=1 and D365_URL/USERNAME/PASSWORD")
    from crm.core import session as session_mod
    from crm.utils.d365_backend import ConnectionProfile

    _assert_not_production(os.environ["D365_URL"])
    home = tmp_path_factory.mktemp("e2e-crm")
    saved = dict(os.environ)
    os.environ["CRM_HOME"] = str(home)
    auth = os.environ.get("D365_AUTH", "ntlm").lower()
    secret = os.environ.get(_E_PW) or os.environ.get(_E_CLIENT_SECRET) or ""
    api_version = os.environ.get("D365_API_VERSION") or ("v9.2" if auth == "oauth" else "v9.1")
    profile = ConnectionProfile(
        name=_LIVE_PROFILE,
        url=os.environ["D365_URL"],
        domain="" if auth == "oauth" else os.environ.get("D365_DOMAIN", ""),
        username="" if auth == "oauth" else os.environ.get("D365_USERNAME", ""),
        api_version=api_version,
        auth_scheme=auth,
        tenant_id=os.environ.get("D365_TENANT_ID"),
        client_id=os.environ.get("D365_CLIENT_ID"),
    )
    session_mod.save_profile(profile)
    session_mod.save_profile_secret_plaintext(_LIVE_PROFILE, secret)
    state = session_mod.load_session("default")
    state["active_profile"] = _LIVE_PROFILE
    session_mod.save_session(state, "default")
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture(scope="session")
def backend(live_profile):
    from crm.core.connection import resolve_credentials
    from crm.utils.d365_backend import D365Backend

    resolved = resolve_credentials(_LIVE_PROFILE)
    return D365Backend(resolved.profile, resolved.password, dry_run=False)


import shutil
import subprocess
import sys
import uuid


def _safe_delete(backend, path: str) -> None:
    """Best-effort teardown; never raises so finalizers don't mask test results."""
    try:
        backend.delete(path)
    except Exception:
        pass


def _resolve_cli(name: str = "crm"):
    force = os.environ.get("CRM_FORCE_INSTALLED", "").strip() == "1"
    found = shutil.which(name)
    if found:
        return [found]
    if force:
        raise RuntimeError(f"{name} not found in PATH. Install with: pip install -e .")
    return [sys.executable, "-m", "crm"]


@pytest.fixture(scope="session")
def cli():
    base = _resolve_cli("crm")

    def run(args, check=True, env=None):
        merged = os.environ.copy()
        if env:
            merged.update(env)
        return subprocess.run(
            base + args, capture_output=True, text=True, check=check, env=merged
        )

    return run


@pytest.fixture
def unique():
    """Collision-free suffix for entity/solution names (per-test)."""
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="session")
def target():
    """'cloud' for OAuth profiles, 'onprem' for NTLM — drives capability markers.
    Reads the same env the live_profile fixture seeds from (robust regardless of
    whether D365Backend exposes the profile)."""
    return "cloud" if os.environ.get("D365_AUTH", "ntlm").lower() == "oauth" else "onprem"


@pytest.fixture(autouse=True)
def _enforce_capability(request):
    if not _e2e_opted_in():
        return
    target_val = "cloud" if os.environ.get("D365_AUTH", "ntlm").lower() == "oauth" else "onprem"
    if request.node.get_closest_marker("requires_cloud") and target_val != "cloud":
        pytest.skip("requires a cloud/OAuth target")
    if request.node.get_closest_marker("requires_onprem") and target_val != "onprem":
        pytest.skip("requires an on-prem/NTLM target")


@pytest.fixture(scope="session")
def ephemeral_entity(backend):
    """One uniquely-named custom entity for the whole session — backs attribute/
    relationship/form/ribbon tests. Session scope avoids paying the slow on-prem
    create+publish cycle in every module."""
    import uuid as _uuid
    from crm.core import metadata as meta_mod

    suffix = _uuid.uuid4().hex[:8]
    schema = f"new_E2E{suffix}"
    info = meta_mod.create_entity(backend, schema_name=schema, display_name=f"E2E {suffix}")
    yield info["logical_name"]
    try:
        meta_mod.delete_entity(backend, info["logical_name"])
    except Exception as exc:
        # Surface the leak without masking the test outcome (xfail would
        # silently downgrade a passing test and hide the stranded entity).
        warnings.warn(
            f"e2e cleanup failed for entity {info['logical_name']!r}: {exc}",
            stacklevel=2,
        )


@pytest.fixture(scope="module")
def ephemeral_solution(backend):
    """Throwaway publisher + unmanaged solution for solution-component tests."""
    import uuid as _uuid
    from crm.core import solution as sol_mod

    suffix = _uuid.uuid4().hex[:8]
    prefix = f"e2e{suffix[:4]}"
    pub_name = f"new_e2epub_{suffix}"
    sol_name = f"new_e2esol_{suffix}"
    pub = sol_mod.create_publisher(
        backend, name=pub_name, prefix=prefix,
        option_value_prefix=10000 + (int(suffix, 16) % 90000),
    )
    sol_mod.create_solution(backend, name=sol_name, publisher_unique_name=pub_name)
    yield sol_name
    try:
        sol_mod.uninstall_solution(backend, sol_name, force=True)
        backend.delete(f"publishers({pub['publisherid']})")
    except Exception as exc:
        # Surface the leak without masking the test outcome (see ephemeral_entity).
        warnings.warn(
            f"e2e cleanup failed for solution {sol_name!r}: {exc}",
            stacklevel=2,
        )
