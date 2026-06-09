# Profile & Credential UX Revamp — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the split `init` / `connection connect` setup with one `crm profile` command group that saves credentials by default (keyring → 0600 plaintext fallback), offers an interactive add-wizard and profile picker, auto-launches setup on first use, and makes a clean break from all `.env` / `D365_*` / `CRM_*` credential reading.

**Architecture:** A new `crm/commands/profile.py` group owns profile management (`add`/`use`/`list`/`edit`/`rm`/`set-password`/`delete-password`). `crm/core/connection.py` is gutted of env-derived profiles and `.env` autoload, collapsing credential resolution to `--password` → profile (keyring → plaintext). `crm/cli.py`'s `backend()` seam resolves only `--profile` > active profile, and auto-launches the wizard on a TTY when no profile exists. `crm connection` keeps only the live-diagnostic verbs. Storage primitives in `session.py` / `keyring_store.py` are reused unchanged.

**Tech Stack:** Python 3.9 floor, Click 8.x, `prompt_toolkit` 3.x (already a core dep — used by the REPL), `keyring`, `msal`, `pytest` + `requests_mock`, pyright (strict on `crm/core/*`).

**Spec:** `docs/superpowers/specs/2026-06-09-profile-credential-ux-design.md`

---

## Conventions for every task

- **Run tests with the main venv against this checkout** (worktrees have no venv — see [[project-worktree-test-without-venv]]). From the repo root:
  - Tests: `python -m pytest <path> -v` (if in a worktree: `PYTHONPATH=$PWD <main-venv>/python -m pytest <path> -v`).
  - Lint: `pyright --pythonpath .venv/bin/python --pythonversion 3.9` (the `3.9` pin catches 3.10+-only symbols that would `ImportError` on the floor — see [[project-pyright-venv-pythonpath]]).
- **Test isolation:** every test that touches stored profiles sets `monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))` so it never reads the user's real `~/.crm`. Tests stay generic — `Contoso` / `crm.contoso.local`, never real org values ([[feedback-keep-repo-generic]]); `git grep -ni moce` must be empty before any commit.
- **Test files** carry `# pyright: basic` on line 2 (the whole tree is linted, including tests — [[project-pyright-venv-pythonpath]]).
- **Commit cadence:** one commit per task, Conventional Commit subject. The FINAL task carries the breaking marker that drives the v2.0.0 bump; intermediate commits use `feat:` / `refactor:` / `test:` / `docs:` as fits. Do NOT hand-edit `CHANGELOG.md` ([[feedback-no-manual-changelog-post-psr]]).
- **Command-layer I/O house rules** ([[feedback-crm-command-layer-io-conventions]]): mutually-exclusive flags `raise click.UsageError` (exit 2); validate untrusted input before `ctx.backend()`; JSON-only `meta` fields gate on `ctx.json_mode` ([[project-emit-meta-renders-in-human-mode]]).

---

## File structure

| File | Responsibility | Action |
|------|----------------|--------|
| `crm/commands/profile.py` | The `crm profile` group + wizard + picker glue | **Create** |
| `crm/commands/_helpers.py` | Add `select_one()` picker, `infer_auth_scheme()`, `default_profile_name()`, `_auth_error_hint()` | Modify |
| `crm/core/connection.py` | Strip env/.env; collapse `resolve_credentials`; add `connect_profile()` engine | Modify |
| `crm/commands/connection.py` | Keep only `status`/`whoami`/`test`/`doctor`; drop the rest | Modify |
| `crm/commands/init.py` | Delete | **Delete** |
| `crm/cli.py` | Resolution chain (profile-only) + auto-launch seam + registry edits | Modify |
| `crm.spec` | Swap `init` → `profile` in `hiddenimports` | Modify |
| `crm/skills/SKILL.md` + `reference/*.md` | New command names, no-`.env` contract | Modify |
| `docs/**`, `README.md`, `CLAUDE.md` | User-facing docs | Modify |
| `crm/tests/test_profile_cmd.py` | New behavior tests | **Create** |
| `crm/tests/test_*` (env-path) | Rework / delete env-derived cases | Modify |

---

## Task 1: Add the credential-UX helpers (pure functions, TDD)

Foundation utilities with no Click/IO coupling, so later tasks import them. All live in `_helpers.py`.

**Files:**
- Modify: `crm/commands/_helpers.py`
- Test: `crm/tests/test_profile_helpers.py` (Create)

- [ ] **Step 1: Write the failing test**

Create `crm/tests/test_profile_helpers.py`:

```python
"""Unit tests for profile-UX pure helpers."""
# pyright: basic
from __future__ import annotations

from crm.commands._helpers import (
    infer_auth_scheme,
    default_profile_name,
    _auth_error_hint,
)


class TestInferAuthScheme:
    def test_dynamics_host_is_oauth(self):
        assert infer_auth_scheme("https://org.crm.dynamics.com") == "oauth"

    def test_dynamics_regional_host_is_oauth(self):
        assert infer_auth_scheme("https://org.crm4.dynamics.com/") == "oauth"

    def test_onprem_host_is_ntlm(self):
        assert infer_auth_scheme("https://crm.contoso.local/contoso") == "ntlm"

    def test_blank_url_defaults_ntlm(self):
        assert infer_auth_scheme("") == "ntlm"


class TestDefaultProfileName:
    def test_uses_first_host_label(self):
        assert default_profile_name("https://crm.contoso.local/contoso") == "crm"

    def test_dynamics_uses_org_label(self):
        assert default_profile_name("https://orgd080.crm.dynamics.com") == "orgd080"

    def test_falls_back_to_default_when_unparseable(self):
        assert default_profile_name("not-a-url") == "default"

    def test_blank_falls_back_to_default(self):
        assert default_profile_name("") == "default"


class TestAuthErrorHint:
    def test_401_hints_set_password(self):
        hint = _auth_error_hint(401, "cloud")
        assert "crm profile set-password" in hint
        assert "--profile cloud" in hint

    def test_no_secret_message_hints_set_password(self):
        hint = _auth_error_hint(None, "cloud", no_secret=True)
        assert "crm profile set-password" in hint

    def test_unrelated_status_has_no_hint(self):
        assert _auth_error_hint(404, "cloud") == ""
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest crm/tests/test_profile_helpers.py -v`
Expected: FAIL — `ImportError: cannot import name 'infer_auth_scheme'`.

- [ ] **Step 3: Implement the helpers**

Append to `crm/commands/_helpers.py` (after the existing imports; `urllib.parse` import goes at the top with the other stdlib imports):

```python
# (add to the stdlib import block at the top of the file)
import urllib.parse
```

```python
# ── Profile-UX helpers (credential revamp) ───────────────────────────────

# Dataverse online hosts always end in this suffix (crm.dynamics.com,
# crm4.dynamics.com, crm.dynamics.cn, ...). Anything else is treated as on-prem.
_CLOUD_HOST_MARKER = ".dynamics."


def infer_auth_scheme(url: str) -> str:
    """Guess the auth scheme from the server URL: oauth for Dataverse online
    (`*.dynamics.*`), else ntlm. The wizard shows this as an overridable default."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return "oauth" if _CLOUD_HOST_MARKER in host else "ntlm"


def default_profile_name(url: str) -> str:
    """Default profile name = the first label of the URL host (`crm.contoso.local`
    -> `crm`, `orgd080.crm.dynamics.com` -> `orgd080`). Falls back to 'default'
    when the URL has no parseable host."""
    host = urllib.parse.urlparse(url).hostname or ""
    label = host.split(".")[0] if host else ""
    return label or "default"


def _auth_error_hint(status: int | None, profile_name: str,
                     *, no_secret: bool = False) -> str:
    """Map an auth failure to a copy-paste fix command, or '' when none applies.

    A 401 (rejected secret) or a missing-secret error both steer the user to
    re-store the secret for the active profile."""
    if no_secret or status == 401:
        return f"run: crm profile set-password --profile {profile_name}"
    return ""
```

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest crm/tests/test_profile_helpers.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Lint**

