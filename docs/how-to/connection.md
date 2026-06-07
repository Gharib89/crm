# How-to: connection

Connect and verify identity, taken from the CRMWorx build (§1). See the
[CLI reference](../reference/cli.md) for every flag.

## Confirm reachability and identity

```bash
crm --json connection whoami
```
Returns `UserId` / `BusinessUnitId` / `OrganizationId`; a non-zero exit (e.g. `401`) means the credentials are wrong — for NTLM the `DOMAIN\username`/password, for OAuth (`D365_AUTH=oauth`, online) the app-registration client id/secret/tenant or a missing application user in Dynamics.

### OAuth (online) smoke check

Verify a Dataverse cloud org over bearer auth (no NTLM):

```bash
export D365_URL="https://<org>.crm.dynamics.com"
export D365_AUTH="oauth"
export D365_TENANT_ID="<aad-tenant-id>"
export D365_CLIENT_ID="<app-registration-id>"
export D365_CLIENT_SECRET="<secret>"

crm --json connection whoami            # expect UserId/OrganizationId, exit 0
ls -l ~/.crm/msal_token_cache.json      # token cached at 0600 after first call
crm --json connection whoami            # second call reuses the cached token
```

A `401` here points at the app registration (client id/secret/tenant) or a
missing application user with a security role — there is no automatic retry.

## Save a targeting profile (validates credentials)

```bash
crm --json connection connect \
  --url "$CRM_BASE_URL" --username "$CRM_USERNAME" \
  --api-version v9.1 \
  --default-solution CRMWorx --publisher-prefix cwx \
  --profile-name crmworx
```
Saves the profile and runs a WhoAmI check. The profile name must be a single path component — no path separators (`/`, `\`), no `:` (rejected so a Windows drive-relative name like `C:foo` can't escape the state directory), and not `.` or `..`; names like `prod`, `crmworx`, or `my-profile` are fine. `--password` is accepted, but prefer `D365_PASSWORD` (e.g. from a `.env` file) to keep the secret off the command line and out of the process list; see the [CLI reference](../reference/cli.md).

## Inspect saved profiles and the active session

```bash
crm --json connection profiles
crm --json connection status
```
`profiles` lists every saved profile; `status` confirms the `active_profile` with its `default_solution` and `publisher_prefix` (no network call).

## Diagnose a broken connection

```bash
crm connection doctor          # or the alias: crm doctor
crm --json connection doctor
```
Runs a live, ordered probe and renders a five-line checklist — `dns_tcp`, `tls`, `version` (the configured `api_version`), `auth`, and an informational `rate_limit` — so a failing layer is pinpointed (DNS vs TCP vs TLS vs wrong api_version vs `401`/`403`) with an actionable hint rather than collapsed into one generic error. It is read-only: it never negotiates or mutates the profile, and the raw GETs run regardless of `--dry-run`. Under `--json` it emits `{ok, data:{checks:[{check,ok,detail,hint}]}}`; the overall `ok` (and exit code) is the AND of the four diagnostic checks — `rate_limit` is informational and never fails the command.

## Store credentials once

By default secrets are never persisted to disk. Use one of the opt-in storage
flags on `crm connection connect` to configure once and skip the prompt on every
subsequent call.

### OS keyring (recommended)

Saves the secret in the platform keyring — macOS Keychain, Windows Credential
Manager, or Linux SecretService. Requires the optional extra:

```bash
pip install crm[keyring]
```

**NTLM (on-prem):**

```bash
crm connection connect \
    --url https://crm.contoso.local/Contoso \
    --username alice --domain CONTOSO \
    --profile-name prod \
    --store-password
```

**OAuth (Dataverse online):**

```bash
crm connection connect \
    --url https://contoso.crm.dynamics.com \
    --auth oauth \
    --tenant-id "<aad-tenant-id>" \
    --client-id "<app-registration-id>" \
    --profile-name cloud \
    --store-password
```

The `--password` / client secret value is read from `--password` or the
`D365_CLIENT_SECRET` / `D365_PASSWORD` env var and then stored in the keyring
under the profile name. Subsequent invocations retrieve it automatically.

### Headless / CI fallback (plaintext)

On hosts with no keyring daemon (containers, CI runners), use
`--store-password-plaintext` instead:

```bash
crm connection connect \
    --url https://crm.contoso.local/Contoso \
    --username alice --domain CONTOSO \
    --profile-name prod \
    --store-password-plaintext
```

This writes the secret into the profile file. On POSIX the file is created
`0600`; on Windows file permissions are not enforced and a warning is emitted.

### Remove a stored secret

```bash
crm connection delete-password --profile prod
```

Removes the stored secret from whichever store (keyring or plaintext) the
profile uses. The profile itself is kept; only the secret is removed.

### Check storage type

```bash
crm --json connection profiles
```

The output now includes a `storage` field per profile showing `keyring`,
`plaintext`, or `none`.

### Resolution order

When a command needs the secret it checks, in order:

1. `--password` flag on the command line
2. `D365_PASSWORD` / `CRM_PASSWORD` environment variable (or `.env` file)
3. Stored secret (keyring or plaintext profile entry)
4. Interactive TTY prompt (skipped in non-interactive / CI contexts)
