# Configure-once Credentials via OS Keyring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user store a D365 secret once (OS keyring primary, explicit plaintext-to-profile-file fallback) and omit it on every later command, then graduate the project to v1.0.0.

**Architecture:** A new `crm/core/keyring_store.py` isolates the optional `keyring` dependency behind a small mockable interface. `resolve_credentials` gains an on-disk secret step (keyring or plaintext `_secret`) and an opt-in TTY prompt. `CLIContext.backend()` restores the session's `active_profile` when no `--profile` is given. Plaintext secrets live as a `_secret` key in the profile JSON — kept off the `ConnectionProfile` dataclass so `status`/`profiles` cannot leak it (Approach B). Two mutually-exclusive `connect` flags pick the store; a new `delete-password` command clears both.

**Tech Stack:** Python 3.9+, Click, `keyring` (optional extra), pytest + `requests_mock` + `CliRunner`, pyright strict on `crm/core/*`.

**Spec:** `docs/superpowers/specs/2026-06-07-keyring-credentials-design.md`

---

## File Structure

- **Create** `crm/core/keyring_store.py` — optional-dep wrapper: `is_available`, `get_secret`, `set_secret`, `delete_secret`, `has_secret`. pyright strict.
- **Modify** `crm/core/session.py` — plaintext secret helpers: `save_profile_secret_plaintext`, `load_profile_secret`, `clear_profile_secret`.
- **Modify** `crm/core/connection.py` — `resolve_credentials` gains the on-disk + prompt steps and an `allow_prompt` kwarg.
- **Modify** `crm/cli.py` — `CLIContext.backend()` restores `active_profile` and passes `allow_prompt`.
- **Modify** `crm/commands/connection.py` — `connect` store flags; new `delete-password`; `profiles` storage-type reporting.
- **Modify** `setup.py` — `keyring` optional extra.
- **Modify** `pyproject.toml` — flip `allow_zero_version` to graduate to v1.0.0.
- **Modify** docs — `README.md`, `docs/how-to/connection.md`, `docs/reference/cli.md`, `crm/skills/SKILL.md`.
- **Tests** — `crm/tests/test_keyring_store.py`, `crm/tests/test_plaintext_secret.py`, `crm/tests/test_resolve_credentials_keyring.py`, `crm/tests/test_active_profile_restore.py`, additions to `crm/tests/test_connection_cmd.py`.

**Test isolation note (reused by every command-level test below):** tests use the existing `_isolated_home` autouse fixture pattern from `crm/tests/test_connection_cmd.py` (sets `CRM_HOME` to a tmp dir, `CRM_DOTENV` to a noop path, snapshots/restores `os.environ`). For new test files, copy that fixture verbatim. Unit tests that exercise `keyring_store` directly patch `_import_keyring`; tests of downstream callers patch the `keyring_store` functions with an in-memory dict (shown per task). **No test ever touches a live keyring.**

---

## Task 1: `keyring_store` module (optional-dep wrapper)

**Files:**
- Create: `crm/core/keyring_store.py`
- Test: `crm/tests/test_keyring_store.py`

- [ ] **Step 1: Create the module skeleton so imports resolve (dodges the pytest-collection trap)**

Create `crm/core/keyring_store.py`:

```python
"""Optional OS-keyring credential store (issue #130).

Isolates the optional `keyring` dependency (the `crm[keyring]` extra) behind a
small, mockable interface so the credential resolver stays clean and tests never
touch a live keyring backend. Service name is fixed; the account is the profile
name. The stored secret is scheme-aware at the call site (NTLM password or OAuth
client secret) — this module just stores an opaque string.
"""

from __future__ import annotations

from typing import Any

from crm.utils.d365_backend import D365Error

KEYRING_SERVICE = "crm"

# Backend module name keyring uses when no real backend is available.
_NULL_BACKEND_MODULE = "keyring.backends.fail"


def _import_keyring() -> Any:
    """Lazy-import `keyring`, or raise a graceful, actionable error."""
    raise NotImplementedError


def is_available() -> bool:
    raise NotImplementedError


def get_secret(profile_name: str) -> str | None:
    raise NotImplementedError


def set_secret(profile_name: str, secret: str) -> None:
    raise NotImplementedError


def delete_secret(profile_name: str) -> bool:
    raise NotImplementedError


def has_secret(profile_name: str) -> bool:
    raise NotImplementedError
```

- [ ] **Step 2: Write the failing tests**

Create `crm/tests/test_keyring_store.py`:

```python
"""Unit tests for the optional keyring wrapper (issue #130)."""
# pyright: basic
from __future__ import annotations

import pytest

from crm.core import keyring_store
from crm.utils.d365_backend import D365Error


class _FakeKeyring:
    """In-memory stand-in for the `keyring` module's password API."""
    def __init__(self):
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service, name):
        return self.store.get((service, name))

    def set_password(self, service, name, secret):
        self.store[(service, name)] = secret

    def delete_password(self, service, name):
        del self.store[(service, name)]

    def get_keyring(self):
        return self  # __class__.__module__ != the null-backend module → "usable"


@pytest.fixture
def fake(monkeypatch):
    kr = _FakeKeyring()
    monkeypatch.setattr(keyring_store, "_import_keyring", lambda: kr)
    return kr


def test_set_then_get_roundtrips(fake):
    keyring_store.set_secret("prod", "s3cret")
    assert keyring_store.get_secret("prod") == "s3cret"


def test_get_missing_returns_none(fake):
    assert keyring_store.get_secret("nope") is None


def test_has_secret_true_false(fake):
    assert keyring_store.has_secret("prod") is False
    keyring_store.set_secret("prod", "x")
    assert keyring_store.has_secret("prod") is True


def test_delete_existing_returns_true(fake):
    keyring_store.set_secret("prod", "x")
    assert keyring_store.delete_secret("prod") is True
    assert keyring_store.get_secret("prod") is None


def test_delete_missing_returns_false(fake):
    assert keyring_store.delete_secret("nope") is False


def test_is_available_true_when_backend_usable(fake):
    assert keyring_store.is_available() is True


def test_unavailable_when_keyring_missing(monkeypatch):
    def _raise():
        raise D365Error("not installed")
    monkeypatch.setattr(keyring_store, "_import_keyring", _raise)
    assert keyring_store.is_available() is False
    assert keyring_store.has_secret("prod") is False
    assert keyring_store.get_secret("prod") is None      # soft: resolver source
    assert keyring_store.delete_secret("prod") is False  # soft: nothing to delete
    with pytest.raises(D365Error):
        keyring_store.set_secret("prod", "x")            # hard: explicit intent
```

