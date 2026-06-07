# Configure-once credentials via OS keyring — design

- **Issue:** [#130](https://github.com/Gharib89/crm/issues/130) — `feat(connection): persist credentials via OS keyring (configure-once UX)`
- **Date:** 2026-06-07
- **Status:** approved (design); ready for implementation plan

## Problem

Secrets are never persisted today. The resolver (`crm/core/connection.py:resolve_credentials`)
resolves the one secret a profile needs — the NTLM password, or the OAuth client
secret — in the order: explicit `--password` / `CLIContext.password` → env
(`D365_PASSWORD` / `CRM_PASSWORD`, incl. `.env`; OAuth: `D365_CLIENT_SECRET`) →
otherwise **raise**. There is no keyring lookup and no interactive prompt.
Profiles persist password-free (`crm/core/session.py`); the profile writer does no
`chmod`. Interactive users must therefore re-supply the secret every session,
breaking the "configure once, use everywhere" expectation.

## Goal

Let a user store the secret once at connect time (OS keyring, primary; explicit
plaintext-to-profile-file, headless/CI fallback) and omit it thereafter — the
secret store keyed by profile name, scheme-aware (NTLM password vs OAuth client
secret), with the existing override→env precedence preserved ahead of keyring.

## Resolution model

Two independent axes — **which profile**, and **which secret for that profile**.

### Profile selection — `CLIContext.backend()`

```
--profile flag  >  session active_profile  >  env-derived profile
```

`backend()` gains a lookup of the session's `active_profile` (from
`session_mod.load_session(self.session_name)`) when no `--profile` flag is given.
This is what makes `crm connection whoami` (no flag) use the profile a prior
`connect` saved — across separate shell invocations, not just the REPL.

Decided in brainstorming: **active_profile wins over env** when both are present
(precedence above). This is a deliberate behavior change for a user who ran
`connect` once *and* also sets env connection vars — they now get the saved
profile. Env-only users (no profile ever saved, e.g. CI temp `CRM_HOME`) are
unaffected: with no `active_profile`, resolution falls through to env exactly as
today. `disconnect` clears `active_profile`, so resolution falls back to env.

### Secret resolution — `resolve_credentials()`

```
1. --password / CLIContext.password          (always wins)
2. D365_PASSWORD / CRM_PASSWORD env / .env    (OAuth profile: D365_CLIENT_SECRET / CRM_CLIENT_SECRET)
3. On-disk secret                             (OS keyring entry, OR plaintext _secret in the profile file — see §Plaintext)
4. TTY prompt                                 (getpass; only when caller opts in — TTY and not --json)
   else → raise   (today's error, extended to mention keyring + the storage flags)
```

The stored secret is scheme-aware: the profile already records its `auth_scheme`,
so the keyring entry holds whichever secret that scheme uses (NTLM → password,
OAuth → client secret). The resolver returns it in the same `ResolvedCredentials.password`
field it uses today; no shape change.

Steps 1–2 keep their current precedence. Step 3 only fires when a named profile
is resolved (env-only mode has no profile name to key by). Step 4 is opt-in (see
§"Resolver change").

## Components

### New unit — `crm/core/keyring_store.py` (pyright strict)

Isolates the optional `keyring` dependency behind a small, mockable interface so
the resolver stays clean and tests never touch a live keyring backend.

```python
KEYRING_SERVICE = "crm"

def is_available() -> bool          # keyring importable AND a usable backend present
def get_secret(profile_name: str) -> str | None
def set_secret(profile_name: str, secret: str) -> None
def delete_secret(profile_name: str) -> bool   # True if an entry existed and was removed
def has_secret(profile_name: str) -> bool       # for the `profiles` storage-type report
```

- `keyring` is imported lazily inside these functions. Import failure (extra not
  installed) or no-usable-backend surfaces a graceful, actionable `D365Error`
  ("install `crm[keyring]`, or use `--store-password-plaintext` / env") — never a
  traceback. `is_available()` / `has_secret()` swallow the absence and return
  `False` (so `profiles` reporting and detection degrade quietly); the write/read
  paths (`set_secret`, `get_secret` when explicitly requested) raise the
  actionable error.