Run: `pyright --pythonpath .venv/bin/python --pythonversion 3.9 crm/commands/_helpers.py`
Expected: 0 errors. (`_helpers.py` is basic-mode, but check anyway.)

- [ ] **Step 6: Commit**

```bash
git add crm/commands/_helpers.py crm/tests/test_profile_helpers.py
git commit -m "feat(profile): add auth-inference, name-default, error-hint helpers"
```

---

## Task 2: Add the `select_one` interactive picker helper (TDD)

The arrow-key picker for `crm profile use`. Built on `prompt_toolkit.shortcuts.radiolist_dialog`, with a non-TTY guard so it never hangs in scripts/CI.

**Files:**
- Modify: `crm/commands/_helpers.py`
- Test: `crm/tests/test_profile_helpers.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `crm/tests/test_profile_helpers.py`:

```python
import pytest
from crm.commands._helpers import select_one


class TestSelectOne:
    def test_non_tty_raises_runtime_error(self, monkeypatch):
        # No TTY -> the picker must refuse rather than block on input.
        monkeypatch.setattr("crm.commands._helpers._stdin_is_tty", lambda: False)
        with pytest.raises(RuntimeError, match="no interactive terminal"):
            select_one("Pick one", [("a", "label a"), ("b", "label b")])

    def test_empty_items_raises_value_error(self, monkeypatch):
        monkeypatch.setattr("crm.commands._helpers._stdin_is_tty", lambda: True)
        with pytest.raises(ValueError, match="no choices"):
            select_one("Pick one", [])

    def test_returns_selected_value(self, monkeypatch):
        monkeypatch.setattr("crm.commands._helpers._stdin_is_tty", lambda: True)
        # Stub the prompt_toolkit dialog so the test never opens a real TUI.
        class _FakeDialog:
            def run(self):
                return "b"
        monkeypatch.setattr(
            "crm.commands._helpers.radiolist_dialog",
            lambda **kw: _FakeDialog(),
        )
        assert select_one("Pick one", [("a", "label a"), ("b", "label b")]) == "b"

    def test_cancel_returns_none(self, monkeypatch):
        monkeypatch.setattr("crm.commands._helpers._stdin_is_tty", lambda: True)
        class _FakeDialog:
            def run(self):
                return None  # user hit Esc / Ctrl-C
        monkeypatch.setattr(
            "crm.commands._helpers.radiolist_dialog",
            lambda **kw: _FakeDialog(),
        )
        assert select_one("Pick one", [("a", "label a")]) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest crm/tests/test_profile_helpers.py::TestSelectOne -v`
Expected: FAIL — `cannot import name 'select_one'`.

- [ ] **Step 3: Implement `select_one`**

Add to `crm/commands/_helpers.py`. Import `_stdin_is_tty` lazily inside the function to avoid a circular import (`cli.py` imports `_helpers`):

```python
# at top of file, with the other crm imports — prompt_toolkit is a core dep
from prompt_toolkit.shortcuts import radiolist_dialog


def select_one(title: str, items: list[tuple[str, str]]) -> str | None:
    """Show an arrow-key single-select picker; return the chosen value (the first
    element of the chosen tuple) or None if the user cancelled.

    `items` is a list of (value, label) pairs. Raises ValueError on empty input
    and RuntimeError when stdin is not a TTY (scripts/CI must pass an explicit
    choice instead of relying on the picker)."""
    from crm.cli import _stdin_is_tty
    if not items:
        raise ValueError("select_one: no choices to display")
    if not _stdin_is_tty():
        raise RuntimeError(
            "select_one: no interactive terminal — pass an explicit choice instead"
        )
    return radiolist_dialog(
        title=title,
        text="Use ↑/↓ then Enter; Esc to cancel.",
        values=[(value, label) for value, label in items],
    ).run()
```

> Note: the test monkeypatches `crm.commands._helpers.radiolist_dialog` and `crm.commands._helpers._stdin_is_tty`. For the `_stdin_is_tty` patch to bind, add a module-level re-export at import time:
> ```python
> # near the top, after the cli-free imports
> def _stdin_is_tty() -> bool:
>     from crm.cli import _stdin_is_tty as _impl
>     return _impl()
> ```
> and have `select_one` call the module-local `_stdin_is_tty()` (drop the inline import of it). Keep the `radiolist_dialog` top-level import so the monkeypatch target exists.

- [ ] **Step 4: Run it to verify it passes**

Run: `python -m pytest crm/tests/test_profile_helpers.py::TestSelectOne -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add crm/commands/_helpers.py crm/tests/test_profile_helpers.py
git commit -m "feat(profile): add select_one arrow-key picker helper"
```

---

## Task 3: Collapse credential resolution in core (remove env/.env)

Gut `crm/core/connection.py` of env-derived profiles and `.env` autoload. Add a `save_secret()` engine (keyring → plaintext fallback) and a `connect_profile()` engine the `add` command will call. `connection.py` is **pyright strict** — keep it strictly typed.

**Files:**
- Modify: `crm/core/connection.py`
- Test: `crm/tests/test_resolve_credentials_keyring.py` (rework), `crm/tests/test_core.py` (delete env cases)

- [ ] **Step 1: Write the failing test for the new resolution + storage engine**

Create `crm/tests/test_connection_core.py`:

```python
"""Core credential resolution + storage after the env/.env removal."""
# pyright: basic
from __future__ import annotations

import pytest

from crm.core import connection as conn_mod
from crm.core import session as session_mod
from crm.utils.d365_backend import ConnectionProfile, D365Error


@pytest.fixture
def crm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    return tmp_path


def _save(name="contoso", **kw):
    p = ConnectionProfile(
        name=name, url="https://crm.contoso.local/contoso",
        domain="CONTOSO", username="alice", **kw,
    )
    session_mod.save_profile(p)
    return p


class TestResolveCredentials:
    def test_password_override_wins(self, crm_home):
        _save()
        r = conn_mod.resolve_credentials("contoso", password_override="pw")
        assert r.password == "pw"
        assert r.profile.name == "contoso"

    def test_reads_plaintext_secret(self, crm_home):
        _save()
        session_mod.save_profile_secret_plaintext("contoso", "fromfile")
        r = conn_mod.resolve_credentials("contoso")
        assert r.password == "fromfile"

    def test_missing_profile_raises(self, crm_home):
        with pytest.raises(D365Error, match="not found"):
            conn_mod.resolve_credentials("ghost")

    def test_no_profile_name_raises(self, crm_home):
        # Env-derived profiles are gone: a None profile name is now an error.
        with pytest.raises(D365Error, match="No profile"):
            conn_mod.resolve_credentials(None)

    def test_no_secret_raises_with_actionable_message(self, crm_home):
        _save()
        with pytest.raises(D365Error, match="set-password"):
            conn_mod.resolve_credentials("contoso", allow_prompt=False)


