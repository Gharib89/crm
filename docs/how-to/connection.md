# How-to: connection

Diagnose the active connection — reachability, identity, and a layered probe of a
broken link. See the [CLI reference](../reference/cli.md) for every flag.

!!! note "Profile setup moved to `crm profile`"
    Creating, switching, and storing credentials for profiles now lives under
    `crm profile` — see [How-to: profile](profile.md). The `connection` group is
    diagnostics only (`whoami`, `test`, `doctor`, `status`).

## Confirm reachability and identity

```bash
crm --json connection whoami
```
Returns `UserId` / `BusinessUnitId` / `OrganizationId`. A non-zero exit (e.g. `401`)
means the credentials are wrong — for NTLM the `DOMAIN\username` / password, for
OAuth (online) the app-registration client id / secret / tenant, or a missing
application user with a security role in Dynamics. There is no automatic retry.

## Reachability check with the API base

```bash
crm --json connection test
```
Runs a `WhoAmI` and reports the resolved API base — a quick smoke check that the
active profile reaches the server.

## Inspect the active session and profile

```bash
crm --json connection status
```
Shows the `active_profile` with its `default_solution` and `publisher_prefix`. It
makes **no network call** — use it to confirm which target the next command will hit.

## Diagnose a broken connection

```bash
crm connection doctor          # or the alias: crm doctor
crm --json connection doctor
```
Runs a live, ordered probe and renders a five-line checklist — `dns_tcp`, `tls`,
`version` (the configured `api_version`), `auth`, and an informational `rate_limit` —
so a failing layer is pinpointed (DNS vs TCP vs TLS vs wrong api_version vs
`401`/`403`) with an actionable hint rather than collapsed into one generic error. It
is read-only: it never negotiates or mutates the profile, and the raw GETs run
regardless of `--dry-run`. Under `--json` it emits
`{ok, data:{checks:[{check,ok,detail,hint}]}}`; the overall `ok` (and exit code) is
the AND of the four diagnostic checks — `rate_limit` is informational and never fails
the command.