- [ ] **Step 3: Run the tests — expect failure**

Run: `pytest crm/tests/test_keyring_store.py -q`
Expected: FAIL (every function raises `NotImplementedError`).

- [ ] **Step 4: Implement the module body**

Replace the skeleton function bodies in `crm/core/keyring_store.py`:

```python
def _import_keyring() -> Any:
    try:
        import keyring
    except ImportError as exc:
        raise D365Error(
            "The optional 'keyring' dependency is not installed. Install it with "
            "`pip install crm[keyring]`, or store the secret with "
            "--store-password-plaintext, or supply it via D365_PASSWORD / "
            "D365_CLIENT_SECRET (env or .env)."
        ) from exc
    return keyring


def is_available() -> bool:
    """True only when keyring is importable AND a real backend is configured."""
    try:
        kr = _import_keyring()
    except D365Error:
        return False
    try:
        backend = kr.get_keyring()
    except Exception:
        return False
    return type(backend).__module__ != _NULL_BACKEND_MODULE


def get_secret(profile_name: str) -> str | None:
    """Read the stored secret, or None. Soft on a missing keyring (returns None)
    so the resolver can treat keyring as just one optional source."""
    if not is_available():
        return None
    return _import_keyring().get_password(KEYRING_SERVICE, profile_name)


def set_secret(profile_name: str, secret: str) -> None:
    """Store the secret. Hard error if keyring is unavailable — the caller asked
    for keyring storage explicitly (--store-password)."""
    if not is_available():
        # _import_keyring raises the actionable message; if it imported but has
        # no backend, raise the same guidance here.
        _import_keyring()
        raise D365Error(
            "No usable OS keyring backend is available. Use "
            "--store-password-plaintext, or env vars, instead."
        )
    _import_keyring().set_password(KEYRING_SERVICE, profile_name, secret)


def delete_secret(profile_name: str) -> bool:
    """Remove the stored secret. Returns True iff an entry existed. Soft on a
    missing keyring (returns False — nothing to delete)."""
    if not is_available():
        return False
    kr = _import_keyring()
    if kr.get_password(KEYRING_SERVICE, profile_name) is None:
        return False
    kr.delete_password(KEYRING_SERVICE, profile_name)
    return True


def has_secret(profile_name: str) -> bool:
    return get_secret(profile_name) is not None
```

- [ ] **Step 5: Run the tests — expect pass**

Run: `pytest crm/tests/test_keyring_store.py -q`
Expected: PASS (8 tests).

- [ ] **Step 6: Commit**

```bash
git add crm/core/keyring_store.py crm/tests/test_keyring_store.py
git commit -m "feat(connection): add optional keyring credential store wrapper (#130)"
```

---

## Task 2: Plaintext secret helpers in `session.py`

**Files:**
- Modify: `crm/core/session.py`
- Test: `crm/tests/test_plaintext_secret.py`

The plaintext secret is a `_secret` key merged into the existing profile JSON. It is read/written directly here, never via the `ConnectionProfile` dataclass — so the dataclass stays secret-free and `status`/`profiles` cannot echo it.

- [ ] **Step 1: Write the failing tests**

Create `crm/tests/test_plaintext_secret.py`:

```python
"""Plaintext profile-secret helpers (issue #130, Approach B)."""
# pyright: basic
from __future__ import annotations

import json
import os

import pytest

from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))


def _save_base_profile(name="prod"):
    session_mod.save_profile(ConnectionProfile(
        name=name, url="https://crm.contoso.local/c", domain="C", username="alice",
    ))


def test_load_secret_none_when_absent():
    _save_base_profile()
    assert session_mod.load_profile_secret("prod") is None


def test_save_then_load_roundtrips():
    _save_base_profile()
    session_mod.save_profile_secret_plaintext("prod", "p@ss")
    assert session_mod.load_profile_secret("prod") == "p@ss"


def test_secret_not_in_dataclass_roundtrip():
    # Approach B: the secret lives only as the _secret JSON key, never on the
    # dataclass — so to_dict()/status output can't leak it.
    _save_base_profile()
    session_mod.save_profile_secret_plaintext("prod", "p@ss")
    p = session_mod.load_profile("prod")
    assert "_secret" not in p.to_dict()
    assert "p@ss" not in json.dumps(p.to_dict())


def test_clear_removes_secret():
    _save_base_profile()
    session_mod.save_profile_secret_plaintext("prod", "p@ss")
    assert session_mod.clear_profile_secret("prod") is True
    assert session_mod.load_profile_secret("prod") is None
    # Profile itself survives the clear.
    assert session_mod.load_profile("prod").username == "alice"


def test_clear_noop_returns_false():
    _save_base_profile()
    assert session_mod.clear_profile_secret("prod") is False


@pytest.mark.skipif(os.name != "posix", reason="chmod 0600 only enforced on POSIX")
def test_plaintext_file_is_0600():
    _save_base_profile()
    path = session_mod.save_profile_secret_plaintext("prod", "p@ss")
    assert (path.stat().st_mode & 0o777) == 0o600
```

- [ ] **Step 2: Run the tests — expect failure**

Run: `pytest crm/tests/test_plaintext_secret.py -q`
Expected: FAIL with `AttributeError: module 'crm.core.session' has no attribute 'load_profile_secret'`. (Import of the module succeeds — collection is not broken — because the test imports `session as session_mod` and calls attributes, never `from ... import load_profile_secret`.)

- [ ] **Step 3: Implement the helpers**

In `crm/core/session.py`, add after `delete_profile` (around line 67):

