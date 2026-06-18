# Configure

The CLI authenticates with **NTLM (Windows Integrated)** for on-prem, or
**OAuth 2.0 client-credentials** for Dataverse online. Both live in a saved
**profile** — there is no `.env` file and no credential environment variables.
Create a profile with `crm profile add`:

```bash
crm profile add
```

On a terminal this runs an interactive wizard: it asks for the server URL, infers
the auth scheme from it (`*.dynamics.*` → OAuth, anything else → NTLM), collects the
identity fields and the secret, saves the profile, stores the secret, runs a
`WhoAmI` to confirm, and activates it.

For scripting, pass flags instead:

**On-prem (NTLM):**

```bash
crm profile add \
    --url https://crm.contoso.local/contoso \
    --username alice --domain CONTOSO \
    --password "$SECRET" \
    --name prod
```

`--domain` is optional when the username is a UPN. Omit `--api-version` to
auto-negotiate — on-prem caps at v9.1 (v9.2 returns HTTP 501), so the CLI steps down
automatically.

**Online / Dataverse cloud (OAuth):**

```bash
crm profile add \
    --url https://contoso.crm.dynamics.com \
    --tenant-id <aad-tenant-id> --client-id <app-registration-id> \
    --password "$CLIENT_SECRET" \
    --name online
```

The OAuth scope and authority are derived automatically (public cloud only). The app
registration needs an **application user** with a security role in Dynamics. The
bearer token is cached under `~/.crm/` (`0600`) and reused until it expires;
username / password / domain are not used in this mode.

Attach a default solution and schema-name prefix so metadata write commands target
them without per-command flags:

```bash
crm profile add --url ... --default-solution CRMWorx --publisher-prefix cwx --name crmworx
```

## How the secret is stored

`crm profile add` stores the secret automatically. By default it goes into the OS
keyring (macOS Keychain / Windows Credential Manager / Linux SecretService); on hosts
with no keyring backend (typical WSL / headless CI) it falls back automatically to a
`0600` plaintext entry inside the profile file. Force plaintext with
`--store-password-plaintext`. Replace a stored secret later with
`crm profile set-password --profile <name>`.

When a command needs the secret it resolves it in this order: `--password` (a per-run
override) → the stored secret (plaintext entry, then keyring) → an interactive TTY
prompt. No environment variable is consulted.

State lives under `~/.crm/` — the only environment knob that affects connections is
`CRM_HOME`, which relocates that directory. See [How-to: profile](../how-to/profile.md)
for the full profile reference.

## Switching and managing profiles

You can keep several profiles and switch the active one:

```bash
crm profile list                 # show all profiles; the active one is marked
crm profile use online           # make "online" the active profile
crm profile edit prod            # change saved fields
crm profile set-password --profile prod   # replace the stored secret
crm profile rm old               # delete a profile
```

Commands use the active profile unless you pass `--profile <name>` for a single run.
See [how-to: profile](../how-to/profile.md) for every flag.
