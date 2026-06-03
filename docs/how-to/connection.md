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
Saves the profile and runs a WhoAmI check. `--password` is accepted, but prefer `D365_PASSWORD` (e.g. from a `.env` file) to keep the secret off the command line and out of the process list; see the [CLI reference](../reference/cli.md).

## Inspect saved profiles and the active session

```bash
crm --json connection profiles
crm --json connection status
```
`profiles` lists every saved profile; `status` confirms the `active_profile` with its `default_solution` and `publisher_prefix` (no network call).
