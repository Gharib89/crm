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
        # pytest>=7 exposes item.path (pathlib); item.fspath is deprecated and
        # warns on newer pytest. Prefer path, fall back for older versions.
        raw = getattr(item, "path", None) or item.fspath
        path = str(raw).replace("\\", "/")
        if "/crm/tests/e2e/" in path:
            item.add_marker(pytest.mark.e2e)


import os
import re
import warnings

# Names of the env vars the live_profile fixture reads to SEED a throwaway profile.
# Values are never hardcoded here — they come from the environment at run time.
_E_URL = "D365_URL"
_E_USERNAME = "D365_USERNAME"
_E_PW = "D365_PASSWORD"
_E_CLIENT_ID = "D365_CLIENT_ID"
_E_CLIENT_SECRET = "D365_CLIENT_SECRET"
_E_TENANT_ID = "D365_TENANT_ID"

_REQUIRED = (_E_URL, _E_USERNAME, _E_PW)                    # NTLM / on-prem
# OAuth/Dataverse hard-requires tenant_id + client_id (D365Backend raises
# otherwise) — list it here so a missing value fails fast at opt-in instead
# of crashing the suite mid-run with a less actionable auth error.
_REQUIRED_OAUTH = (_E_URL, _E_CLIENT_ID, _E_TENANT_ID)     # when D365_AUTH=oauth
# Opt-in env var naming an EXISTING profile to source creds + target from. When
# set, the target (cloud/on-prem) is intrinsic to the profile's auth scheme and
# the flat D365_* env set is not consulted (#273). Unset → the D365_* env path.
_E2E_PROFILE_ENV = "D365_E2E_PROFILE"
# Hosts that must never receive a destructive e2e run. Extend per environment.
_PROD_HOST_MARKERS = ("prod", "live")
# Dataverse online prod hosts: <org>.crm.dynamics.com AND regional variants
# <org>.crm4.dynamics.com / .crm2. / .crm11. etc. A plain ".crm.dynamics.com"
# substring misses the numbered regions, so match the family with a regex.
_PROD_HOST_RE = re.compile(r"\.crm\d*\.dynamics\.com")


def _e2e_opted_in() -> bool:
    if os.environ.get("D365_E2E", "").strip() != "1":
        return False
    # Profile-sourced creds: opting in is just naming an existing profile — its
    # secret/target are validated when loaded (a typo'd name must fail loudly at
    # setup, not silently skip the whole suite).
    if os.environ.get(_E2E_PROFILE_ENV, "").strip():
        return True
    # OAuth/Dataverse authenticates via client_id + secret + tenant, NOT a
    # username — so its opt-in check must not demand D365_USERNAME.
    if os.environ.get("D365_AUTH", "ntlm").lower() == "oauth":
        cred = os.environ.get(_E_PW) or os.environ.get(_E_CLIENT_SECRET)
        return bool(cred) and all(os.environ.get(k) for k in _REQUIRED_OAUTH)
    return all(os.environ.get(k) for k in _REQUIRED)


def _assert_not_production(url: str) -> None:
    host = url.split("//", 1)[-1].split("/", 1)[0].lower()
    # Exact host match only — substring matching let a short value like "crm"
    # whitelist many unintended hosts and silently bypass the guard.
    allow = os.environ.get("D365_E2E_ALLOW_HOST", "").lower()
    if allow and allow == host:
        return
    matched = next((m for m in _PROD_HOST_MARKERS if m in host), None)
    if matched is None and _PROD_HOST_RE.search(host):
        matched = "*.crm*.dynamics.com"
    if matched is not None:
        raise RuntimeError(
            f"Refusing to run destructive e2e against host {host!r} "
            f"(matched {matched!r}). Set D365_E2E_ALLOW_HOST to the exact "
            f"host {host!r} to override."
        )


_LIVE_PROFILE = "e2e"