- Detection (`has_secret`) does one `keyring.get_password` per profile. On Windows
  Credential Manager and SecretService a read does not prompt; the maintainer
  verifies the Windows round-trip directly.

### Resolver change — `crm/core/connection.py`

`resolve_credentials(profile_name, password_override, *, allow_prompt: bool = False)`.

After the env step and before the existing `raise`:

1. `keyring_store.get_secret(profile.name)` — use it if non-empty.
2. If still empty and `allow_prompt`: `getpass.getpass(...)` for the secret.
3. Else raise the existing clear error, extended to mention keyring + the
   `--store-password` / `--store-password-plaintext` flags.

`allow_prompt` defaults **False** so core stays non-interactive for library
callers and tests. `CLIContext.backend()` passes `allow_prompt = _stdin_is_tty()
and not self.json_mode`, keeping the interactive prompt opt-in at the CLI
boundary while satisfying "the resolver gains steps 3–4." A `getpass` on a
keyring-read path inside core is acceptable because the gate lives with the
caller, not in core's default.

### Plaintext fallback — Approach B (profile JSON, secret kept off the dataclass)

`ConnectionProfile` stays secret-free (preserving its documented "no secrets"
invariant). The plaintext secret is stored as a separate `_secret` key in the
**same** profile JSON file, written and read directly by `session.py`, bypassing
the dataclass round-trip:

```python
# crm/core/session.py
def save_profile_secret_plaintext(name: str, secret: str) -> Path  # merges {"_secret": secret} into the profile JSON, chmod 0600 on POSIX
def load_profile_secret(name: str) -> str | None                   # reads "_secret" from the file, else None
def clear_profile_secret(name: str) -> bool                        # removes "_secret", rewrites file; True if one was present
```

- `connection status` and `connection profiles` build their output from the
  `ConnectionProfile` dataclass (`p.to_dict()`), which has no secret field — so
  they **structurally cannot** leak `_secret`. No redaction logic to maintain or
  get wrong.
- `_secret` is written with `0600` on POSIX (`os.chmod` on the profile file after
  the atomic write). On Windows file-mode perms are not enforceable via `chmod`;
  the connect-time warning states this explicitly and steers Windows/desktop
  users to `--store-password` (Credential Manager).
- Keyring-stored and env-only profiles remain secret-free on disk (no `_secret`
  key, no `chmod` needed).
- The shared `_atomic_write_json` is **not** changed — only the profile-secret
  paths apply `chmod`, so session files are unaffected.

### Plaintext secret resolution

`resolve_credentials` step 2 (env) already precedes keyring. The on-disk
`_secret` is read as part of profile loading: after loading the profile,
if no flag/env secret was supplied, `load_profile_secret(name)` is consulted
**at the same precedence as keyring** (it is the headless equivalent of the
keyring entry). Concretely the secret chain becomes: flag → env →
(`load_profile_secret` OR `keyring_store.get_secret`) → prompt. A profile carries
at most one on-disk store (the connect flags are mutually exclusive), so there is
no ordering ambiguity between the two in normal use; if both somehow exist,
plaintext `_secret` (the on-disk value) is read first.

## Commands — `crm/commands/connection.py`

### `connect`

Add two mutually-exclusive options:

- `--store-password` → after a successful `test_connection`, `keyring_store.set_secret(profile_name, secret)`.
- `--store-password-plaintext` → `session_mod.save_profile_secret_plaintext(profile_name, secret)` + a loud warning (Windows: warn perms unenforced, recommend `--store-password`).

- Both supplied → `raise click.UsageError(...)` (exit 2), validated **before**
  touching any backend (command-layer convention for mutually-exclusive flags).
- The secret stored is exactly the one `test_connection` resolved — which, on a
  TTY, may have been `getpass`-prompted via the new step 4. So
  `crm connection connect --profile-name prod --store-password` with no
  `--password`/env prompts once, tests, then stores. No separate prompt path in
  the command.
