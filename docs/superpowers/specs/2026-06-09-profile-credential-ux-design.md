# Profile & Credential UX Revamp — Design

**Date:** 2026-06-09
**Status:** Approved (design); pending implementation plan
**Target version:** v2.0.0 (breaking)

## Problem

Today's setup, credential, and profile management is split, opt-in, and `.env`-dependent:

- **Two overlapping setup paths.** `crm init` (interactive wizard, NTLM **and** OAuth) vs `crm connection connect` (flag-driven, **NTLM-only**). Confusing which to use; OAuth profiles can only be born in the wizard.
- **Credentials are NOT saved by default.** The user must pass `--store-password` / `--store-password-plaintext`, or supply `D365_PASSWORD` via `.env`. Result: people lean on `.env`.
- **No real profile switcher.** Switching means the `--profile` flag, the session's `active_profile`, and `disconnect`. No `crm <verb> use` and no picker.
- **`.env` is load-bearing for credentials.** `load_dotenv()` auto-loads `.env`; `D365_*`/`CRM_*` env vars feed `profile_from_env()`. This is the local-dev crutch the user wants gone.
- **Two confusing auth knobs.** `D365_AUTH`/`CRM_AUTH` (env: *which* credential block to read) vs `CRM_AUTH_SCHEME`/`--auth-scheme` (per-invocation override).

### Goal

Match the experience of best-in-class CLIs (`gh auth login`, `gcloud init`, `flyctl`): **credentials live in the tool's own config dir (`~/.crm/`), never `.env`. Configure a profile once, it just works. Switching is one command. Zero-to-working is one command.**

## Decisions (locked with the user)

1. **Command surface:** consolidate profile management under a `crm profile` noun group.
2. **Default storage:** always save the secret. Try OS keyring; auto-fall back to `chmod 600` plaintext when keyring is unavailable (typical WSL/headless); always report which store was used.
3. **Switching:** `crm profile use` with no arg shows an interactive arrow-key picker; with a name it switches directly.
4. **Clean break on env:** remove **all** `.env` auto-loading **and** `D365_*`/`CRM_*` credential/config env-var reading. crm gets credentials and connection config **only** from a saved profile or an explicit flag. No env opt-in (matches `gh`/`gcloud`/`flyctl`).
5. **Back-compat:** hard remove `crm init` and `crm connection connect` now; one clean breaking release (**v2.0.0**). Old names error with a one-line pointer.
6. **Group split:** `crm profile` owns management (add/list/use/edit/rm/set-password/delete-password); `crm connection` keeps only live diagnostics (whoami/test/doctor/status), acting on the active profile.
7. **UX upgrades folded in:** infer auth mode from URL; smart defaults + confirm-by-doing (WhoAmI); auto-launch wizard on first use (TTY only); actionable auth-error hints.
8. **Out of scope (deferred):** wrong-org destructive-op guard. Tracked separately; not in this change.

## Implementation approach