class TestSaveSecret:
    def test_keyring_unavailable_falls_back_to_plaintext(self, crm_home, monkeypatch):
        _save()
        monkeypatch.setattr(conn_mod.keyring_store, "is_available", lambda: False)
        where = conn_mod.save_secret("contoso", "sekret")
        assert where == "plaintext"
        assert session_mod.load_profile_secret("contoso") == "sekret"

    def test_keyring_available_uses_keyring(self, crm_home, monkeypatch):
        _save()
        stored = {}
        monkeypatch.setattr(conn_mod.keyring_store, "is_available", lambda: True)
        monkeypatch.setattr(conn_mod.keyring_store, "set_secret",
                            lambda n, s: stored.__setitem__(n, s))
        monkeypatch.setattr(conn_mod.keyring_store, "delete_secret", lambda n: False)
        where = conn_mod.save_secret("contoso", "sekret")
        assert where == "keyring"
        assert stored["contoso"] == "sekret"
        # keyring path must clear any stale plaintext (single-store invariant)
        assert session_mod.load_profile_secret("contoso") is None

    def test_force_plaintext_skips_keyring(self, crm_home, monkeypatch):
        _save()
        monkeypatch.setattr(conn_mod.keyring_store, "is_available", lambda: True)
        monkeypatch.setattr(conn_mod.keyring_store, "delete_secret", lambda n: False)
        where = conn_mod.save_secret("contoso", "sekret", force_plaintext=True)
        assert where == "plaintext"
        assert session_mod.load_profile_secret("contoso") == "sekret"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest crm/tests/test_connection_core.py -v`
Expected: FAIL — `save_secret` missing; `resolve_credentials(None)` still tries `profile_from_env`.

- [ ] **Step 3: Rewrite the env/secret sections of `crm/core/connection.py`**

3a. **Delete** the module-docstring env-var table (lines 1–16) and replace with:

```python
"""High-level connection management: resolve a profile's secret, store secrets,
test reachability, and run the connection doctor.

Credentials and connection config come ONLY from a saved profile (under
``~/.crm/profiles``) or an explicit ``--password``. There is no ``.env`` autoload
and no ``D365_*`` / ``CRM_*`` environment-variable reading — run ``crm profile add``
once to configure a profile.
"""
```

3b. **Delete** these blocks entirely:
- The env-name constants `ENV_URL … ENV_CLIENT_SECRET` and `_ENV_ALIASES` (lines 41–65).
- `_env()` (68–77), `env_api_version()` (80–87).
- `load_dotenv()` (90–137).
- `_split_domain_user()` (143–155) — only `profile_from_env` used it.
- `profile_from_env()` (167–228).
- `resolve_secret_for_storage()` (294–320) — replaced by `save_secret` + a slimmer prompt path below.

3c. **Replace** `resolve_credentials()` (231–291) with:

```python
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
```

3d. **Add** the storage engine (after `resolve_credentials`):

```python
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
```

3e. **Fix `_RENEGOTIATE_HINT`** (382–385) — it names the removed `crm connection connect`:

```python
_RENEGOTIATE_HINT = (
    "on-prem caps at v9.1 — re-run `crm profile add` without --api-version "
    "to auto-negotiate (tries v9.2, downgrades to v9.1)"
)
```

3f. **Fix the doctor 401 hint** in `_doctor_auth` (666–672) — drop the `D365_PASSWORD`/`CRM_PASSWORD` env references:

```python
    if status == 401:
        return _check(
            "auth", False, "authentication failed (HTTP 401)",
            "check the stored secret — re-store it with "
            "`crm profile set-password --profile <name>`",
        )
```

3g. **Fix the `_doctor_*` token-failure hints** (581, 670) that name `D365_CLIENT_ID` etc. — replace the env-var guidance in the two OAuth hints with: `"for an OAuth profile, check tenant_id/client_id (crm profile edit) and re-store the client secret (crm profile set-password)"`.

- [ ] **Step 4: Run the new core test**

Run: `python -m pytest crm/tests/test_connection_core.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Delete the now-dead env tests, keep the rest**

In `crm/tests/test_core.py`: delete the entire `TestProfileFromEnv` class (the `test_profile_from_env_*` methods, ~lines 60–95) and any `conn_mod.load_dotenv` / `conn_mod.env_api_version` references. Keep the `profile`/`backend` fixtures and all backend/URL tests.

Delete `crm/tests/test_resolve_credentials_keyring.py` (its env-precedence and dotenv cases are obsolete; the keyring/plaintext resolution is now covered by `test_connection_core.py`).

- [ ] **Step 6: Run the core suite + lint**

Run: `python -m pytest crm/tests/test_core.py crm/tests/test_connection_core.py -v`
Expected: PASS.
Run: `pyright --pythonpath .venv/bin/python --pythonversion 3.9 crm/core/connection.py`
Expected: 0 errors.

- [ ] **Step 7: Commit**

```bash
git add crm/core/connection.py crm/tests/test_connection_core.py crm/tests/test_core.py
git rm crm/tests/test_resolve_credentials_keyring.py
git commit -m "refactor(core): resolve credentials from profile only, drop .env/env autoload

Removes load_dotenv, profile_from_env, the D365_*/CRM_* env reading and the
D365_AUTH selector. Adds save_secret() (keyring with 0600-plaintext fallback)."
```

---

## Task 4: Build the `crm profile` command group

The new group. Reuses `conn_mod.test_connection`, `conn_mod.save_secret`, the Task 1–2 helpers, and the session/profile primitives.

**Files:**
- Create: `crm/commands/profile.py`
- Test: `crm/tests/test_profile_cmd.py` (Create)

- [ ] **Step 1: Write the failing tests**

Create `crm/tests/test_profile_cmd.py`:

