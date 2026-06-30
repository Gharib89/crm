# Setup — install and connect

Install the `crm` binary, then create a connection profile. The skill ships
assuming the binary is already on PATH — this is here for re-install and for
connecting to a new org / host.

## Install

The prebuilt `crm` binary bundles CPython and every dependency — no Python install
needed. One line per host:

**Windows (PowerShell):**

```powershell
irm https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.ps1 | iex
```

**Linux:**

```bash
curl -fsSL https://pub-bbeb86c46454443ca76521dd4d29818e.r2.dev/install.sh | sh
```

Open a new shell so PATH updates, then verify with `crm --version`.

## Configure

The CLI authenticates with **Windows Integrated auth** for on-prem (`ntlm` by
default; `kerberos` / `negotiate` also supported), or **OAuth 2.0
client-credentials** for Dataverse online. Run **`crm profile add`** once to create
a connection profile — it infers the scheme from the URL (any `.dynamics.` host →
OAuth — `*.dynamics.com` plus regional clouds like `.dynamics.cn` / `.dynamics.de`;
anything else → `ntlm`), prompts for what that scheme needs, stores the secret,
verifies with WhoAmI, and activates the profile.

```bash
crm profile add          # interactive wizard (on a terminal)
```

Or drive it non-interactively for scripting/CI:

```bash
# On-prem (NTLM)
crm profile add --url https://crm.contoso.local/contoso \
  --username alice --domain CONTOSO --password '...' --name onprem

# Dataverse online (OAuth) — app registration instead of user/pass/domain
crm profile add --url https://contoso.crm.dynamics.com \
  --tenant-id <aad-tenant> --client-id <app-id> --client-secret '<secret>' --name cloud
```

The OAuth scope (`https://<host>/.default`) and authority
(`https://login.microsoftonline.com/<tenant>`) are derived automatically; public
cloud only. The bearer token is cached at `~/.crm/msal_token_cache.json` (`0600`).
The app registration needs an **application user** with a security role in Dynamics.

**No `.env`, no credential env vars.** The CLI reads credentials and connection
config ONLY from a saved profile (or a per-run `--password`). There is no `.env`
autoload and no `D365_*` / `CRM_*` environment-variable reading. The one retained
env knob is `CRM_HOME` (state-directory override; default `~/.crm/`).

Switch or inspect profiles with `crm profile use [name]` (no name → interactive
picker; `--none` clears the active profile) and `crm profile list` (marks the
active one); edit or delete one with `crm profile edit` / `crm profile rm`;
rename one with `crm profile rename OLD NEW` (rewrites the profile file, migrates
the keyring entry best-effort, moves the cache dir, and updates the active-session
pointer when OLD is currently active — refuses to clobber an existing NEW). On a
fresh machine, any connection command with no profile drops
into `crm profile add` automatically on a terminal (under `--json`/no-TTY it
errors cleanly telling you to run `crm profile add`).

**`--solution` is required for every customization write.** There is no profile
`default_solution` field — pass `--solution <unique_name>` explicitly on every
`metadata create-*`, `metadata update-*`, and component write. Omitting it exits 2
with a clear error. The old `--require-solution` flag and `CRM_REQUIRE_SOLUTION`
env var are gone — strict is now the only mode.

**Publisher prefix.** `crm profile add` prompts for an optional publisher prefix
(press Enter to skip); `--publisher-prefix <prefix>` sets it non-interactively. A
profile `publisher_prefix` lets `metadata create-*` auto-derive schema names
(`<prefix>_<PascalName>`) without a per-command `--schema-name` flag. Invalid
prefixes are rejected immediately (both wizard and flag paths).