- `--store-password` with `keyring` not installed / no backend → graceful
  `D365Error` from `keyring_store`, not a traceback.

### `connection delete-password --profile NAME`

Removes the stored secret wherever it lives: `keyring_store.delete_secret(NAME)`
**and** `session_mod.clear_profile_secret(NAME)`. If neither had a secret, emit a
clear "nothing stored for profile NAME" message (success, no error). Decided
scope: "make the stored secret gone, wherever it is" (both stores), not
keyring-only.

### `connection profiles`

Per-profile `credential_storage`:

```
"plaintext"  if the profile file has a _secret key       (cheap, no keyring call)
"keyring"    elif keyring_store.has_secret(name)
"none"       otherwise
```

Surfaced in `meta.profiles[]` (JSON) and the human status line. `--json` `data`
stays the bare name list (back-compat — unchanged shape). Plaintext is checked
first (file read, no keyring call) and reported even if a keyring entry also
exists, because the on-disk secret is the one worth surfacing.

## Dependency

`setup.py` `extras_require`:

```python
"keyring": ["keyring>=24"],
```

Optional extra (`pip install crm[keyring]`); **not** a hard runtime dependency.
Code paths that need it import lazily and degrade per §keyring_store.

## Error handling

- Mutually-exclusive flags → `click.UsageError`, exit 2.
- `--store-password` without `keyring`/backend → graceful actionable `D365Error`.
- No secret from any source → existing `D365Error`, message extended to name
  keyring + the storage flags.
- `delete-password` with nothing stored → success + clear message (no error).
- `getpass` prompt only when TTY and not `--json`; non-interactive callers fall
  straight through to the raise.

## Testing

`keyring` backend is **mocked** in all unit tests (CI has no live keyring
session); the maintainer verifies the live Windows Credential Manager round-trip.

- Secret resolution precedence: flag → env → keyring → prompt, with
  `keyring_store` mocked and `getpass` / env monkeypatched. Assert each step wins
  over the next.
- Profile-selection precedence: `whoami` with no `--profile` uses saved
  `active_profile`; `--profile` flag overrides; env-only (no saved profile) still
  builds from env; `disconnect` reverts to env.
- Mutually-exclusive flags → exit 2.
- Plaintext write: `_secret` present in file; `0600` on POSIX (assertion skipped
  on Windows); `status` and `profiles` output never contain the secret.
- `keyring` absent + `--store-password` → graceful error, no traceback.
- `delete-password`: removes keyring entry and `_secret`; no-op message when
  nothing stored.
- `profiles` reports `plaintext` / `keyring` / `none` correctly.
- OAuth profile: stores/resolves the **client secret** through the same path.

`pyright --pythonpath .venv/bin/python --pythonversion 3.9` clean on touched
strict files (`crm/core/connection.py`, `crm/core/session.py`, the new
`crm/core/keyring_store.py`).

## Documentation (same change)

- **README.md** — capability note: credentials can be stored once (keyring
  primary, explicit plaintext fallback).
- **docs/how-to/connection.md** — `--store-password` / `--store-password-plaintext`
  flows, `delete-password`, the CI/headless path, resolution order.
- **docs/reference/cli.md** — new flags and the `delete-password` command.
- **crm/skills/SKILL.md** — the "never persisted to disk" lines → "persisted only
  on explicit opt-in (`--store-password` keyring / `--store-password-plaintext`)."
- **CHANGELOG.md** — **not hand-edited.** `python-semantic-release` owns it; the
  `feat(connection): …` commit subject drives the section. (The issue AC mentions
  editing `[Unreleased]`; that predates the current PSR-owned policy — follow the
  repo, commit `68b3101`.)

## Out of scope (per triage)

- `keyrings.alt` / any automatic encrypted-file backend — the only on-disk-secret
  path is the explicit `--store-password-plaintext` flag.
- Migrating or re-encrypting existing profiles.
- A non-TTY / `--json` prompt path.
- Token caching, MFA flows, or storing anything beyond the per-profile secret.