```python
"""Tests for the `crm profile` command group."""
# pyright: basic
from __future__ import annotations

import json
import pytest
import requests_mock
from click.testing import CliRunner

from crm.cli import cli
from crm.core import session as session_mod

_WHOAMI = {"UserId": "00000000-0000-0000-0000-000000000001",
           "BusinessUnitId": "00000000-0000-0000-0000-0000000000bb",
           "OrganizationId": "00000000-0000-0000-0000-0000000000cc"}


@pytest.fixture
def crm_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_HOME", str(tmp_path / ".crm"))
    # keyring unavailable in tests -> save_secret falls back to plaintext
    import crm.core.keyring_store as ks
    monkeypatch.setattr(ks, "is_available", lambda: False)
    monkeypatch.setattr(ks, "delete_secret", lambda n: False)
    return tmp_path


class TestAddScriptable:
    def test_add_ntlm_saves_profile_and_secret_and_activates(self, crm_home):
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=_WHOAMI)
            result = runner.invoke(cli, [
                "--json", "profile", "add",
                "--url", "https://crm.contoso.local/contoso",
                "--username", "alice", "--domain", "CONTOSO",
                "--password", "pw", "--name", "contoso", "--yes",
            ])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is True
        assert payload["data"]["profile"] == "contoso"
        assert payload["data"]["credential_storage"] == "plaintext"
        # profile persisted + active
        assert "contoso" in session_mod.list_profiles()
        state = session_mod.load_session("default")
        assert state["active_profile"] == "contoso"
        assert session_mod.load_profile_secret("contoso") == "pw"

    def test_add_infers_oauth_from_dynamics_url(self, crm_home):
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, json=_WHOAMI)
            result = runner.invoke(cli, [
                "--json", "profile", "add",
                "--url", "https://org.crm.dynamics.com",
                "--tenant-id", "t1", "--client-id", "c1",
                "--password", "secret", "--name", "cloud", "--yes",
            ])
        assert result.exit_code == 0, result.output
        p = session_mod.load_profile("cloud")
        assert p.auth_scheme == "oauth"
        assert p.tenant_id == "t1" and p.client_id == "c1"

    def test_add_missing_url_in_json_mode_errors_cleanly(self, crm_home):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "add", "--name", "x"])
        # no TTY under CliRunner + no --url -> usage error, never hangs
        assert result.exit_code == 2, result.output


class TestUse:
    def _seed(self, name):
        from crm.utils.d365_backend import ConnectionProfile
        session_mod.save_profile(ConnectionProfile(
            name=name, url=f"https://{name}.contoso.local/o",
            domain="C", username="u"))

    def test_use_by_name_switches_active(self, crm_home):
        self._seed("a"); self._seed("b")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "use", "b"])
        assert result.exit_code == 0, result.output
        assert session_mod.load_session("default")["active_profile"] == "b"

    def test_use_unknown_name_errors(self, crm_home):
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "use", "ghost"])
        assert result.exit_code == 1, result.output

    def test_use_none_clears_active(self, crm_home):
        self._seed("a")
        runner = CliRunner()
        runner.invoke(cli, ["--json", "profile", "use", "a"])
        result = runner.invoke(cli, ["--json", "profile", "use", "--none"])
        assert result.exit_code == 0, result.output
        assert session_mod.load_session("default")["active_profile"] is None

    def test_use_no_arg_no_tty_errors(self, crm_home):
        self._seed("a")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "use"])
        # no name + no TTY -> clean error, never opens a picker
        assert result.exit_code in (1, 2), result.output


class TestListEditRm:
    def _seed(self, name):
        from crm.utils.d365_backend import ConnectionProfile
        session_mod.save_profile(ConnectionProfile(
            name=name, url=f"https://{name}.contoso.local/o",
            domain="C", username="u"))

    def test_list_marks_active(self, crm_home):
        self._seed("a"); self._seed("b")
        runner = CliRunner()
        runner.invoke(cli, ["--json", "profile", "use", "a"])
        result = runner.invoke(cli, ["--json", "profile", "list"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)["data"]
        active = {row["name"]: row["active"] for row in data}
        assert active["a"] is True and active["b"] is False

    def test_edit_changes_url(self, crm_home):
        self._seed("a")
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "profile", "edit", "a",
            "--url", "https://new.contoso.local/o2"])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile("a").url == "https://new.contoso.local/o2"

    def test_rm_deletes_profile_and_secret(self, crm_home):
        self._seed("a")
        session_mod.save_profile_secret_plaintext("a", "pw")
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "profile", "rm", "a", "--yes"])
        assert result.exit_code == 0, result.output
        assert "a" not in session_mod.list_profiles()

    def test_rm_active_clears_session_pointer(self, crm_home):
        self._seed("a")
        runner = CliRunner()
        runner.invoke(cli, ["--json", "profile", "use", "a"])
        runner.invoke(cli, ["--json", "profile", "rm", "a", "--yes"])
        assert session_mod.load_session("default")["active_profile"] is None


class TestSetDeletePassword:
    def _seed(self, name="a"):
        from crm.utils.d365_backend import ConnectionProfile
        session_mod.save_profile(ConnectionProfile(
            name=name, url=f"https://{name}.contoso.local/o",
            domain="C", username="u"))

    def test_set_password_stores_plaintext_when_no_keyring(self, crm_home):
        self._seed()
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "profile", "set-password", "--profile", "a",
            "--password", "pw"])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile_secret("a") == "pw"

    def test_delete_password_removes_secret(self, crm_home):
        self._seed()
        session_mod.save_profile_secret_plaintext("a", "pw")
        runner = CliRunner()
        result = runner.invoke(cli, [
            "--json", "profile", "delete-password", "--profile", "a"])
        assert result.exit_code == 0, result.output
        assert session_mod.load_profile_secret("a") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest crm/tests/test_profile_cmd.py -v`
Expected: FAIL — `No such command 'profile'`.

- [ ] **Step 3: Implement `crm/commands/profile.py`**