```python
# ── Plaintext profile secret (issue #130, explicit opt-in only) ─────────
#
# Stored as a `_secret` key in the SAME profile JSON file, written/read here
# directly — never via ConnectionProfile.to_dict()/from_dict() — so the
# dataclass (and every status/list view built from it) stays secret-free.


def _read_profile_raw(name: str) -> dict[str, Any]:
    p = profile_path(name)
    if not p.is_file():
        raise FileNotFoundError(f"Profile not found: {name} (looked at {p})")
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_profile_secret_plaintext(name: str, secret: str) -> Path:
    """Merge a plaintext `_secret` into the profile JSON; 0600 on POSIX.

    Windows cannot enforce file-mode perms via chmod — the caller emits the
    warning that steers Windows users to --store-password (Credential Manager).
    """
    data = _read_profile_raw(name)
    data["_secret"] = secret
    p = profile_path(name)
    _atomic_write_json(p, data)
    if os.name == "posix":
        os.chmod(p, 0o600)
    return p


def load_profile_secret(name: str) -> str | None:
    """Return the plaintext `_secret` from the profile file, or None."""
    try:
        return _read_profile_raw(name).get("_secret")
    except FileNotFoundError:
        return None


def clear_profile_secret(name: str) -> bool:
    """Strip `_secret` from the profile file. True iff one was present."""
    try:
        data = _read_profile_raw(name)
    except FileNotFoundError:
        return False
    if "_secret" not in data:
        return False
    del data["_secret"]
    _atomic_write_json(profile_path(name), data)
    return True
```

