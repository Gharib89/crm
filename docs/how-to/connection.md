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
Returns the user identity GUIDs plus connection metadata so the result is
self-identifying — useful for confirming which org is active without matching
`OrganizationId` by hand:

| field | meaning |
|---|---|
| `UserId` | the calling user's GUID |
| `BusinessUnitId` | the user's business unit GUID |
| `OrganizationId` | the org GUID |
| `profile` | the resolved profile name (reflects any `--profile` override) |
| `url` | the resolved Web API base URL, e.g. `https://host/org/api/data/v9.1/` |
| `org_name` | friendly org name from the `organizations` table (null on read failure) |

The success envelope also carries `meta.profile` and `meta.url` (as for every
backend-connected `--json` command — see "Connection identity" in `GLOSSARY.md`).

A non-zero exit (e.g. `401`) means the credentials are wrong — for NTLM the
`DOMAIN\username` / password, for OAuth (online) the app-registration client id /
secret / tenant, or a missing application user with a security role in Dynamics.
There is no automatic retry.

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
Shows the `active_profile` with its `publisher_prefix`. It makes **no network
call** — use it to confirm which target the next command will hit. Profiles no
longer carry a `default_solution` — every customization-write command requires
an explicit `--solution` of its own (#636).

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

## Check that `crm` and `pac` hit the same org

`crm connection whoami` proves the `crm` target only. A write that goes through
the Power Platform CLI (`pac`) uses its own auth profile, which can point at a
different org. On a Dataverse online dev org, compare the two before a
`pac`-dependent write:

```bash
crm --profile <name> --json connection whoami
crm --profile <name> --json query odata \
    "RetrieveCurrentOrganization(AccessType=Microsoft.Dynamics.CRM.EndpointAccessType'Default')"
pac org who --json
```

| `crm` side | `pac org who --json` |
|---|---|
| `whoami` `OrganizationId` | `OrgId` |
| function result `EnvironmentId` (`whoami` has none) | `EnvironmentId` |
| `whoami` `UserId` (names the caller; not part of the match) | `UserId` |

The Dataverse org ID and the Power Platform environment ID are different GUIDs:
compare each only with its own counterpart. `pac auth list` has no JSON output
and no org ID, so use `pac org who --json`. Output was identical on Ubuntu and
Windows (PAC 2.14.1).

- **Match** (org ID equal, and environment ID equal or absent from the function
  result): proceed.
- **Mismatch** (either differs): no `pac` write.
- **Unverifiable** (`pac org who --json` exits non-zero, for example with no `pac`
  profile, or `whoami` lacks `OrganizationId`): no `pac` write.
- **`pac` unavailable** (not on PATH): skip the `pac` steps, continue with `crm`.

Only a match unlocks a `pac` write. Pass the profile and the unmanaged solution
explicitly on every write. On-premises and CRM-only work skips this check:
it needs `pac` and an online org. `connection doctor` itself does not call `pac`. The bundled agent skill's
setup reference carries the same recipe for agents.
