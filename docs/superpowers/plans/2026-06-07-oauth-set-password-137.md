# Plan: `crm connection set-password` (issue #137)

**Branch:** `feat/oauth-set-password-137` (off `origin/main` @ v1.0.0)
**Issue:** #137 — OAuth profiles cannot store their client secret via the CLI (configure-once gap)
**Commit subject (drives PSR bump):** `feat(connection): add 'set-password' to store a secret for any profile (#137)`

## Problem

`resolve_credentials` already reads a stored secret scheme-agnostically (override → env →
on-disk plaintext `_secret` → OS keyring → TTY prompt → raise). But the only command that
*writes* a store is `connection connect`, which always builds an NTLM-shaped profile (no
`--tenant-id`/`--client-id`). `init` builds a full OAuth profile but discards the entered
secret. So there is **no CLI path to put an OAuth client secret into the keyring/plaintext
store** — OAuth users must export `D365_CLIENT_SECRET` every run.

## Fix

Add a scheme-agnostic writer `connection set-password --profile NAME [--store-password |
--store-password-plaintext]` that stores a secret for an **already-existing** profile
without rebuilding it. Storage-side mirror of `connection delete-password`.

## Design decisions (grounded in the current code)

- **Secret source must NOT read the on-disk store.** Reusing `resolve_credentials` would let
  an already-stored secret re-store as a silent no-op. So add a *new* core resolver that
  stops at env (no plaintext/keyring read).
- **Core vs command split (architecture rule):** credential-resolution logic lives in
  `crm/core/connection.py` (pyright **strict**); the Click command in
  `crm/commands/connection.py` stays a thin wrapper (basic mode). Precedent: `env_api_version()`
  exists precisely so command modules don't reach into the private `_env()`.
- **Single-store invariant (must preserve):** a profile carries at most one store; plaintext
  shadows keyring in resolution. So storing to keyring clears any plaintext `_secret`; storing
  to plaintext deletes any keyring entry. Mirror `connect`'s exact order: write the new store
  first (the one that can fail), then clear the other.
- **Missing profile** → status-less `D365Error` routed through `_handle_d365_error` →
  `meta.category == "validation"`, exit 1, `ok=false` (identical to `connect`'s missing-profile
  envelope; `classify_d365_error(None, …)` returns `("validation", False)`).
- **Default store** (neither flag) → keyring. **Both flags** → `click.UsageError`, exit 2.
- **Overwrite** → silent (idempotent set; no confirm — not destructive like `delete`).

## Reference: exact current code

- `connect` store-flag block: `crm/commands/connection.py:46-123` (mutual-exclusion check;
  `keyring_store.set_secret` then `session_mod.clear_profile_secret`; plaintext path =
  `save_profile_secret_plaintext` then `keyring_store.delete_secret` + Windows warning).
- `delete-password` (shape to mirror): `crm/commands/connection.py:250-268`.
- `resolve_credentials` + the two no-secret error messages to update:
  `crm/core/connection.py:231-290` (oauth msg lines 277-282 carries the stale
  "Storing OAuth secrets via the CLI is not yet supported; see issue #137." text;
  ntlm msg lines 283-288 names only `connect --store-password`).
- Env helpers/constants: `_env`, `ENV_CLIENT_SECRET`, `ENV_PASSWORD`,
  `crm/core/connection.py:43-77`; `load_dotenv` at line 93.
- Plaintext store helpers: `save_profile_secret_plaintext` / `load_profile_secret` /
  `clear_profile_secret`, `crm/core/session.py:100-133`.
- Keyring store API: `set_secret` (hard-raises `D365Error` when no backend) / `delete_secret`
  (soft bool) / `get_secret` / `is_available`, `crm/core/keyring_store.py`.
- emit/exit conventions: `ctx.emit(False, …)` raises `Exit(1)` (ADR 0001);
  `_handle_d365_error` (`crm/commands/_helpers.py:55-73`) builds the
  `{status,code,category,retryable}` meta and emits failure. `_stdin_is_tty` /
  `FAILURE_EXIT_CODE` in `crm/cli.py`.
- Test patterns: `crm/tests/test_connection_cmd.py` `fake_keyring` fixture (line 135) +
  `TestConnectStoreFlags` (line 148) + `_profile_json` helper; `_WHOAMI`;
  `crm/tests/test_resolve_credentials_keyring.py` (resolver unit tests, `_save`, `fake_keyring`).

---

## Task 1 — Implement `set-password` (core resolver + command + resolver-message updates + tests)

**Goal:** A working `crm connection set-password` plus the new core resolver it uses and the
two resolver error-message corrections, all under test. TDD.

### 1a. New core resolver in `crm/core/connection.py` (strict)

Add next to `resolve_credentials`:

```python
def resolve_secret_for_storage(
    profile: ConnectionProfile,
    password_override: str | None = None,
    *,
    allow_prompt: bool = False,
) -> str:
    """Resolve a secret to STORE for *profile*: override → env (scheme-aware) →
    TTY prompt. Deliberately never reads the on-disk store (plaintext _secret /
    OS keyring) — set-password must not silently re-store an already-stored
    secret. Raises D365Error when nothing resolves."""
    load_dotenv()
    is_oauth = profile.auth_scheme == "oauth"
    secret = password_override or (
        _env(ENV_CLIENT_SECRET) if is_oauth else _env(ENV_PASSWORD)
    )
    if not secret and allow_prompt:
        import getpass
        label = "client secret" if is_oauth else "password"
        secret = getpass.getpass(f"D365 {label} for profile {profile.name!r}: ") or None
    if not secret:
        var = ENV_CLIENT_SECRET if is_oauth else ENV_PASSWORD
        label = "client secret" if is_oauth else "password"
        raise D365Error(
            f"No {label} supplied to store. Pass --password or set {var} "
            f"(or its CRM_ alias) in the environment / .env."
        )
    return secret
```

### 1b. Update the two no-secret messages in `resolve_credentials`

- **oauth** (lines 277-282): drop the "not yet supported; see issue #137" sentence; point at
  the new command. Must still contain `D365_CLIENT_SECRET` (test_oauth_auth.py asserts it):
  ```python
  raise D365Error(
      f"No client secret supplied. Set {ENV_CLIENT_SECRET} (or CRM_CLIENT_SECRET) "
      "in the environment / .env, pass --password, or store it once with "
      "`crm connection set-password --profile <name> --store-password` (OS keyring) "
      "/ --store-password-plaintext."
  )
  ```
- **ntlm** (lines 283-288): add `set-password` alongside the existing `connect --store-password`.
  Must still contain `--store-password` (test_resolve_credentials_keyring.py:80 regex):
  ```python
  raise D365Error(
      f"No password supplied. Set {ENV_PASSWORD} (or CRM_PASSWORD) in the "
      "environment / .env, pass --password, or store it once with "
      "`crm connection connect --store-password` / "
      "`crm connection set-password --store-password` (OS keyring) / "
      "--store-password-plaintext."
  )
  ```

### 1c. New command in `crm/commands/connection.py`

Add after `connection_delete_password`. Thin wrapper; mirror `connect`'s flag handling and the
plaintext warning verbatim:

```python
@connection_group.command("set-password")
@click.option("--profile", "profile_name", required=True,
              help="Profile to store the secret for (must already exist).")
@click.option("--password", "password_opt",
              help="Secret to store (else env D365_CLIENT_SECRET/D365_PASSWORD per the "
                   "profile's auth scheme, else a TTY prompt).")
@click.option("--store-password", is_flag=True,
              help="Store the secret in the OS keyring (default when neither flag is "
                   "given). Needs the 'crm[keyring]' extra.")
@click.option("--store-password-plaintext", is_flag=True,
              help="Headless/CI fallback: write the secret into the profile file "
                   "(0600 on POSIX; perms unenforced on Windows). Emits a warning.")
@pass_ctx
def connection_set_password(ctx, profile_name, password_opt,
                            store_password, store_password_plaintext):
    """Store a secret (OAuth client secret or NTLM password) for an existing profile.

    Storage-side mirror of `connection delete-password`. Does not contact the server
    and does not rebuild the profile — it only writes the secret into the chosen store
    (OS keyring by default, or the profile file with --store-password-plaintext), keeping
    a profile's single-store invariant. The secret is read from --password, else the
    scheme's env var, else a TTY prompt; the existing on-disk store is never read.
    """
    if store_password and store_password_plaintext:
        raise click.UsageError(
            "--store-password and --store-password-plaintext are mutually exclusive."
        )
    # Profile must already exist (no active-profile fallback — symmetric with
    # delete-password). A missing profile is an operational failure, not a create path.
    try:
        profile = session_mod.load_profile(profile_name)
    except FileNotFoundError:
        _handle_d365_error(ctx, D365Error(f"Profile {profile_name!r} not found."))
        return
    allow_prompt = _stdin_is_tty() and not ctx.json_mode
    try:
        secret = conn_mod.resolve_secret_for_storage(
            profile, password_override=password_opt, allow_prompt=allow_prompt,
        )
    except D365Error as exc:
        _handle_d365_error(ctx, exc)
        return
    if store_password_plaintext:
        session_mod.save_profile_secret_plaintext(profile_name, secret)
        # Plaintext is now the single store — drop any stale keyring entry.
        keyring_store.delete_secret(profile_name)
        warn = (
            "Stored the secret in PLAINTEXT in the profile file."
            if os.name != "posix"
            else "Stored the secret in PLAINTEXT in the profile file (0600)."
        )
        if os.name != "posix":
            warn += (" On Windows file permissions are NOT enforced — prefer "
                     "--store-password (Credential Manager).")
        ctx.skin.warning(warn)
        where = "plaintext"
    else:
        # Keyring (explicit or default). set_secret hard-raises if no backend.
        try:
            keyring_store.set_secret(profile_name, secret)
        except D365Error as exc:
            _handle_d365_error(ctx, exc)
            return
        # Keyring is now the single store — drop any stale plaintext secret.
        session_mod.clear_profile_secret(profile_name)
        where = "keyring"
    ctx.emit(True, data={"profile": profile_name, "stored": True, "to": where})
```

