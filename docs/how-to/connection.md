# How-to: connection

Connect and verify identity, taken from the CRMWorx build (§1). See the
[CLI reference](../reference/cli.md) for every flag.

## Confirm reachability and identity

```bash
crm --json connection whoami
```
Returns `UserId` / `BusinessUnitId` / `OrganizationId`; a non-zero exit (e.g. `401`) means the `DOMAIN\username` credentials are wrong.

## Save a targeting profile (validates credentials)

```bash
crm --json connection connect \
  --url "$CRM_BASE_URL" --username "$CRM_USERNAME" \
  --api-version v9.1 \
  --default-solution CRMWorx --publisher-prefix cwx \
  --profile-name crmworx
```
Saves the profile and runs a WhoAmI check; the password is read from `D365_PASSWORD`, never passed on the command line — set it via `.env` or a shell export; see the [CLI reference](../reference/cli.md).

## Inspect saved profiles and the active session

```bash
crm --json connection profiles
crm --json connection status
```
`profiles` lists every saved profile; `status` confirms the `active_profile` with its `default_solution` and `publisher_prefix` (no network call).