```python
"""`crm profile` — create, switch, and manage connection profiles."""
# pyright: basic
from __future__ import annotations

import click

from crm.cli import CLIContext, FAILURE_EXIT_CODE, pass_ctx, _stdin_is_tty
from crm.core import connection as conn_mod
from crm.core import keyring_store
from crm.core import session as session_mod
from crm.commands._helpers import (
    _handle_d365_error,
    _plaintext_secret_warning,
    _confirm_destructive,
    infer_auth_scheme,
    default_profile_name,
    select_one,
)
from crm.utils.d365_backend import ConnectionProfile, D365Backend, D365Error


@click.group("profile")
def profile_group():
    """Create, switch, and manage connection profiles."""


# ── add ───────────────────────────────────────────────────────────────────

@profile_group.command("add")
@click.option("--url", default=None, help="Server URL, e.g. https://crm.contoso.local/org "
              "or https://org.crm.dynamics.com")
@click.option("--name", "name_opt", default=None, help="Profile name (default: URL host label).")
@click.option("--auth-scheme", "auth_opt",
              type=click.Choice(["ntlm", "kerberos", "negotiate", "oauth"]),
              default=None, help="Override the auth scheme inferred from the URL.")
@click.option("--username", default=None, help="NTLM: username.")
@click.option("--domain", default=None, help="NTLM: AD domain (blank for UPN).")
@click.option("--tenant-id", default=None, help="OAuth: Azure AD tenant id.")
@click.option("--client-id", default=None, help="OAuth: application (client) id.")
@click.option("--password", "password_opt", default=None,
              help="Secret (NTLM password or OAuth client secret). Prompted if omitted on a TTY.")
@click.option("--api-version", default=None,
              help="Web API version. Omit to auto-negotiate (v9.2 → v9.1 on on-prem).")
@click.option("--no-verify-ssl", is_flag=True, help="Skip SSL certificate verification.")
@click.option("--default-solution", default=None, help="Default solution uniquename.")
@click.option("--publisher-prefix", default=None, help="Default schema-name prefix, e.g. 'new'.")
@click.option("--store-password-plaintext", is_flag=True,
              help="Force plaintext storage (skip the OS keyring).")
@click.option("--yes", "-y", is_flag=True, help="Skip the overwrite-confirm prompt.")
@pass_ctx
def profile_add(ctx: CLIContext, url, name_opt, auth_opt, username, domain,
                tenant_id, client_id, password_opt, api_version, no_verify_ssl,
                default_solution, publisher_prefix, store_password_plaintext, yes):
    """Create a profile, save its secret, test the connection, and activate it.

    Run with no flags for an interactive wizard; pass flags for scripting/CI.
    """
    interactive = _stdin_is_tty() and not ctx.json_mode
    # ── URL (required) ──
    if not url:
        if not interactive:
            raise click.UsageError("--url is required (no TTY for the wizard).")
        url = click.prompt("Server URL (e.g. https://crm.corp/org or https://org.crm.dynamics.com)")
    auth_scheme = auth_opt or infer_auth_scheme(url)
    if interactive and auth_opt is None:
        auth_scheme = click.prompt(
            "Auth scheme", type=click.Choice(["ntlm", "kerberos", "negotiate", "oauth"]),
            default=auth_scheme)

    # ── scheme-specific fields ──
    if auth_scheme == "oauth":
        if not tenant_id:
            if not interactive:
                raise click.UsageError("--tenant-id is required for an OAuth profile.")
            tenant_id = click.prompt("Azure AD tenant id")
        if not client_id:
            if not interactive:
                raise click.UsageError("--client-id is required for an OAuth profile.")
            client_id = click.prompt("Application (client) id")
        domain = ""
        username = ""
    else:
        if not username:
            if not interactive:
                raise click.UsageError("--username is required for an on-prem profile.")
            username = click.prompt("Username")
        if domain is None:
            domain = click.prompt("AD domain (blank for UPN)", default="", show_default=False) \
                if interactive else ""

    # ── name + secret ──
    name = name_opt or (
        click.prompt("Profile name", default=default_profile_name(url))
        if interactive else default_profile_name(url))
    secret = password_opt
    if not secret and interactive:
        label = "Client secret" if auth_scheme == "oauth" else "Password"
        secret = click.prompt(label, hide_input=True, default="", show_default=False) or None
    if not secret:
        raise click.UsageError("--password is required (no TTY to prompt for it).")

    if name in session_mod.list_profiles() and not yes:
        if not _confirm_destructive("profile", name, yes,
                                    message=f"Profile {name!r} exists. Overwrite?"):
            ctx.emit(False, error="aborted by user")
            return

    negotiate = api_version is None
    profile = ConnectionProfile(
        name=name, url=url, domain=domain or "", username=username or "",
        api_version=api_version or conn_mod.DEFAULT_API_VERSION,
        verify_ssl=not no_verify_ssl, auth_scheme=auth_scheme,
        tenant_id=tenant_id, client_id=client_id,
        default_solution=default_solution, publisher_prefix=publisher_prefix,
    )
    session_mod.save_profile(profile)

    # store secret (keyring → plaintext fallback)
    try:
        where = conn_mod.save_secret(name, secret, force_plaintext=store_password_plaintext)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if where == "plaintext" and not store_password_plaintext:
        ctx.skin.warning("OS keyring unavailable — " + _plaintext_secret_warning())
    elif where == "plaintext":
        ctx.skin.warning(_plaintext_secret_warning())

    # confirm-by-doing: live WhoAmI (+ api_version negotiation)
    ctx.profile_name = name
    ctx.password = secret
    ctx.invalidate_backend()
    try:
        info = conn_mod.test_connection(ctx.backend(), negotiate=negotiate)
    except D365Error as exc:
        _handle_d365_error(ctx, exc, hint="profile saved; fix creds then re-run `crm profile add`")
        return
    if info["api_version"] != profile.api_version:
        profile.api_version = info["api_version"]
        session_mod.save_profile(profile)

    state = session_mod.load_session(ctx.session_name)
    state["active_profile"] = name
    session_mod.save_session(state, ctx.session_name)
    ctx.invalidate_backend()
    data = {
        "profile": name, "auth_scheme": auth_scheme,
        "credential_storage": where, "active": True,
        "user_id": info.get("user_id"), "api_version": info["api_version"],
    }
    ctx.emit(True, data=data, meta={"profile": name})


# ── use ────────────────────────────────────────────────────────────────────

@profile_group.command("use")
@click.argument("name", required=False)
@click.option("--none", "clear", is_flag=True, help="Clear the active profile.")
@pass_ctx
def profile_use(ctx: CLIContext, name, clear):
    """Switch the active profile. No argument shows an interactive picker."""
    state = session_mod.load_session(ctx.session_name)
    if clear:
        state["active_profile"] = None
        session_mod.save_session(state, ctx.session_name)
        ctx.profile_name = None
        ctx.password = None
        ctx.invalidate_backend()
        ctx.emit(True, data={"active_profile": None})
        return

    names = session_mod.list_profiles()
    if not name:
        if not names:
            ctx.emit(False, error="No profiles. Run `crm profile add`.")
            return
        try:
            active = state.get("active_profile")
            items = [(n, _use_label(n, active)) for n in names]
            name = select_one("Select profile to activate", items)
        except RuntimeError:
            ctx.emit(False, error="profile name required (no TTY for the picker); "
                     "see `crm profile list`.")
            return
        if not name:  # cancelled
            ctx.emit(False, error="no profile selected")
            return

    if name not in names:
        _handle_d365_error(ctx, D365Error(f"Profile {name!r} not found."))
        return
    state["active_profile"] = name
    session_mod.save_session(state, ctx.session_name)
    ctx.profile_name = name
    ctx.password = None
    ctx.invalidate_backend()
    ctx.emit(True, data={"active_profile": name})


def _use_label(name: str, active: str | None) -> str:
    try:
        p = session_mod.load_profile(name)
        target = "cloud" if p.auth_scheme == "oauth" else "on-prem"
        flag = "  (active)" if name == active else ""
        return f"{name}  {target}  {p.url}{flag}"
    except FileNotFoundError:
        return name


# ── list ───────────────────────────────────────────────────────────────────

def _credential_storage(name: str) -> str:
    if session_mod.load_profile_secret(name) is not None:
        return "plaintext"
    if keyring_store.has_secret(name):
        return "keyring"
    return "none"


@profile_group.command("list")
@pass_ctx
def profile_list(ctx: CLIContext):
    """List saved profiles; the active one is marked."""
    names = session_mod.list_profiles()
    active = session_mod.load_session(ctx.session_name).get("active_profile")
    rows = []
    for n in names:
        try:
            p = session_mod.load_profile(n)
            rows.append({
                "name": n, "active": n == active,
                "target": "cloud" if p.auth_scheme == "oauth" else "on-prem",
                "url": p.url, "credential_storage": _credential_storage(n),
                "default_solution": p.default_solution,
                "publisher_prefix": p.publisher_prefix,
            })
        except FileNotFoundError:
            rows.append({"name": n, "active": n == active,
                        "credential_storage": _credential_storage(n)})
    if ctx.json_mode:
        ctx.emit(True, data=rows)
        return
    ctx.skin.section("Profiles")
    if not rows:
        ctx.skin.hint("(none) — run `crm profile add`")
    for r in rows:
        mark = "● " if r.get("active") else "○ "
        ctx.skin.status(mark + r["name"],
                        f"{r.get('target','?')}  {r.get('url','?')}  "
                        f"cred={r['credential_storage']}")


# ── edit ───────────────────────────────────────────────────────────────────

@profile_group.command("edit")
@click.argument("name")
@click.option("--url", default=None)
@click.option("--username", default=None)
@click.option("--domain", default=None)
@click.option("--tenant-id", default=None)
@click.option("--client-id", default=None)
@click.option("--api-version", default=None)
@click.option("--default-solution", default=None)
@click.option("--publisher-prefix", default=None)
@pass_ctx
def profile_edit(ctx: CLIContext, name, url, username, domain, tenant_id,
                 client_id, api_version, default_solution, publisher_prefix):
    """Change a profile's fields (not its secret — use set-password)."""
    try:
        p = session_mod.load_profile(name)
    except FileNotFoundError:
        _handle_d365_error(ctx, D365Error(f"Profile {name!r} not found."))
        return
    if url is not None: p.url = url.rstrip("/")
    if username is not None: p.username = username
    if domain is not None: p.domain = domain
    if tenant_id is not None: p.tenant_id = tenant_id
    if client_id is not None: p.client_id = client_id
    if api_version is not None: p.api_version = api_version
    if default_solution is not None: p.default_solution = default_solution
    if publisher_prefix is not None: p.publisher_prefix = publisher_prefix
    session_mod.save_profile(p)
    ctx.invalidate_backend()
    ctx.emit(True, data={"profile": name, "updated": True})


# ── rm ─────────────────────────────────────────────────────────────────────

@profile_group.command("rm")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation.")
@pass_ctx
def profile_rm(ctx: CLIContext, name, yes):
    """Delete a profile and its stored secret."""
    if name not in session_mod.list_profiles():
        _handle_d365_error(ctx, D365Error(f"Profile {name!r} not found."))
        return
    if not _confirm_destructive("profile", name, yes):
        ctx.emit(False, error="aborted by user")
        return
    keyring_store.delete_secret(name)
    session_mod.clear_profile_secret(name)
    session_mod.delete_profile(name)
    state = session_mod.load_session(ctx.session_name)
    if state.get("active_profile") == name:
        state["active_profile"] = None
        session_mod.save_session(state, ctx.session_name)
        ctx.profile_name = None
        ctx.invalidate_backend()
    ctx.emit(True, data={"profile": name, "removed": True})


# ── set-password / delete-password (moved from `connection`) ─────────────────

@profile_group.command("set-password")
@click.option("--profile", "profile_name", required=True, help="Profile to store the secret for.")
@click.option("--password", "password_opt", default=None, help="Secret to store (else prompted on a TTY).")
@click.option("--store-password-plaintext", is_flag=True, help="Force plaintext storage.")
@pass_ctx
def profile_set_password(ctx: CLIContext, profile_name, password_opt, store_password_plaintext):
    """Store/replace the secret for an existing profile."""
    try:
        profile = session_mod.load_profile(profile_name)
    except FileNotFoundError:
        _handle_d365_error(ctx, D365Error(f"Profile {profile_name!r} not found."))
        return
    secret = password_opt
    if not secret and _stdin_is_tty() and not ctx.json_mode:
        import getpass
        label = "client secret" if profile.auth_scheme == "oauth" else "password"
        secret = getpass.getpass(f"D365 {label} for profile {profile_name!r}: ") or None
    if not secret:
        ctx.emit(False, error="No secret supplied. Pass --password.")
        return
    try:
        where = conn_mod.save_secret(profile_name, secret, force_plaintext=store_password_plaintext)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if where == "plaintext":
        ctx.skin.warning(_plaintext_secret_warning())
    ctx.emit(True, data={"profile": profile_name, "stored": True, "to": where})


@profile_group.command("delete-password")
@click.option("--profile", "profile_name", required=True, help="Profile whose secret to remove.")
@pass_ctx
def profile_delete_password(ctx: CLIContext, profile_name):
    """Remove a stored secret (OS keyring AND plaintext)."""
    removed_keyring = keyring_store.delete_secret(profile_name)
    removed_plaintext = session_mod.clear_profile_secret(profile_name)
    removed = removed_keyring or removed_plaintext
    where = []
    if removed_keyring: where.append("keyring")
    if removed_plaintext: where.append("plaintext")
    ctx.emit(True, data={"profile": profile_name, "removed": removed, "from": where},
             meta=({"note": "no stored secret found"} if not removed else None))
```

