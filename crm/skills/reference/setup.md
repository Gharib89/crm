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
anything else → `ntlm`), prompts for what that scheme needs, and verifies with
WhoAmI **before saving anything**. Only on success does it store the secret and
activate the profile.

If that live test fails, `add` doesn't save blindly: a clearly malformed profile
(bad URL, missing tenant/client id, no secret) is rejected outright, nothing
saved. A structurally plausible profile whose test still failed — likely a
transient outage (VPN down, app user not yet provisioned) — prompts `Save the
profile anyway?` on a terminal; declining leaves nothing saved. Non-interactively
(`--json`/CI), pass `--save-on-test-failure` to save it anyway, otherwise the
command errors without saving. Saving despite a failed test prints a one-time
warning that the connection was never verified — nothing is flagged on the stored
profile itself.

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

**Read-only profiles (guardrail).** A profile can be marked read-only to block
accidental writes: the backend refuses every org mutation as an operational
failure (`ok:false`, exit 1, error naming the profile) while reads run normally.
Gotcha for JSON callers: a read-only refusal is **not** a `--dry-run` preview —
`--dry-run` is checked first, so a read-only *and* dry-run mutation still previews
(`ok:true`, `data._dry_run:true`), but a real mutation on a read-only profile
returns `ok:false` exit 1. If writes are being refused, that's the guardrail
working as intended — clearing it requires an interactive terminal, so don't try
to disable it non-interactively; it's a guardrail against accidents, not a
security boundary (the real boundary is a server-side read-only role).

Switch or inspect profiles with `crm profile use [name]` (no name → interactive
picker; `--none` clears the active profile) and `crm profile list` (marks the
active one); edit, rename, or delete one with `crm profile edit` /
`crm profile rename OLD NEW` / `crm profile rm`. `rename` is the safe way to
relabel a profile in place (its secret and cached metadata move with it)
without re-running `add` — but like `rm`, it only repoints the *active*
session pointer, so a concurrent session still holding the old name breaks. On
a fresh machine, any connection command with no profile drops into
`crm profile add` automatically on a terminal (under `--json`/no-TTY it errors
cleanly telling you to run `crm profile add`).