def _resolve_e2e_profile():
    """Resolve the ``(ConnectionProfile, secret)`` to seed as the throwaway e2e
    profile. The returned profile is always named ``_LIVE_PROFILE`` so both cred
    sources converge on the same isolated-home seeding.

    Profile path (``D365_E2E_PROFILE`` set): load that named profile + its secret
    from the developer's REAL ``CRM_HOME`` and rename a copy — the target is
    intrinsic to the profile's auth scheme. MUST run while ``CRM_HOME`` still
    points at the real home (before the fixture switches it to the temp home).
    The load is read-only; every later mutation lands in the isolated home.

    Env path (unset): build the profile from the flat ``D365_*`` env set — the CI
    path, unchanged.
    """
    import dataclasses

    from crm.utils.d365_backend import ConnectionProfile, D365Error

    name = os.environ.get(_E2E_PROFILE_ENV, "").strip()
    if name:
        from crm.core.connection import resolve_credentials

        try:
            resolved = resolve_credentials(name)
        except D365Error as exc:
            raise D365Error(f"{_E2E_PROFILE_ENV}={name!r}: {exc}") from exc
        return dataclasses.replace(resolved.profile, name=_LIVE_PROFILE), resolved.password

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
        tenant_id=os.environ.get(_E_TENANT_ID),
        client_id=os.environ.get(_E_CLIENT_ID),
    )
    return profile, secret


def _is_unreachable(exc: BaseException) -> bool:
    """Classify a failed reachability probe: True iff the host never answered.

    The backend wraps a connection-level failure (DNS / TCP / TLS / timeout) as a
    status-less ``D365Error`` whose message carries the transport-failure prefix.
    Any HTTP response — including 401/403 — sets a status, so it is *reachable*
    and must NOT be masked as "unreachable" (auth/server errors surface normally).
    """
    from crm.utils.d365_backend import _TRANSPORT_FAILURE_PREFIX

    return getattr(exc, "status", None) is None and str(exc).startswith(_TRANSPORT_FAILURE_PREFIX)


# Reachability probe: short timeout so a downed VPN skips fast instead of hanging.
_PROBE_TIMEOUT = 8


def _probe_or_skip(profile, secret) -> None:
    """One short-timeout GET to the service root. A connection-level failure
    (DNS/TCP/timeout — host unreachable) skips the whole session naming the
    likely cause; any HTTP response (incl 401/403) means the host is reachable,
    so we proceed and let the tests surface auth/server errors normally (#273)."""
    import dataclasses

    from crm.utils.d365_backend import D365Backend, D365Error

    # retry_max=0 → a single shot, so a downed host skips fast instead of paying
    # the full retry/backoff budget. Set via the public profile field rather than
    # the backend's private retry attr.
    probe = D365Backend(dataclasses.replace(profile, retry_max=0), secret, dry_run=False)
    try:
        probe.get("", timeout=_PROBE_TIMEOUT)
    except D365Error as exc:
        # backend.get wraps every transport failure as a D365Error. We skip ONLY on
        # an unreachable host; a reachable-but-failing target (auth/server error) is
        # intentionally left unhandled here — the same GET from the `backend`
        # fixture will raise it again, so the tests surface it with full context.
        if _is_unreachable(exc):
            pytest.skip(
                f"target {profile.url!r} unreachable (VPN down / host not "
                f"responding?) — {exc}"
            )