- [ ] **Step 4: Wire the group into the registry**

In `crm/cli.py` `_lazy_commands` (line ~306), replace the `init` entry with `profile` (keep alphabetical order — `profile` goes after `plugin`):

```python
        "plugin": "crm.commands.plugin:plugin_group",
        "profile": "crm.commands.profile:profile_group",
        "query": "crm.commands.query:query_group",
```

Delete the `"init": "crm.commands.init:init_cmd",` line.

- [ ] **Step 5: Run the profile-command tests**

Run: `python -m pytest crm/tests/test_profile_cmd.py -v`
Expected: PASS (all classes). If `profile add` hangs, the no-TTY guard is wrong — `CliRunner` stdin is not a TTY so `_stdin_is_tty()` returns False and every prompt path must raise `UsageError` instead.

- [ ] **Step 6: Lint**

Run: `pyright --pythonpath .venv/bin/python --pythonversion 3.9 crm/commands/profile.py`
Expected: 0 errors (basic mode).

- [ ] **Step 7: Commit**

```bash
git add crm/commands/profile.py crm/tests/test_profile_cmd.py crm/cli.py
git commit -m "feat(profile): add 'crm profile' group (add/use/list/edit/rm/set-password)"
```

---

## Task 5: Trim `crm connection` to diagnostics + delete `init`

Remove the migrated/removed commands from `connection.py`; delete `init.py`; update the lazy registry, the lazy-import test, and `crm.spec`.

**Files:**
- Modify: `crm/commands/connection.py`
- Delete: `crm/commands/init.py`
- Modify: `crm/cli.py`, `crm.spec`, `crm/tests/test_lazy_imports.py`
- Delete/rework: `crm/tests/test_crm_init.py`, `crm/tests/test_connection_cmd.py`

- [ ] **Step 1: Strip `connection.py`**

In `crm/commands/connection.py` delete these command functions and their decorators entirely: `connection_connect` (18–119), `connection_profiles` + `_credential_storage` (172–222), `connection_disconnect` (225–237), `connection_delete_password` (240–258), `connection_set_password` (261–315).

Keep: `connection_group`, `connection_status`, `connection_whoami`, `connection_test`, `doctor_command`, and the final `connection_group.add_command(doctor_command)`.

In `connection_test` (154–169) delete the `conn_mod.load_dotenv()` call and the `env_api_version()` use; a loaded profile is always respected as saved, so set `negotiate = False`:

```python
@connection_group.command("test")
@pass_ctx
def connection_test(ctx: CLIContext):
    """Reachability check: WhoAmI + report API base."""
    try:
        info = conn_mod.test_connection(ctx.backend(), negotiate=False)
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    ctx.emit(True, data=info)
```

Drop the now-unused imports at the top: `keyring_store`, `ConnectionProfile`, `_plaintext_secret_warning` (keep `_handle_d365_error`). Verify with the lint step.

- [ ] **Step 2: Delete `init.py` and update the registry + spec**

```bash
git rm crm/commands/init.py
```

In `crm/cli.py`: the `init` entry was already removed in Task 4 Step 4. Also update the root `--password` help text (line 361) and `--auth-scheme` help (372–376) which reference `D365_PASSWORD` / `CRM_AUTH_SCHEME` env vars:

```python
@click.option("--password", help="Secret for this run (overrides the profile's stored secret).")
```
```python
@click.option("--auth-scheme",
              type=click.Choice(["ntlm", "kerberos", "negotiate", "oauth"]),
              default=None,
              help="Override the active profile's auth scheme for this run. "
                   "ntlm/kerberos/negotiate = on-prem; oauth = cloud.")
```

In `crm/cli.py` line 429, drop the `CRM_AUTH_SCHEME` env read (env break):

```python
    cli_ctx.auth_scheme = auth_scheme
```

In `crm.spec` `hiddenimports`: replace `"crm.commands.init"` with `"crm.commands.profile"`.

In `crm/tests/test_lazy_imports.py` `LAZY_MODULES` (line 15–24): replace `"crm.commands.init"` with `"crm.commands.profile"`.

- [ ] **Step 3: Delete/rework the dead command tests**

```bash
git rm crm/tests/test_crm_init.py
```

In `crm/tests/test_connection_cmd.py`: delete every test exercising `connection connect` / `connection profiles` / `connection disconnect` / `connection set-password` / `connection delete-password` (those behaviors now live in `test_profile_cmd.py`). Keep tests for `connection status` / `whoami` / `test` if present. If the file becomes empty, `git rm` it.

