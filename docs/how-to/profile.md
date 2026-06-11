# How-to: profile

Set up and switch connection targets. A **profile** holds the server URL, auth
scheme, identity fields (NTLM username/domain or OAuth tenant/client id), and the
optional `default_solution` / `publisher_prefix` used by metadata write commands.
The secret (NTLM password or OAuth client secret) is stored alongside it. This is
the only place credentials come from — there is no `.env` and no credential
environment variables. See the [CLI reference](../reference/cli.md) for every flag.

## Set up the first profile

```bash
crm profile add
```

On a terminal, `add` with no flags runs an **interactive wizard**: it asks for the
server URL, infers the auth scheme from it (`*.dynamics.*` → OAuth, anything else →
NTLM), prompts for the identity fields and the secret, then saves the profile,
stores the secret, runs a `WhoAmI` against the server to confirm it works, and
activates it. Zero-to-working in one command.

The first time you run any connection command with no profile configured, the CLI
launches this wizard for you automatically (TTY only). Under `--json` or a
non-interactive shell it skips the wizard and errors cleanly, telling you to run
`crm profile add`.

For scripting or CI, pass flags instead of answering prompts:

```bash
# On-prem (NTLM) — auth inferred as ntlm from the URL
crm profile add \
    --url https://crm.contoso.local/Contoso \
    --username alice --domain CONTOSO \
    --password "$SECRET" \
    --name prod

# Dataverse online (OAuth) — auth inferred as oauth from *.dynamics.com
crm profile add \
    --url https://contoso.crm.dynamics.com \
    --tenant-id <aad-tenant-id> --client-id <app-registration-id> \
    --client-secret "$CLIENT_SECRET" \
    --name online
```

`--client-secret` is an alias for `--password` (the two are mutually exclusive) so
OAuth scripting reads naturally; either works. `--name` defaults to the URL host
label. Override the inferred scheme with `--auth-scheme` when the URL doesn't match
the heuristic — the interactive wizard offers the same choice as an inline arrow-key
picker (↑/↓ then Enter, Esc to cancel) with the inferred scheme preselected. Omit `--api-version` to
**auto-negotiate** — on-prem is capped at v9.1 (v9.2 returns HTTP 501), so the CLI
steps down automatically. Attach a default solution and schema-name prefix so
metadata commands target them without per-command flags:

```bash
crm profile add --url ... --default-solution CRMWorx --publisher-prefix cwx --name crmworx
```

## Switch the active profile

```bash
crm profile use            # interactive picker (no argument)
crm profile use prod       # switch to a named profile
crm profile use --none     # clear the active profile
```

The active profile is remembered across commands. Pass `--profile <name>` on any
command to override it for a single run.

## List saved profiles

```bash
crm profile list
crm --json profile list
```

Marks the active profile and shows each one's target (on-prem / cloud), URL, and
where its secret lives (`cred=keyring`, `cred=plaintext`, or `cred=none`).

## Edit a profile's fields

```bash
crm profile edit prod --default-solution CRMWorx --publisher-prefix cwx
crm profile edit online --url https://contoso.crm.dynamics.com --client-id <new-id>
```

`edit` changes any non-secret field — URL, identity fields, api-version, default
solution, publisher prefix. To change the secret, use `set-password` (below).

## Delete a profile

```bash
crm profile rm old-profile          # prompts for confirmation
crm profile rm old-profile --yes    # skip the prompt
```

Removes the profile and its stored secret. If it was the active profile, the active
pointer is cleared.

## Manage the stored secret

Storing the secret is **automatic** when you run `crm profile add` — the wizard and
the flag-driven form both save it. Use `set-password` to store or replace it for a
profile that already exists, and `delete-password` to remove it:

```bash
crm profile set-password --profile prod                       # prompts for the secret on a TTY
crm profile set-password --profile prod --password "$SECRET"
crm profile set-password --profile online --client-secret "$CLIENT_SECRET"  # OAuth alias
crm profile delete-password --profile prod
```

`set-password` works the same for an NTLM password and an OAuth client secret.

### Where the secret is stored

By default the secret goes into the **OS keyring** — macOS Keychain, Windows
Credential Manager, or Linux SecretService. Keyring support is a core dependency, so
it works out of the box on every install with no extra to set up.

On hosts with no keyring backend (typical WSL or headless CI), the secret falls back
automatically to a `0600` plaintext entry inside the profile file on disk — no flag
needed. To force plaintext even where a keyring exists, pass
`--store-password-plaintext` to `add` or `set-password`. On POSIX the file is created
`0600`; on Windows file permissions are not enforced and a warning is emitted.

### Secret resolution order

When a command needs the secret it checks, in order:

1. `--password` on the command line (a per-run override)
2. The stored secret — plaintext entry first, then the OS keyring
3. An interactive TTY prompt (skipped in `--json` / non-interactive contexts)

There is no environment-variable step — `.env`, `D365_*`, and `CRM_*` credential
variables are not read. `CRM_HOME` is the only env var involved in
credential/connection resolution (it relocates the state directory, default
`~/.crm/`). Other `CRM_*` vars tune unrelated runtime behavior (logging,
retries, stage-only) but never supply connection config.

## Confirm it works

`crm profile add` already runs a `WhoAmI` for you. To re-check an active profile
later, use the connection diagnostics:

```bash
crm connection whoami       # issue WhoAmI() against the server
crm connection doctor       # ordered DNS/TCP → TLS → version → auth probe
```

See [How-to: connection](connection.md) for the full diagnostics set.