@pytest.fixture(scope="session", autouse=True)
def live_profile(tmp_path_factory):
    """Seed a throwaway profile under an isolated CRM_HOME and activate it. Creds
    come from either an existing named profile (D365_E2E_PROFILE) read from the
    developer's REAL home, or the flat D365_* env set; the CLI then resolves from
    the THROWAWAY profile only, never the real home. Hard-skips unless opted in,
    and skips the whole session if the resolved target is unreachable (#273)."""
    if not _e2e_opted_in():
        pytest.skip(
            "e2e opt-in required: set D365_E2E=1 plus EITHER "
            "D365_E2E_PROFILE=<an existing profile> OR credentials — "
            "NTLM: D365_URL/D365_USERNAME/D365_PASSWORD; "
            "OAuth: D365_AUTH=oauth + D365_URL/D365_CLIENT_ID/D365_TENANT_ID "
            "+ D365_CLIENT_SECRET"
        )
    from crm.core import session as session_mod

    # Resolve creds while CRM_HOME still points at the real home (the profile
    # path reads it read-only); the env path is home-independent.
    profile, secret = _resolve_e2e_profile()
    _assert_not_production(profile.url)  # resolved URL, profile or env

    home = tmp_path_factory.mktemp("e2e-crm")
    saved = dict(os.environ)
    os.environ["CRM_HOME"] = str(home)
    # Probe AFTER the switch so any OAuth token cache lands in the temp home.
    _probe_or_skip(profile, secret)
    session_mod.save_profile(profile)  # profile.name is already _LIVE_PROFILE
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
def target(live_profile):
    """'cloud' for an OAuth target, 'onprem' for NTLM — drives capability markers
    and the in-test branching used by divergent (`both`) tests. Derived from the
    resolved profile's auth scheme, so it is correct whether creds came from a
    named profile (D365_E2E_PROFILE) or the D365_* env set."""
    from crm.core import session as session_mod

    return "cloud" if session_mod.load_profile(_LIVE_PROFILE).auth_scheme == "oauth" else "onprem"


@pytest.fixture(autouse=True)
def _enforce_capability(request):
    if not _e2e_opted_in():
        return
    target_val = request.getfixturevalue("target")
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


from collections import namedtuple
from pathlib import Path

# Identity of the no-op plug-in built from crm/tests/e2e/plugin_src. The type name
# is the C# namespace.class (NoOpPlugin.cs); it is independent of the per-build
# assembly name, so register-step can bind to it regardless of the unique build.
PluginAssemblyBuild = namedtuple(
    "PluginAssemblyBuild", ["dll", "public_key_token", "assembly_name", "type_name"]
)
_PLUGIN_SRC = Path(__file__).parent / "plugin_src"
_PLUGIN_TYPE_NAME = "CrmCli.NoOpPlugin"


@pytest.fixture(scope="session")
def plugin_assembly(tmp_path_factory):
    """Build the signed no-op IPlugin from crm/tests/e2e/plugin_src via
    `dotnet build` and yield its identity for the assembly-lifecycle test.

    Skips with instructions when the .NET SDK is absent (the suite's
    skip-with-instructions convention), so a local `pytest -m e2e` without dotnet
    stays runnable. A unique per-session assembly name keeps reruns on the shared
    CI org collision-free. The strong-name public key token is read back from the
    built assembly (emitted by the csproj), so it always matches the uploaded
    content that the cloud sandbox validates.
    """
    if shutil.which("dotnet") is None:
        pytest.skip(
            "dotnet SDK not found — the plugin lifecycle e2e builds a signed "
            "net462 IPlugin from crm/tests/e2e/plugin_src via `dotnet build`. "
            "Install the .NET SDK (https://dotnet.microsoft.com/download) to run it."
        )
    asm_name = f"CrmCliNoOp{uuid.uuid4().hex[:8]}"
    out_dir = tmp_path_factory.mktemp("plugin_build")
    proc = subprocess.run(
        ["dotnet", "build", str(_PLUGIN_SRC / "NoOpPlugin.csproj"),
         "-c", "Release", f"-p:AssemblyName={asm_name}", "-o", str(out_dir)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        # In CI (the authoritative run) the SDK + network are present, so a build
        # failure is a genuine defect — fail loudly rather than masking it as a
        # skip. (dotnet-absent already skipped above.)
        pytest.fail(
            "dotnet build of the e2e plug-in assembly failed (NuGet restore needs "
            f"network access):\n{proc.stdout}\n{proc.stderr}"
        )
    dll = out_dir / f"{asm_name}.dll"
    identity = out_dir / "assembly-identity.txt"
    token = identity.read_text().strip().lower() if identity.is_file() else ""
    if not re.fullmatch(r"[0-9a-f]{16}", token):
        pytest.fail(
            f"could not read the assembly public key token from {identity} "
            f"(got {token!r}); the csproj EmitAssemblyIdentity target may not "
            "have run."
        )
    yield PluginAssemblyBuild(
        dll=str(dll),
        public_key_token=token,
        assembly_name=asm_name,
        type_name=_PLUGIN_TYPE_NAME,
    )