In `crm/tests/test_active_profile_restore.py`: it sets `D365_*` env vars to build an env-derived backend. Rework each test to instead `session_mod.save_profile(...)` + `save_profile_secret_plaintext(...)` + `profile use`, then assert restoration. (Pattern: see `test_profile_cmd.py::TestUse._seed`.)

- [ ] **Step 4: Run the lazy-import guard + connection tests**

Run: `python -m pytest crm/tests/test_lazy_imports.py crm/tests/test_connection_cmd.py crm/tests/test_active_profile_restore.py -v`
Expected: PASS. The `test_every_lazy_command_target_resolves` and `test_lazy_command_modules_are_bundled_in_pyinstaller_spec` checks confirm `profile` is wired + bundled and `init` is gone.

- [ ] **Step 5: Lint**

Run: `pyright --pythonpath .venv/bin/python --pythonversion 3.9 crm/commands/connection.py crm/cli.py`
Expected: 0 errors (no unused-import warnings).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor(connection): keep only diagnostics; remove init + migrated verbs"
```

---

## Task 6: Auto-launch the wizard on first use (TTY only)

When a connection command runs with no profile resolvable, drop into `crm profile add` on a TTY, or emit a clean error under `--json`/no-TTY.

**Files:**
- Modify: `crm/cli.py` (`backend()`)
- Test: `crm/tests/test_profile_cmd.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `crm/tests/test_profile_cmd.py`:

```python
class TestAutoLaunch:
    def test_no_profile_json_mode_errors_no_hang(self, crm_home):
        # whoami with no profile, under --json (no TTY) -> clean error, exit 1.
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "connection", "whoami"])
        assert result.exit_code == 1, result.output
        payload = json.loads(result.output)
        assert payload["ok"] is False
        assert "crm profile add" in payload["error"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest crm/tests/test_profile_cmd.py::TestAutoLaunch -v`
Expected: it likely already errors but with the wrong message (the raw `resolve_credentials` "No profile configured" text). Confirm the message includes `crm profile add` — if it does, this test passes once Task 3's message is in place. If a TTY-launch path is needed, continue.

- [ ] **Step 3: Add the auto-launch seam in `backend()`**

In `crm/cli.py` `backend()` (after computing `effective_profile`, before `resolve_credentials`, ~line 152):

```python
        if effective_profile is None:
            # No profile resolvable. On a TTY, drop into the setup wizard so a
            # first-time user goes zero-to-working; under --json / no-TTY, fail
            # cleanly (resolve_credentials raises the actionable message).
            if _stdin_is_tty() and not self.json_mode:
                import click as _click
                from crm.commands.profile import profile_add
                _click.echo("No profile configured yet. Let's set one up:")
                _click.get_current_context().invoke(profile_add)
                state = session_mod.load_session(self.session_name)
                effective_profile = state.get("active_profile")
```

(The subsequent `resolve_credentials(effective_profile, ...)` still raises the clean error when `effective_profile` is None — the no-TTY path.)

- [ ] **Step 4: Run the test + the full profile suite**

Run: `python -m pytest crm/tests/test_profile_cmd.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add crm/cli.py crm/tests/test_profile_cmd.py
git commit -m "feat(cli): auto-launch profile wizard on first use (TTY), clean error otherwise"
```

---

## Task 7: Wire actionable auth-error hints

Thread `_auth_error_hint` into the D365 error handler so 401/no-secret failures print the fix.

**Files:**
- Modify: `crm/commands/_helpers.py` (`_handle_d365_error`)
- Test: `crm/tests/test_profile_helpers.py` (already covers `_auth_error_hint`); add an integration assertion in `test_profile_cmd.py`

- [ ] **Step 1: Write the failing test**

Append to `crm/tests/test_profile_cmd.py`:

```python
class TestAuthHints:
    def test_401_whoami_prints_set_password_hint(self, crm_home):
        from crm.utils.d365_backend import ConnectionProfile
        session_mod.save_profile(ConnectionProfile(
            name="cloud", url="https://org.crm.dynamics.com",
            domain="", username="", auth_scheme="oauth",
            tenant_id="t", client_id="c"))
        session_mod.save_profile_secret_plaintext("cloud", "badsecret")
        runner = CliRunner()
        with requests_mock.Mocker() as m:
            m.get(requests_mock.ANY, status_code=401, json={"error": {"message": "unauthorized"}})
            result = runner.invoke(cli, ["--json", "--profile", "cloud", "connection", "whoami"])
        assert result.exit_code == 1, result.output
        payload = json.loads(result.output)
        assert "set-password" in (payload.get("meta", {}).get("hint", "")
                                  or payload.get("error", ""))
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest crm/tests/test_profile_cmd.py::TestAuthHints -v`
Expected: FAIL — no hint emitted.

- [ ] **Step 3: Thread the hint into `_handle_d365_error`**

In `crm/commands/_helpers.py` `_handle_d365_error` (55–74), after computing `category`, derive an auth hint when the caller didn't pass one. The active profile name comes from the backend or ctx:

```python
def _handle_d365_error(ctx: "CLIContext", exc: D365Error, *, hint: str | None = None) -> None:
    from crm.utils.d365_backend import classify_d365_error
    category, retryable = classify_d365_error(exc.status, exc.code, str(exc))
    meta: dict[str, Any] = {
        "status": exc.status,
        "code": exc.code,
        "category": category,
        "retryable": retryable,
    }
    # Auto-derive an auth fix-it hint (401 / no-secret) when the caller gave none.
    if hint is None and exc.status == 401:
        backend = getattr(ctx, "_backend", None)
        pname = (getattr(getattr(backend, "profile", None), "name", None)
                 or ctx.profile_name or "<name>")
        hint = _auth_error_hint(exc.status, pname)
    if exc.completed_steps is not None:
        meta["completed_steps"] = exc.completed_steps
    if exc.stage is not None:
        meta["failed_stage"] = exc.stage
    if hint and ctx.json_mode:
        meta["hint"] = hint
    message = f"{exc}\nHint: {hint}" if hint else str(exc)
    ctx.emit(False, error=message, meta=meta)
```

Add `_auth_error_hint` to the imports already present in `_helpers.py` (it's defined in the same module — no import needed; just ensure it's defined above `_handle_d365_error` or reference works since both are module-level).

- [ ] **Step 4: Run the test**

Run: `python -m pytest crm/tests/test_profile_cmd.py::TestAuthHints -v`
Expected: PASS.

- [ ] **Step 5: Run the whole suite to catch regressions in error envelopes**

Run: `python -m pytest crm/tests/ -x -q`
Expected: PASS. (`_handle_d365_error` is shared widely; the 401-only guard keeps non-401 envelopes byte-identical.)

- [ ] **Step 6: Commit**

```bash
git add crm/commands/_helpers.py crm/tests/test_profile_cmd.py
git commit -m "feat(profile): emit set-password fix hint on 401 auth failures"
```

---

## Task 8: Update the shipped skill (SKILL.md + reference)

The skill is the agent-facing source of truth and ships standalone — inline everything, never link repo paths ([[CLAUDE.md skill rules]]).

**Files:**
- Modify: `crm/skills/SKILL.md`, `crm/skills/reference/*.md` (whichever cover connection/setup)

- [ ] **Step 1: Find the stale references**

Run: `grep -rn "connection connect\|crm init\|load_dotenv\|D365_PASSWORD\|CRM_PASSWORD\|\.env\|D365_AUTH\|store-password" crm/skills/`
Expected: a list of lines to fix.

- [ ] **Step 2: Rewrite the setup/connection guidance**