Note: the existing `save_profile()` writes `profile.to_dict()`, which has no `_secret` — so re-running `connect` without `--store-password-plaintext` deliberately drops any stale on-disk secret. This is intended (don't silently retain an old plaintext secret).

- [ ] **Step 4: Run the tests — expect pass**

Run: `pytest crm/tests/test_plaintext_secret.py -q`
Expected: PASS (6 on POSIX; the 0600 test skips on Windows).

- [ ] **Step 5: Commit**

```bash
git add crm/core/session.py crm/tests/test_plaintext_secret.py
git commit -m "feat(connection): plaintext profile-secret helpers, secret kept off the dataclass (#130)"
```

---

## Task 3: Resolver gains on-disk + prompt steps

**Files:**
- Modify: `crm/core/connection.py:230-270` (`resolve_credentials`)
- Test: `crm/tests/test_resolve_credentials_keyring.py`

New secret chain: `password_override` → env → on-disk (`load_profile_secret` OR `keyring_store.get_secret`) → TTY prompt (opt-in) → raise. The on-disk and prompt steps fire only for a named profile (`profile_name` truthy); env-only mode is unchanged.

- [ ] **Step 1: Write the failing tests**

Create `crm/tests/test_resolve_credentials_keyring.py`:

```python
"""resolve_credentials: keyring / plaintext / prompt steps (issue #130)."""
# pyright: basic
from __future__ import annotations

import os

import pytest

from crm.core import connection as conn_mod
from crm.core import keyring_store
from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile, D365Error


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    monkeypatch.setenv("CRM_DOTENV", str(tmp_path / "noop.env"))
    for k in ("D365_PASSWORD", "CRM_PASSWORD", "D365_CLIENT_SECRET", "CRM_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def fake_keyring(monkeypatch):
    """In-memory keyring_store: patch the funcs the resolver calls."""
    store: dict[str, str] = {}
    monkeypatch.setattr(keyring_store, "is_available", lambda: True)
    monkeypatch.setattr(keyring_store, "get_secret", lambda n: store.get(n))
    return store


def _save(name="prod"):
    session_mod.save_profile(ConnectionProfile(
        name=name, url="https://crm.contoso.local/c", domain="C", username="alice",
    ))


def test_override_beats_everything(fake_keyring, monkeypatch):
    _save()
    fake_keyring["prod"] = "from-keyring"
    monkeypatch.setenv("D365_PASSWORD", "from-env")
    rc = conn_mod.resolve_credentials("prod", password_override="from-flag")
    assert rc.password == "from-flag"


def test_env_beats_keyring(fake_keyring, monkeypatch):
    _save()
    fake_keyring["prod"] = "from-keyring"
    monkeypatch.setenv("D365_PASSWORD", "from-env")
    rc = conn_mod.resolve_credentials("prod")
    assert rc.password == "from-env"


def test_keyring_used_when_no_flag_or_env(fake_keyring):
    _save()
    fake_keyring["prod"] = "from-keyring"
    rc = conn_mod.resolve_credentials("prod")
    assert rc.password == "from-keyring"


def test_plaintext_beats_keyring(fake_keyring):
    _save()
    fake_keyring["prod"] = "from-keyring"
    session_mod.save_profile_secret_plaintext("prod", "from-disk")
    rc = conn_mod.resolve_credentials("prod")
    assert rc.password == "from-disk"


def test_prompt_when_allowed_and_nothing_else(monkeypatch):
    _save()
    monkeypatch.setattr(keyring_store, "is_available", lambda: False)
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: "typed-secret")
    rc = conn_mod.resolve_credentials("prod", allow_prompt=True)
    assert rc.password == "typed-secret"


def test_raise_when_nothing_and_no_prompt(monkeypatch):
    _save()
    monkeypatch.setattr(keyring_store, "is_available", lambda: False)
    with pytest.raises(D365Error, match="keyring|--store-password"):
        conn_mod.resolve_credentials("prod", allow_prompt=False)
```

- [ ] **Step 2: Run the tests — expect failure**

Run: `pytest crm/tests/test_resolve_credentials_keyring.py -q`
Expected: FAIL — `resolve_credentials` has no `allow_prompt` kwarg / does not consult keyring (e.g. `TypeError: unexpected keyword argument 'allow_prompt'`, and the keyring/plaintext assertions fail).

- [ ] **Step 3: Implement the resolver changes**

In `crm/core/connection.py`, add the import near the top (after the `from crm.core import session as session_mod` line, ~line 37):

```python
from crm.core import keyring_store
```

Replace `resolve_credentials` (lines ~230-270) with:

```python
def resolve_credentials(
    profile_name: str | None = None,
    password_override: str | None = None,
    *,
    allow_prompt: bool = False,
) -> ResolvedCredentials:
    """Resolve a ConnectionProfile + the one secret its scheme needs.

    Secret order: override → env → on-disk (plaintext _secret, else OS keyring)
    → TTY prompt (only when ``allow_prompt``) → raise. The on-disk and prompt
    steps fire only for a named profile; env-only mode is unchanged.

    ``allow_prompt`` defaults False so core stays non-interactive for library
    callers and tests; the CLI passes True only on a TTY and not under --json.
    """
    load_dotenv()
    if profile_name:
        try:
            profile = session_mod.load_profile(profile_name)
        except FileNotFoundError as exc:
            raise D365Error(f"Profile {profile_name!r} not found.") from exc
    else:
        profile = profile_from_env()

    is_oauth = profile.auth_scheme == "oauth"
    env_secret = _env(ENV_CLIENT_SECRET) if is_oauth else _env(ENV_PASSWORD)
    secret = password_override or env_secret

    # On-disk secret (named profiles only): plaintext _secret wins over keyring
    # (a profile carries at most one store; the connect flags are exclusive).
    if not secret and profile_name:
        secret = session_mod.load_profile_secret(profile_name)
        if not secret and keyring_store.is_available():
            secret = keyring_store.get_secret(profile_name)

    if not secret and allow_prompt:
        import getpass
        label = "client secret" if is_oauth else "password"
        entered = getpass.getpass(f"D365 {label} for profile {profile.name!r}: ")
        secret = entered or None

    if not secret:
        if is_oauth:
            raise D365Error(
                f"No client secret supplied. Set {ENV_CLIENT_SECRET} (or "
                "CRM_CLIENT_SECRET) in the environment / .env, pass --password, "
                "or store it once with `crm connection connect --store-password` "
                "(OS keyring) / --store-password-plaintext."
            )
        raise D365Error(
            f"No password supplied. Set {ENV_PASSWORD} (or CRM_PASSWORD) in the "
            "environment / .env, pass --password, or store it once with "
            "`crm connection connect --store-password` (OS keyring) / "
            "--store-password-plaintext."
        )

    return ResolvedCredentials(profile=profile, password=secret)
```

- [ ] **Step 4: Run the tests — expect pass**

Run: `pytest crm/tests/test_resolve_credentials_keyring.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the existing resolver/connection tests for regressions**

Run: `pytest crm/tests/test_oauth_auth.py crm/tests/test_auth_scheme.py crm/tests/test_connection_cmd.py -q`
Expected: PASS (no behavior change for flag/env paths).

- [ ] **Step 6: Commit**

```bash
git add crm/core/connection.py crm/tests/test_resolve_credentials_keyring.py
git commit -m "feat(connection): resolve secret from keyring/plaintext/prompt after env (#130)"
```

---

## Task 4: `backend()` restores the session's active_profile

**Files:**
- Modify: `crm/cli.py:116-134` (`CLIContext.backend()`)
- Test: `crm/tests/test_active_profile_restore.py`

Precedence: `--profile` flag > session `active_profile` > env. A stale `active_profile` (file deleted) falls back to env. Also wires `allow_prompt = _stdin_is_tty() and not json_mode`.

- [ ] **Step 1: Write the failing tests**

Create `crm/tests/test_active_profile_restore.py`:

```python
"""backend() restores session active_profile across invocations (issue #130)."""
# pyright: basic
from __future__ import annotations

import json
import os

import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import keyring_store

_WHOAMI = {"UserId": "00000000-0000-0000-0000-000000000001"}
_BASE = "https://crm.contoso.local/Contoso"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    saved = dict(os.environ)
    os.environ["CRM_HOME"] = str(tmp_path / ".crm")
    os.environ["CRM_DOTENV"] = str(tmp_path / "noop.env")
    for k in ("D365_URL", "CRM_BASE_URL", "D365_PASSWORD", "CRM_PASSWORD"):
        os.environ.pop(k, None)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture
def fake_keyring(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(keyring_store, "is_available", lambda: True)
    monkeypatch.setattr(keyring_store, "get_secret", lambda n: store.get(n))
    monkeypatch.setattr(keyring_store, "set_secret",
                        lambda n, s: store.__setitem__(n, s))
    monkeypatch.setattr(keyring_store, "has_secret", lambda n: n in store)
    return store


def test_whoami_uses_active_profile_without_flag(fake_keyring):
    runner = CliRunner()
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", json=_WHOAMI)
        # connect stores in keyring AND sets the session active_profile.
        r1 = runner.invoke(cli, [
            "connection", "connect", "--url", _BASE, "--username", "alice",
            "--domain", "CONTOSO", "--password", "pw",
            "--profile-name", "prod", "--store-password",
        ])
        assert r1.exit_code == 0, r1.output
        # Fresh invocation: NO --profile, NO env, NO --password. Must work via
        # restored active_profile + keyring secret.
        r2 = runner.invoke(cli, ["--json", "connection", "whoami"])
    assert r2.exit_code == 0, r2.output
    assert json.loads(r2.stdout)["data"]["UserId"] == _WHOAMI["UserId"]


def test_stale_active_profile_falls_back_to_env(fake_keyring, monkeypatch):
    # active_profile points at a deleted profile → fall back to env, no crash.
    runner = CliRunner()
    with requests_mock.Mocker() as m:
        m.get(f"{_BASE}/api/data/v9.2/WhoAmI", json=_WHOAMI)
        runner.invoke(cli, [
            "connection", "connect", "--url", _BASE, "--username", "alice",
            "--domain", "CONTOSO", "--password", "pw",
            "--profile-name", "prod", "--store-password",
        ])
        # Delete the profile file but leave active_profile pointing at it.
        os.remove(os.path.join(os.environ["CRM_HOME"], "profiles", "prod.json"))
        monkeypatch.setenv("D365_URL", _BASE)
        monkeypatch.setenv("D365_USERNAME", "bob")
        monkeypatch.setenv("D365_PASSWORD", "envpw")
        r = runner.invoke(cli, ["--json", "connection", "whoami"])
    assert r.exit_code == 0, r.output  # used env, did not crash on stale pointer
```

- [ ] **Step 2: Run the tests — expect failure**

Run: `pytest crm/tests/test_active_profile_restore.py -q`
Expected: FAIL — `whoami` with no `--profile` resolves from env (no active_profile restore yet), so the first test errors with "No password supplied" / profile not used.

- [ ] **Step 3: Implement the backend() change**

In `crm/cli.py`, replace the body of `CLIContext.backend()` (lines ~116-134) with:

```python
    def backend(self) -> "D365Backend":
        from crm.core import connection as conn_mod
        from crm.core import session as session_mod
        from crm.utils.d365_backend import D365Backend

        # Profile selection: --profile flag > session active_profile > env.
        # A flag value is authoritative; otherwise fall back to the saved
        # active_profile so `connect` once works on later commands (#130).
        effective_profile = self.profile_name
        if effective_profile is None:
            state = session_mod.load_session(self.session_name)
            candidate = state.get("active_profile")
            # Ignore a stale pointer to a deleted profile — fall back to env.
            if candidate and session_mod.profile_path(candidate).is_file():
                effective_profile = candidate

        key = (effective_profile, self.password, self.dry_run, self.auth_scheme,
               self.retry_on_ambiguous)
        if self._backend is None or self._backend_key != key:
            allow_prompt = _stdin_is_tty() and not self.json_mode
            resolved = conn_mod.resolve_credentials(
                profile_name=effective_profile,
                password_override=self.password,
                allow_prompt=allow_prompt,
            )
            if self.auth_scheme is not None:
                resolved.profile.auth_scheme = self.auth_scheme
            self._backend = D365Backend(
                resolved.profile, resolved.password, dry_run=self.dry_run,
                retry_on_ambiguous=self.retry_on_ambiguous,
            )
            self._backend_key = key
        return self._backend
```

(`_stdin_is_tty` is defined at module level in `crm/cli.py` — call it directly.)

- [ ] **Step 4: Run the tests — expect pass**

Run: `pytest crm/tests/test_active_profile_restore.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Regression — env-only and explicit-profile paths still work**

Run: `pytest crm/tests/test_connection_cmd.py crm/tests/test_connection_doctor_cmd.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add crm/cli.py crm/tests/test_active_profile_restore.py
git commit -m "feat(connection): restore session active_profile when no --profile given (#130)"
```

---

## Task 5: `connect` store flags

**Files:**
- Modify: `crm/commands/connection.py:18-71` (`connection_connect`)
- Test: add to `crm/tests/test_connection_cmd.py`

Two mutually-exclusive flags; validate the conflict before any backend work; capture the resolved secret and write it to the chosen store on success.

- [ ] **Step 1: Write the failing tests**

Append to `crm/tests/test_connection_cmd.py` (the file already has `_isolated_home` autouse + `_profile_json`):

```python
from crm.core import keyring_store  # add to the imports at top of the file


@pytest.fixture
def fake_keyring(monkeypatch):
    store = {}
    monkeypatch.setattr(keyring_store, "is_available", lambda: True)
    monkeypatch.setattr(keyring_store, "get_secret", lambda n: store.get(n))
    monkeypatch.setattr(keyring_store, "set_secret",
                        lambda n, s: store.__setitem__(n, s))
    monkeypatch.setattr(keyring_store, "has_secret", lambda n: n in store)
    monkeypatch.setattr(keyring_store, "delete_secret",
                        lambda n: store.pop(n, None) is not None)
    return store


class TestConnectStoreFlags:
    _BASE = "https://crm.contoso.local/Contoso"

    def test_store_password_writes_keyring(self, fake_keyring):
        with requests_mock.Mocker() as m:
            m.get(f"{self._BASE}/api/data/v9.2/WhoAmI", json=_WHOAMI)
            r = CliRunner().invoke(cli, [
                "connection", "connect", "--url", self._BASE, "--username", "alice",
                "--domain", "CONTOSO", "--password", "pw",
                "--profile-name", "prod", "--store-password",
            ])
        assert r.exit_code == 0, r.output
        assert fake_keyring["prod"] == "pw"

    def test_store_plaintext_writes_secret_with_warning(self, tmp_path):
        with requests_mock.Mocker() as m:
            m.get(f"{self._BASE}/api/data/v9.2/WhoAmI", json=_WHOAMI)
            r = CliRunner(mix_stderr=False).invoke(cli, [
                "connection", "connect", "--url", self._BASE, "--username", "alice",
                "--domain", "CONTOSO", "--password", "pw",
                "--profile-name", "ci", "--store-password-plaintext",
            ])
        assert r.exit_code == 0, r.output
        assert _profile_json(tmp_path, "ci")["_secret"] == "pw"
        assert "plaintext" in r.stderr.lower()

    def test_both_flags_is_usage_error(self):
        r = CliRunner().invoke(cli, [
            "connection", "connect", "--url", self._BASE, "--username", "alice",
            "--password", "pw", "--profile-name", "x",
            "--store-password", "--store-password-plaintext",
        ])
        assert r.exit_code == 2  # click.UsageError
        assert "mutually exclusive" in (r.output + (r.stderr or "")).lower()

    def test_store_password_without_keyring_is_graceful(self, monkeypatch):
        monkeypatch.setattr(keyring_store, "is_available", lambda: False)
        def _raise(n, s):
            from crm.utils.d365_backend import D365Error
            raise D365Error("The optional 'keyring' dependency is not installed.")
        monkeypatch.setattr(keyring_store, "set_secret", _raise)
        with requests_mock.Mocker() as m:
            m.get(f"{self._BASE}/api/data/v9.2/WhoAmI", json=_WHOAMI)
            r = CliRunner().invoke(cli, [
                "connection", "connect", "--url", self._BASE, "--username", "alice",
                "--password", "pw", "--profile-name", "p", "--store-password",
            ])
        assert r.exit_code == 1               # graceful failure envelope
        assert "Traceback" not in (r.output + (r.stderr or ""))
        assert "keyring" in (r.output + (r.stderr or "")).lower()
```

- [ ] **Step 2: Run the tests — expect failure**

Run: `pytest crm/tests/test_connection_cmd.py::TestConnectStoreFlags -q`
Expected: FAIL — the flags don't exist yet ("no such option: --store-password").

- [ ] **Step 3: Implement the connect flags**

In `crm/commands/connection.py`, add to the imports at the top:

```python
from crm.core import keyring_store
from crm.cli import CLIContext, FAILURE_EXIT_CODE, pass_ctx, _stdin_is_tty
```

(the line currently imports `CLIContext, FAILURE_EXIT_CODE, pass_ctx` — extend it with `_stdin_is_tty`.)

Add the two options to `connection_connect` (after the `--publisher-prefix` option, before `@pass_ctx`):

```python
@click.option("--store-password", is_flag=True,
              help="Store the secret in the OS keyring (service 'crm', account = "
                   "profile name) so later commands need no password. Needs the "
                   "'crm[keyring]' extra.")
@click.option("--store-password-plaintext", is_flag=True,
              help="Headless/CI fallback: write the secret into the profile file "
                   "(0600 on POSIX; perms unenforced on Windows). Emits a warning.")
```

Add the two params to the function signature (after `publisher_prefix`):

```python
                       store_password, store_password_plaintext):
```

At the very top of the function body (before building the profile), validate the conflict:

```python
    if store_password and store_password_plaintext:
        raise click.UsageError(
            "--store-password and --store-password-plaintext are mutually exclusive."
        )
```

Replace the secret-wiring block (current lines ~52-54:
`ctx.profile_name = ...; ctx.password = ...; ctx.invalidate_backend()`) with an explicit resolve that captures the secret once (so storing and the connection test share one prompt):

```python
    session_mod.save_profile(profile)
    ctx.profile_name = profile_name
    # Resolve the secret once (flag → env → keyring/plaintext → TTY prompt) so we
    # can both connect with it and store it without prompting twice.
    allow_prompt = _stdin_is_tty() and not ctx.json_mode
    try:
        resolved = conn_mod.resolve_credentials(
            profile_name=profile_name,
            password_override=password_opt,
            allow_prompt=allow_prompt,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.password = resolved.password
    ctx.invalidate_backend()
```

(This drops the old `os.environ.get(conn_mod.ENV_PASSWORD, "")` lookup — the resolver now owns env. Keep the file's existing `import os`; it is still needed below for `os.name`.)

After the negotiated-version re-save and `session_mod.save_session(...)` block, and before the final `ctx.emit(...)`, write the secret to the chosen store:

```python
    if store_password:
        try:
            keyring_store.set_secret(profile_name, resolved.password)
        except D365Error as exc:
            _handle_d365_error(ctx, exc)
            return
    elif store_password_plaintext:
        session_mod.save_profile_secret_plaintext(profile_name, resolved.password)
        warn = (
            "Stored the secret in PLAINTEXT in the profile file."
            if os.name != "posix"
            else "Stored the secret in PLAINTEXT in the profile file (0600)."
        )
        if os.name != "posix":
            warn += (" On Windows file permissions are NOT enforced — prefer "
                     "--store-password (Credential Manager).")
        ctx.skin.warning(warn)
```

Final clean-up: the secret must be stored only after the last `save_profile` call (the negotiated-version re-save), since `save_profile` writes `to_dict()` which has no `_secret` and would otherwise wipe a just-written plaintext secret. The placement above (after the re-save block) satisfies this.

- [ ] **Step 4: Run the tests — expect pass**

Run: `pytest crm/tests/test_connection_cmd.py::TestConnectStoreFlags -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Full connection-command regression**

Run: `pytest crm/tests/test_connection_cmd.py -q`
Expected: PASS (negotiation + missing-profile + store-flags).

- [ ] **Step 6: Commit**

```bash
git add crm/commands/connection.py crm/tests/test_connection_cmd.py
git commit -m "feat(connection): --store-password / --store-password-plaintext on connect (#130)"
```

---

## Task 6: `connection delete-password` command

**Files:**
- Modify: `crm/commands/connection.py` (new command)
- Test: add to `crm/tests/test_connection_cmd.py`

Clears the secret from BOTH stores; clear "nothing stored" message when neither had one.

- [ ] **Step 1: Write the failing tests**

Append to `crm/tests/test_connection_cmd.py`:

```python
class TestDeletePassword:
    def _save_profile(self):
        from crm.utils.d365_backend import ConnectionProfile
        session_path = None  # noqa: F841
        from crm.core import session as session_mod
        session_mod.save_profile(ConnectionProfile(
            name="prod", url="https://crm.contoso.local/c", domain="C", username="a",
        ))

    def test_delete_removes_keyring_entry(self, fake_keyring):
        self._save_profile()
        fake_keyring["prod"] = "pw"
        r = CliRunner().invoke(cli, ["connection", "delete-password", "--profile", "prod"])
        assert r.exit_code == 0, r.output
        assert "prod" not in fake_keyring

    def test_delete_removes_plaintext_secret(self, fake_keyring, tmp_path):
        from crm.core import session as session_mod
        self._save_profile()
        session_mod.save_profile_secret_plaintext("prod", "pw")
        r = CliRunner().invoke(cli, ["connection", "delete-password", "--profile", "prod"])
        assert r.exit_code == 0, r.output
        assert session_mod.load_profile_secret("prod") is None

    def test_delete_nothing_stored_is_clear_noop(self, fake_keyring):
        self._save_profile()
        r = CliRunner().invoke(cli, ["--json", "connection", "delete-password", "--profile", "prod"])
        assert r.exit_code == 0, r.output
        payload = json.loads(r.stdout)
        assert payload["data"]["removed"] is False
```

- [ ] **Step 2: Run the tests — expect failure**

Run: `pytest crm/tests/test_connection_cmd.py::TestDeletePassword -q`
Expected: FAIL — "No such command 'delete-password'".

- [ ] **Step 3: Implement the command**

In `crm/commands/connection.py`, add after `connection_disconnect` (before the `doctor_command`):

```python
@connection_group.command("delete-password")
@click.option("--profile", "profile_name", required=True,
              help="Profile whose stored secret should be removed.")
@pass_ctx
def connection_delete_password(ctx: CLIContext, profile_name):
    """Remove a stored secret for a profile (OS keyring AND plaintext)."""
    removed_keyring = keyring_store.delete_secret(profile_name)
    removed_plaintext = session_mod.clear_profile_secret(profile_name)
    removed = removed_keyring or removed_plaintext
    where = []
    if removed_keyring:
        where.append("keyring")
    if removed_plaintext:
        where.append("plaintext")
    ctx.emit(
        True,
        data={"profile": profile_name, "removed": removed, "from": where},
        meta={"note": "no stored secret found" if not removed else None},
    )
```

- [ ] **Step 4: Run the tests — expect pass**

Run: `pytest crm/tests/test_connection_cmd.py::TestDeletePassword -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add crm/commands/connection.py crm/tests/test_connection_cmd.py
git commit -m "feat(connection): add 'connection delete-password' (#130)"
```

---

## Task 7: `connection profiles` reports storage type

**Files:**
- Modify: `crm/commands/connection.py:124-157` (`connection_profiles`)
- Test: add to `crm/tests/test_connection_cmd.py`

Per-profile `credential_storage` = `plaintext` (file has `_secret`) | `keyring` (`has_secret`) | `none`. `--json` `data` stays the bare name list.

- [ ] **Step 1: Write the failing tests**

Append to `crm/tests/test_connection_cmd.py`:

```python
class TestProfilesStorageType:
    def _mk(self, name):
        from crm.utils.d365_backend import ConnectionProfile
        from crm.core import session as session_mod
        session_mod.save_profile(ConnectionProfile(
            name=name, url="https://crm.contoso.local/c", domain="C", username="a",
        ))

    def test_reports_three_storage_types(self, fake_keyring):
        from crm.core import session as session_mod
        self._mk("kr"); self._mk("pt"); self._mk("no")
        fake_keyring["kr"] = "pw"
        session_mod.save_profile_secret_plaintext("pt", "pw")
        r = CliRunner().invoke(cli, ["--json", "connection", "profiles"])
        assert r.exit_code == 0, r.output
        payload = json.loads(r.stdout)
        assert sorted(payload["data"]) == ["kr", "no", "pt"]  # data shape unchanged
        by = {p["name"]: p["credential_storage"] for p in payload["meta"]["profiles"]}
        assert by == {"kr": "keyring", "pt": "plaintext", "no": "none"}
```

- [ ] **Step 2: Run the tests — expect failure**

Run: `pytest crm/tests/test_connection_cmd.py::TestProfilesStorageType -q`
Expected: FAIL — `meta.profiles[]` entries have no `credential_storage` key (KeyError).

- [ ] **Step 3: Implement the reporting**

In `crm/commands/connection.py`, add a helper above `connection_profiles`:

```python
def _credential_storage(name: str) -> str:
    """Where this profile's secret is stored: plaintext > keyring > none.

    Plaintext is checked first (a cheap file read, no keyring call) and reported
    even if a keyring entry also exists — the on-disk secret is the one to flag.
    """
    if session_mod.load_profile_secret(name) is not None:
        return "plaintext"
    if keyring_store.has_secret(name):
        return "keyring"
    return "none"
```

In `connection_profiles`, extend the JSON branch — add `credential_storage` to each `profiles` entry:

```python
        profiles = []
        for n in names:
            storage = _credential_storage(n)
            try:
                p = session_mod.load_profile(n)
                profiles.append({
                    "name": n,
                    "default_solution": p.default_solution,
                    "publisher_prefix": p.publisher_prefix,
                    "credential_storage": storage,
                })
            except FileNotFoundError:
                profiles.append({"name": n, "credential_storage": storage})
        ctx.emit(True, data=names, meta={"profiles": profiles})
        return
```

And the human branch — append the storage type to the status line:

```python
    for n in names:
        storage = _credential_storage(n)
        try:
            p = session_mod.load_profile(n)
            ctx.skin.status(
                n,
                f"solution={p.default_solution} prefix={p.publisher_prefix} "
                f"cred={storage}",
            )
        except FileNotFoundError:
            ctx.skin.status(n, f"cred={storage}")
```

- [ ] **Step 4: Run the tests — expect pass**

Run: `pytest crm/tests/test_connection_cmd.py::TestProfilesStorageType -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add crm/commands/connection.py crm/tests/test_connection_cmd.py
git commit -m "feat(connection): report per-profile credential storage type in profiles (#130)"
```

---

## Task 8: `keyring` optional extra in `setup.py`

**Files:**
- Modify: `setup.py:24-34` (`extras_require`)

- [ ] **Step 1: Add the extra**

In `setup.py`, inside `extras_require`, add (after the `"kerberos"` line):

```python
        "keyring": ["keyring>=24"],
```

- [ ] **Step 2: Verify it installs and is importable**

Run: `pip install -e ".[keyring]" && python -c "import keyring; print(keyring.__version__)"`
Expected: prints a version ≥ 24, no error.

- [ ] **Step 3: Verify the wrapper degrades gracefully without it (sanity)**

Run: `pytest crm/tests/test_keyring_store.py -q`
Expected: PASS (tests patch `_import_keyring`, so they pass regardless of whether the extra is installed).

- [ ] **Step 4: Commit**

```bash
git add setup.py
git commit -m "feat(connection): add optional 'keyring' extra (crm[keyring]) (#130)"
```

---

## Task 9: Documentation sync

**Files:**
- Modify: `README.md`, `docs/how-to/connection.md`, `docs/reference/cli.md`, `crm/skills/SKILL.md`

Docs ship in the same change (project rule). CHANGELOG is **not** touched — PSR owns it.

- [ ] **Step 1: README capability note**

In `README.md`, in the connection/auth section, add a short note:

```markdown
### Storing credentials once

By default secrets are never persisted. To configure once:

- `crm connection connect ... --store-password` saves the secret in your OS
  keyring (macOS Keychain / Windows Credential Manager / Linux SecretService).
  Requires the optional extra: `pip install crm[keyring]`.
- For headless/CI hosts with no keyring, `--store-password-plaintext` writes the
  secret into the profile file (`0600` on POSIX; perms unenforced on Windows).
- `crm connection delete-password --profile NAME` removes a stored secret.
- `crm connection profiles` shows each profile's storage type (keyring / plaintext / none).

Resolution order: `--password` > `D365_PASSWORD`/`CRM_PASSWORD` (env/.env) >
stored secret (keyring or plaintext) > interactive prompt (TTY only).
```

- [ ] **Step 2: how-to/connection.md**

Read `docs/how-to/connection.md` first (`Read` it), then add a "Store credentials once" section mirroring the README note but with full command examples for both NTLM (password) and OAuth (client secret), the CI/headless path (`--store-password-plaintext`), and `delete-password`. Match the existing heading style and admonition style in that file.

- [ ] **Step 3: reference/cli.md**

Read `docs/reference/cli.md`. If it documents commands manually, add the two `connect` flags and the `delete-password` command. If it is generated via `mkdocs-click` from the Click tree, no manual edit is needed — verify which by grepping for `::: mkdocs-click` or an existing flag listing, and only edit if it's hand-maintained.

- [ ] **Step 4: SKILL.md "never persisted" lines**

In `crm/skills/SKILL.md`, find the lines asserting secrets are "never persisted to disk" (grep `never persisted`). Update to:

> Secrets are not persisted by default. They may be stored on explicit opt-in:
> `crm connection connect --store-password` (OS keyring) or
> `--store-password-plaintext` (profile file, `0600` on POSIX). Resolution:
> `--password` > env/.env > stored secret > TTY prompt.

- [ ] **Step 5: Build docs to verify no broken refs**

Run: `mkdocs build --strict`
Expected: builds with no warnings (warnings fail CI).

- [ ] **Step 6: Commit**

```bash
git add README.md docs/how-to/connection.md docs/reference/cli.md crm/skills/SKILL.md
git commit -m "docs(connection): document configure-once credential storage (#130)"
```

---

## Task 10: Graduate to v1.0.0 (PSR config)

**Files:**
- Modify: `pyproject.toml:10-14` (`[tool.semantic_release]`)

Do **not** hand-edit `setup.py`/`crm/__init__.py` — PSR owns those `version_variables` and bumps them at release. Graduating to 1.0.0 is done by flipping `allow_zero_version`: with it `false`, the next release driven by this feature's `feat:` commits is forced to `1.0.0`. (The existing comment in `pyproject.toml` documents exactly this.)

- [ ] **Step 1: Flip the graduation lever**

In `pyproject.toml`, replace the pre-1.0 block:

```toml
# Project is pre-1.0: stay in 0.x. allow_zero_version defaults to false in PSR
# v10 (would force 1.0.0); major_on_zero=false keeps breaking changes a minor
# while we are still in 0.x.
allow_zero_version = true
major_on_zero = false
```

with:

```toml
# Graduated to 1.0 (#130). With allow_zero_version=false PSR forces the next
# release (driven by this milestone's feat: commits) to 1.0.0, and normal semver
# resumes thereafter (breaking → major). major_on_zero is moot once >= 1.0.
allow_zero_version = false
major_on_zero = true
```

- [ ] **Step 2: Dry-run the next version PSR would compute**

Run: `python -m semantic_release version --print` (or `semantic-release version --print` if installed as a script)
Expected: prints `1.0.0` (it reads the `feat:` commits from this branch + the flipped config). If the tool isn't installed locally, skip — CI computes it; the flip + a `feat:` history is sufficient.

- [ ] **Step 3: Confirm the version files are unchanged (PSR will bump them at release, not us)**

Run: `git diff --stat setup.py crm/__init__.py`
Expected: no changes (they stay `0.13.1` in the branch; PSR rewrites them to `1.0.0` during the release commit on merge to `main`).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "feat(release)!: graduate to 1.0.0 with configure-once credentials (#130)

BREAKING CHANGE: secrets can now be persisted (opt-in keyring/plaintext);
profile resolution restores the session active_profile when no --profile is
given. Flips allow_zero_version so PSR cuts v1.0.0."
```

---

## Full-Suite Verification (after all tasks)

- [ ] **Run the whole test suite**

Run: `pytest -q`
Expected: all pass (E2E tests needing live D365 creds are skipped without `.env`).

- [ ] **Run pyright on touched strict files**

Run: `pyright --pythonpath .venv/bin/python --pythonversion 3.9 crm/core/keyring_store.py crm/core/connection.py crm/core/session.py`
Expected: 0 errors (these are strict-checked).

- [ ] **Docs build**

Run: `mkdocs build --strict`
Expected: clean.

- [ ] **Live round-trip (maintainer, Windows) — manual**

```powershell
crm connection connect --url https://<host>/<org> --username <u> --domain <d> --profile-name prod --store-password
crm --json connection whoami        # no --profile, no --password → succeeds via Credential Manager
crm connection profiles             # prod → cred=keyring
crm connection delete-password --profile prod
```
Expected: `whoami` succeeds with no secret supplied; `profiles` shows `keyring`; delete removes the Credential Manager entry.

---

## Self-Review Notes (author)

- **Spec coverage:** keyring store (T1), plaintext Approach B + 0600 (T2), resolver order incl. prompt (T3), active_profile restore + precedence (T4), connect flags + mutual exclusion + graceful no-keyring (T5), delete-password both stores (T6), profiles storage type (T7), optional extra (T8), docs incl. SKILL.md + CHANGELOG-left-to-PSR (T9). OAuth client-secret path is covered by the scheme-aware `env_secret`/error branches in T3 and exercised by `test_oauth_auth.py` regression in T3 Step 5.
- **Type consistency:** function names used identically across tasks — `keyring_store.{is_available,get_secret,set_secret,delete_secret,has_secret}`, `session_mod.{save_profile_secret_plaintext,load_profile_secret,clear_profile_secret}`, `resolve_credentials(..., allow_prompt=)`, `_credential_storage(name)`.
- **TDD-collection trap:** the only brand-new importable module (`keyring_store`) is created as a skeleton in T1 Step 1 before its test imports it; all other new tests import existing modules (`from crm.core import session as session_mod`) and hit `AttributeError` at call time (a clean test failure), never a collection ImportError.
- **Secret-wipe ordering:** plaintext `_secret` is written only after the final `save_profile` in `connect` (T5 Step 3), since `save_profile` writes `to_dict()` (no `_secret`).