Build the interactive layer on **`prompt_toolkit`** (already a core dep — used by `crm repl`) for the picker, and `click.prompt`/`getpass` for sequential field prompts (same primitives as today's `init` wizard). **No new dependency** — does not touch the 5 PyInstaller bundle sites for deps (only new lazy command modules → `crm.spec` hiddenimports, per [[project-release-build-traps]]).

## Command surface (the new `crm profile` group)

```
crm profile add [flags]       wizard (no flags) OR scriptable (full flags). Creates + activates a profile, saves creds by default.
crm profile use [name]        no arg → arrow-key picker; with name → switch directly. --none clears active.
crm profile list              table of all profiles; active marked ●, with target (on-prem/cloud) + host.
crm profile edit <name>       change url/username/domain/api_version/solution/prefix (NOT secret). Wizard or flags.
crm profile rm <name>         delete profile + its stored secret. Confirm unless --yes.
crm profile set-password      store/replace secret for a profile (moved from `connection`).
crm profile delete-password   remove stored secret (moved from `connection`).

crm connection whoami|test|doctor|status    UNCHANGED — live diagnostics on the active profile.
```

**Removed (breaking → v2.0.0):**

| Old | Replacement |
|-----|-------------|
| `crm init` | `crm profile add` |
| `crm connection connect` | `crm profile add` |
| `crm connection profiles` | `crm profile list` |
| `crm connection disconnect` | `crm profile use --none` |
| `crm connection set-password` | `crm profile set-password` |
| `crm connection delete-password` | `crm profile delete-password` |

Old command names error with a one-line pointer to the new command (Click's "no such command" + a custom hint where feasible).

## Credential storage & resolution (the clean break)

### Storage on `add` / `set-password` (always saves — no opt-in flag)

1. Try OS keyring (`keyring_store.py`, service `"crm"`, account = profile name). Success → store there.
2. Keyring unavailable → auto-fall back to plaintext `_secret` in the profile JSON, `chmod 600` (POSIX).
3. **Always print which store was used:**
   - `✓ secret saved to OS keyring`
   - `⚠ keyring unavailable — saved to profile file (chmod 600)`
4. `--store-password-plaintext` forces plaintext (skips the keyring attempt).
5. **Keyring-XOR-plaintext invariant preserved:** writing one store clears the other (existing behavior in `connect`/`set-password`).

### Runtime resolution — new precedence (top → bottom)

```
1. --password flag            (explicit, this invocation)
2. active profile's secret    (keyring, then plaintext _secret)
   └─ --profile <name> selects a different profile here
```

**Removed entirely:**

- `.env` auto-load (`load_dotenv()` deleted from `resolve_credentials()` and `connection_test()`).
- All `D365_*` / `CRM_*` credential **and connection-config** env-var reading (`_env()`, `profile_from_env()`).
- The `D365_AUTH` / `CRM_AUTH` env selector.

### Connection config (url / auth-scheme / api_version / …) — profile-only

The env-derived-profile path (`profile_from_env()`) is removed. **No profile + no `--profile` = error** (or auto-launch wizard on a TTY — see below). `--auth-scheme` survives **only** as a per-invocation override of the active profile's saved scheme (no env coupling).

### Env vars: what survives

- **`CRM_HOME`** — storage root override. **Kept** (a path knob, not a credential).
- **`CRM_DOTENV`** — **removed** (dies with `.env`).
- All `D365_*` / `CRM_*` connection/credential vars — **removed**.

## The `add` wizard flow

```
$ crm profile add
? URL          › https://org.crm.dynamics.com
  → detected: Cloud (OAuth)              ← inferred from *.dynamics.com host; overridable
                                            (non-dynamics host → defaults On-prem/NTLM)

  ── Cloud (OAuth) branch ──             ── On-prem (NTLM) branch ──
  ? Tenant ID  ›                          ? Domain      › [CONTOSO]   (pre-filled from host)
  ? Client ID  ›                          ? Username    ›
  ? Secret     › ********                  ? Password    › ********

? api_version [v9.2]   ›                  ← smart default
? profile name [org]   ›                  ← default = URL host label
? default solution (optional) ›
? publisher prefix (optional) ›

  Saving secret… ✓ keyring            (or ⚠ keyring unavailable — profile file, chmod 600)
  Testing connection…
✓ Connected as ahmed@contoso (org b948cd5f) — profile 'org' is now active
```

- **Auth-mode inference:** `*.dynamics.com` → Cloud/OAuth; any other host → On-prem/NTLM. Shown as the default; `--auth-scheme ntlm|oauth` (flag) or an in-wizard toggle overrides.
- **Smart defaults:** `api_version` defaults to `v9.2`; profile name defaults to the URL host label (`crm.contoso.local` → `contoso`); domain pre-filled from host where derivable.
- **Confirm-by-doing:** the wizard ends with a live WhoAmI and prints the identity, so the user leaves knowing it works. (api_version is negotiated when omitted, as `init`/`connect` do today.)
- **Scriptable form (CI / no TTY):** every prompt has a matching flag, e.g.
  `crm profile add --url … --tenant-id … --client-id … --password "$SECRET" --name cloud --yes`.
  If a required field is missing **and** there is no TTY → clean error naming the missing flag (never hangs).
- **`edit`** reuses the same field prompts pre-filled with current values; the secret is untouched (use `set-password` to change it).

## Picker, auto-launch, error hints

### `crm profile use` picker

```
$ crm profile use
? Select profile to activate:
  ● contoso   on-prem  https://crm.contoso.local      (active)
  ○ cloud     cloud    https://org.crm.dynamics.com
  ○ sandbox   cloud    https://sb.crm.dynamics.com
✓ switched → cloud
```

- `crm profile use cloud` → direct switch, no picker.
- `crm profile use --none` → clears active profile (replaces `disconnect`).
- No TTY + no name → clean error (`profile name required; see 'crm profile list'`); never hangs.
- Empty profile list → "No profiles. Run 'crm profile add'." (or auto-launch on TTY).
- Picker built on `prompt_toolkit` (`radiolist_dialog` or inline radiolist), factored into a shared `select_one(items)` helper in `crm/commands/_helpers.py` with a non-TTY fallback.

### Auto-launch wizard on first use

Any command that needs a connection, run with **no profile configured** and **no `--profile`**:

- **TTY** → print `No profile configured yet. Let's set one up:` then drop into `crm profile add`. After setup, the user re-runs the original command (no implicit replay).
- **No TTY / `--json`** → clean non-zero error: `No profile configured. Run 'crm profile add'.` Never prompts in scripts.
- Trigger lives at the `backend()` construction seam in `cli.py` (where profile resolution happens today, ~lines 137–169).

### Actionable auth-error hints

Map failure → fix command:

```
✗ 401 Unauthorized — stored secret rejected.
  → run: crm profile set-password --profile cloud

✗ No secret stored for profile 'cloud'.
  → run: crm profile set-password --profile cloud
```

Hints respect `--json`: emit a structured `hint` field (gated on `ctx.json_mode`, per [[project-emit-meta-renders-in-human-mode]]), not decorative text. Builds on `connection doctor`'s existing hint pattern.

## Code touch-points

| Area | Change |
|------|--------|
| `crm/commands/profile.py` | **NEW** — the `crm profile` group: `add`, `use`, `list`, `edit`, `rm`, `set-password`, `delete-password`. |
| `crm/commands/init.py` | **DELETE.** |
| `crm/commands/connection.py` | Strip `connect`, `profiles`, `disconnect`, `set-password`, `delete-password`. Keep `whoami`, `test`, `doctor`, `status`. |
| `crm/core/connection.py` | DELETE `load_dotenv` path, `profile_from_env()`, all `_env()` / `D365_*` / `CRM_*` reading, `D365_AUTH` selector. `resolve_credentials()` collapses to `--password` → profile keyring → profile plaintext → (TTY prompt). |
| `crm/cli.py` | Resolution chain → `--profile` > active profile only. Add auto-launch-wizard / clean-error seam at `backend()`. `--auth-scheme` kept as profile-only override (drop env coupling). Wire the new `profile` group; unwire `init`. |
| `crm/core/session.py`, `keyring_store.py`, `utils/d365_backend.py` | Storage primitives reused mostly unchanged. `add` always saves the secret (no opt-in). |
| `crm/commands/_helpers.py` | Add `select_one(items)` picker helper with non-TTY fallback. |
| `crm/skills/` | Update `SKILL.md` router + `reference/*.md`: new command names, no-`.env` contract, the saved-by-default storage rule. (Skill is self-contained — inline, never link repo paths.) |
| `crm.spec` | Add new lazy command module(s) to `hiddenimports` (per [[project-release-build-traps]]). |

`crm/core/connection.py` is **pyright strict** — keep the collapsed `resolve_credentials()` strictly typed.

## CI / test rework (cost of the clean break)

- **E2E creds today come from `.env`/env vars.** Rework: a session-scoped pytest fixture builds a throwaway profile from CI secrets into a temp `CRM_HOME` (`crm profile add --url "$D365_URL" --password "$D365_PASSWORD" … --name e2e-ci --yes`), activates it, tears it down after.
- **`.github/workflows/*` E2E job:** still reads secrets from the runner env, but pipes them into `crm profile add` flags instead of exporting `D365_*` for crm to scan.
- **Delete** `test_resolve_credentials_*` env-path cases and the `CRM_DOTENV` isolation cases. [[feedback-test-isolate-load-dotenv]] becomes **obsolete** (no more `load_dotenv` to isolate) — note in the memory update. Keep keyring/plaintext resolution tests.
- **New tests:** `profile add/use/list/edit/rm`; picker non-TTY fallback; auth-mode inference (`*.dynamics.com` vs other); auto-launch trigger (TTY vs `--json`); error-hint mapping; "always saves" storage + keyring→plaintext fallback path.
- Test files stay generic — `Contoso` / `internalcrm.contoso.local` placeholders, never real org values (per [[feedback-keep-repo-generic]]); `git grep -ni moce` clean before commit.

## Docs (same change — CI gate `mkdocs build --strict`)

- `docs/getting-started/configure.md` — rewrite; drop the `.env` / env-var section; lead with `crm profile add`.
- `docs/how-to/connection.md` — split: new `docs/how-to/profile.md` (management) + slimmed `connection.md` (diagnostics). Update `mkdocs.yml` nav.
- `docs/reference/cli.md` — regenerate for the new command tree.
- `README.md` — auth-modes + storage-strategy section; remove `.env` instructions.
- `CLAUDE.md` — profile/credential notes.
- Stale cross-refs / broken links fail CI (`docs.yml`).

## Migration for existing users

- On first v2.0.0 run, existing `~/.crm/profiles/*.json` keep working — **schema unchanged**, no data migration.
- Only lost behavior: env/`.env` credential injection. Users who relied on it run `crm profile add` once (or pass `--password`).
- Old command names print a one-line pointer to the replacement.

## Versioning

Ship a breaking Conventional Commit (`feat!:` / `BREAKING CHANGE:` footer). PSR cuts **v2.0.0** automatically on push to `main` (`major_on_zero` is moot post-1.0). Bundle-shape: only new lazy command modules → `crm.spec` hiddenimports; no dep change, so the other PyInstaller path sites are untouched.

## Success criteria

1. `crm profile add` on a fresh machine → working connection with creds saved, zero `.env`, zero extra flags on the happy path; ends with a green WhoAmI line.
2. `crm profile use` (no arg) shows a picker; `crm profile use <name>` switches directly; `crm profile list` marks the active one with host.
3. No `.env` and no `D365_*`/`CRM_*` env var influences credentials or connection config anywhere (grep-clean of `load_dotenv` / `profile_from_env` / `_env(`).
4. Running any connection command with no profile, on a TTY, auto-launches the wizard; under `--json`/no-TTY it errors cleanly and never hangs.
5. `pytest` green (reworked E2E fixture); `pyright --pythonpath .venv/bin/python` clean; `mkdocs build --strict` clean.
6. Old `crm init` / `crm connection connect` error with a pointer to the new command.