Replace every `crm init` / `crm connection connect` workflow with `crm profile add`. Replace the credential-storage explanation with: "credentials are saved by default (OS keyring, or 0600 plaintext fallback); there is no `.env` or `D365_*`/`CRM_*` env reading." Update the command list: `crm profile add|use|list|edit|rm|set-password|delete-password`; `crm connection whoami|test|doctor|status`. State only what `--help` can't (the no-`.env` contract, the keyring→plaintext fallback, that `use` with no arg opens a picker).

- [ ] **Step 3: Verify no stale refs remain**

Run: `grep -rn "connection connect\|crm init\|load_dotenv\|\.env\|D365_AUTH" crm/skills/`
Expected: empty (or only legitimate mentions, e.g. explaining the removal).

- [ ] **Step 4: Commit**

```bash
git add crm/skills/
git commit -m "docs(skill): retarget setup to 'crm profile', drop .env/env-var guidance"
```

---

## Task 9: Update user docs (CI gate: mkdocs build --strict)

**Files:**
- Rewrite: `docs/getting-started/configure.md`, `docs/getting-started/initialize.md`
- Split: `docs/how-to/connection.md` → add `docs/how-to/profile.md`, slim `connection.md`
- Modify: `docs/reference/cli.md`, `README.md`, `CLAUDE.md`, `mkdocs.yml`

- [ ] **Step 1: Create `docs/how-to/profile.md`**

Document `crm profile add` (wizard + scriptable), `use` (picker + name + `--none`), `list`, `edit`, `rm`, `set-password`, `delete-password`. Show the keyring→plaintext fallback and the inferred-auth-from-URL behavior. Use `Contoso` placeholders only.

- [ ] **Step 2: Slim `docs/how-to/connection.md`** to only `whoami` / `test` / `doctor` / `status`, with a top note: "Profile setup moved to `crm profile` — see [Profiles](profile.md)."

- [ ] **Step 3: Rewrite `docs/getting-started/configure.md`** — delete the `.env` and env-var-table sections; lead with `crm profile add`. Rewrite `initialize.md` (titled "First-run setup") around `crm profile add` (it currently references `crm init`).

- [ ] **Step 4: Update `mkdocs.yml` nav** — add `how-to/profile.md` under the How-to section (after the connection entry, line ~108); retitle the `configure.md` nav label from "Auth (NTLM / OAuth) and env vars" to "Auth (NTLM / OAuth) and profiles" (lines 78, 104).

- [ ] **Step 5: Update `docs/reference/cli.md`, `README.md`, `CLAUDE.md`** — replace `crm init` / `connection connect` mentions; update the storage-strategy description; in `CLAUDE.md` note the env break.

- [ ] **Step 6: Build docs strict**

Run: `mkdocs build --strict`
Expected: build OK, zero warnings (broken links / stale refs fail the build).

- [ ] **Step 7: Commit**

```bash
git add docs/ README.md CLAUDE.md mkdocs.yml
git commit -m "docs: retarget setup to 'crm profile'; remove .env/env-var docs"
```

---

## Task 10: Full verification + breaking release commit

**Files:** none (verification + the version-bumping commit subject).

- [ ] **Step 1: Generic-repo scan**

Run: `git grep -ni "moce\|internalcrm.moce\|b948cd5f"`
Expected: empty (no real org values — [[feedback-keep-repo-generic]]).

- [ ] **Step 2: Full test suite**

Run: `python -m pytest crm/tests/ -q`
Expected: PASS, no skips beyond the live-E2E set (which need creds). If any test still sets `D365_URL`/`CRM_BASE_URL`/`CRM_DOTENV` to build a backend, rework it to the save_profile pattern (Task 3/5).

- [ ] **Step 3: Full lint**

Run: `pyright --pythonpath .venv/bin/python --pythonversion 3.9`
Expected: 0 errors (CI lints the whole tree).

- [ ] **Step 4: Smoke-test the binary path imports (frozen-build guard)**

Run: `python -m pytest crm/tests/test_lazy_imports.py -v`
Expected: PASS — confirms `profile` is registered + in `crm.spec` and `init` is fully gone.

- [ ] **Step 5: Manual smoke test (real interactive run)**

Run (TTY): `python -m crm profile add` and walk the wizard against a real/dev server; then `python -m crm profile list`, `python -m crm profile use`, `python -m crm connection whoami`. Confirm the keyring/plaintext line prints and the WhoAmI identity shows. (Per [[feedback-verify-real-path-not-just-mock]] — mocks hide keyring/getpass/TTY behavior.)

- [ ] **Step 6: The breaking-change commit**

If all prior commits used non-breaking subjects, make the version bump explicit with a final marker commit (empty or a trivial doc touch), OR amend the Task 5 connection commit. Simplest: a dedicated marker.

```bash
git commit --allow-empty -m "feat!: profile-first credential UX; remove init/connect and .env

BREAKING CHANGE: 'crm init' and 'crm connection connect' are removed —
use 'crm profile add'. Credentials and connection config now come only from
saved profiles (or --password); .env autoload and all D365_*/CRM_* credential
env vars are no longer read. CRM_HOME is the only retained env knob."
```

PSR cuts **v2.0.0** on the next push to `main`.

- [ ] **Step 7: Push (only when the user asks)**

Per the project convention this is a substantial feature → branch + PR + Copilot review, not a direct `main` push ([[feedback-commit-direct-to-main]]). Confirm with the user before pushing; then follow the Copilot review loop ([[feedback-monitor-copilot-review]]).

---

## Self-review (filled)

**Spec coverage:**
- `crm profile` group (add/use/list/edit/rm/set-password/delete-password) → Tasks 4. ✓
- Save-by-default, keyring→plaintext fallback, report store → Task 3 (`save_secret`) + Task 4 (`add`). ✓
- Picker on `use` (no arg) + direct name + `--none` → Task 2 + Task 4. ✓
- Clean break: no `.env`, no `D365_*`/`CRM_*`, no `D365_AUTH` → Task 3 (core) + Task 5 (`CRM_AUTH_SCHEME` env read, root help). ✓
- Infer auth from URL, smart defaults, confirm-by-doing → Task 1 + Task 4. ✓
- Auto-launch on first use (TTY) / clean error (JSON) → Task 6. ✓
- Actionable auth hints → Task 1 + Task 7. ✓
- Remove `init` + `connection connect`; group split → Task 5. ✓
- `crm.spec` + lazy registry + sync tests → Task 4/5. ✓
- Skill + docs in same change → Task 8 + Task 9. ✓
- Test rework (env-path tests) → Task 3/5; E2E fixture noted (live-creds tests rework to save_profile). ✓
- Breaking → v2.0.0 → Task 10. ✓

**Placeholder scan:** every code step shows full code; the only `<name>` is a deliberate fallback string. No TBD/TODO.

**Type consistency:** `save_secret(name, secret, *, force_plaintext)` → 'keyring'|'plaintext' used identically in core, `add`, `set-password`. `resolve_credentials(profile_name, password_override, *, allow_prompt)` signature unchanged from today (callers in `cli.py` untouched). `select_one(title, items)` → value|None, used in `profile use`. `infer_auth_scheme`/`default_profile_name`/`_auth_error_hint` signatures match across Tasks 1, 4, 7.

**Note on E2E live tests:** `test_full_e2e.py` needs live creds and currently reads env. Reworking it to build a temp-`CRM_HOME` profile from CI secrets is part of Task 10 Step 2's "rework any remaining env-deriving test"; if it's gated behind a live-creds marker it stays skipped in normal CI and need not block this change.