`D365Error` is already imported in this module. No `cli.py` change — `@connection_group.command`
auto-registers (same as `delete-password`).

### 1d. Tests

**`crm/tests/test_resolve_credentials_keyring.py`** — unit tests for `resolve_secret_for_storage`
(mirror the existing `_save`/`fake_keyring` style; the autouse `_home` fixture already clears the
secret env vars):
- override beats env.
- env scheme-aware: ntlm profile reads `D365_PASSWORD`; oauth profile reads `D365_CLIENT_SECRET`.
- prompt used when `allow_prompt=True` and nothing else (monkeypatch `getpass.getpass`).
- raises `D365Error` when nothing and `allow_prompt=False`.
- **never reads on-disk:** save a plaintext `_secret` AND seed `fake_keyring` for the profile, call
  with no override/env and `allow_prompt=False` → it must **raise** (proves the on-disk store is
  not a source), and assert the stored secret is still intact.

**`crm/tests/test_connection_cmd.py`** — `TestSetPassword` (reuse `fake_keyring`, `_profile_json`,
build a profile first via `session_mod.save_profile(...)` or a `connect` call). Cover every
acceptance criterion:
- no store flag + `--password X` → `fake_keyring[P] == X`; a pre-existing plaintext `_secret` is cleared.
- `--store-password` → keyring set, plaintext cleared.
- `--store-password-plaintext` → `_profile_json[P]["_secret"] == X`, keyring entry deleted, stderr
  contains "plaintext".
- both flags → `exit_code == 2`, "mutually exclusive".
- missing profile → `exit_code == 1`, `ok is False`, `meta.category == "validation"`.
- keyring target, no backend (monkeypatch `is_available`→False and `set_secret`→raises `D365Error`)
  → exit 1, no "Traceback", message mentions keyring.
- secret from env (set `D365_CLIENT_SECRET` for an oauth profile / `D365_PASSWORD` for ntlm),
  no `--password` → stored value is the env value.
- on-disk not a source: profile has a stored keyring secret, run with no `--password`/env and
  non-TTY stdin (CliRunner) → exit 1 (no secret), keyring value unchanged.
- overwrite: set twice, second value wins.
- success envelope: `data == {"profile": P, "stored": True, "to": "keyring"}` (and `"plaintext"` case).
- **#137 round-trip proof:** create an `auth_scheme="oauth"` profile, run
  `set-password --profile cloud --password S --store-password`, then call
  `conn_mod.resolve_credentials("cloud")` (same `fake_keyring` store) with no
  `D365_CLIENT_SECRET` set → `rc.password == "S"`.

### Verify
- `pytest -q` green (new + existing; the two message edits keep existing assertions passing).
- `pyright --pythonpath .venv/bin/python` (or the project's invocation) clean on
  `crm/core/connection.py` (strict).
- `crm connection set-password --help` renders.

### Commit
`feat(connection): add 'set-password' to store a secret for any profile (#137)`

---

## Task 2 — Docs & skill sync (project rule: same change)

**Goal:** Every user-facing surface documents `set-password`; the stale "OAuth secrets can only
come from env/.env" / "not yet supported" guidance is retired. `mkdocs build --strict` passes.

Files (all already mention credential storage / `#137` / OAuth-secret guidance — grep hits):
`crm/skills/SKILL.md`, `docs/how-to/connection.md`, `README.md`,
`docs/getting-started/initialize.md`, `docs/getting-started/configure.md`, and the CLI reference
`docs/reference/cli.md`.

For each:
- Document `connection set-password --profile NAME [--store-password | --store-password-plaintext]`
  as the way to store a secret (OAuth client secret **or** NTLM password) for an existing profile,
  storage-side mirror of `delete-password`.
- Replace any "storing OAuth secrets via the CLI is not supported / only env/.env for OAuth" text
  with: OAuth client secrets can now be stored via `set-password` (keyring or plaintext), same as
  NTLM passwords; env/.env remains a valid source.
- Keep `cli.py`-derived reference (`docs/reference/cli.md`) in sync with the new command + flags.
- Do **not** hand-edit `CHANGELOG.md` (PSR-owned).

### Verify
- `mkdocs build --strict` passes (no warnings/broken links).
- `grep -rn "not yet supported" docs/ crm/skills/ README.md` → no stale OAuth-secret claim remains.
- Spot-check the new command name/flags match Task 1 exactly.

### Commit
`docs(connection): document 'set-password' for storing profile secrets (#137)`

---

## After both tasks
- Final whole-branch code review.
- `superpowers:finishing-a-development-branch` (PR with `feat(connection): … (#137)` subject so
  PSR bumps a minor and generates the CHANGELOG entry).
