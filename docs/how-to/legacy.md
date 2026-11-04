# How-to: legacy

Read a **CRM 2011** org. A CRM 2011 org has no Web API, so every other `crm`
command fails against it; `crm legacy` reaches it over a read-only SOAP
transport instead. Tested on CRM 2011 UR15 (5.0.9690.3557), Linux. See the
[CLI reference](../reference/cli.md) for every flag.

`crm legacy` only reads. It sends an allowlisted set of messages (`WhoAmI`,
`RetrieveVersion`, the three metadata reads, the three dependency reads,
`ExportSolution`, the two ribbon reads and paged `RetrieveMultiple`) over NTLM,
and refuses anything else (`Create`, `Update`, `Delete`, `Associate`,
`Disassociate`, `PublishXml`, `PublishAllXml`, `ImportSolution`, or any other
unlisted request) before it opens a connection. The reasoning is in
[ADR 0031](../adr/0031-legacy-crm2011-read-only-soap-adapter.md).

## Create the profile

```bash
crm profile add --url https://crm.contoso.local/contoso \
  --username alice --domain CONTOSO --name legacy2011
```

`profile add` tests the connection with the Web API first. When that answers 404
on an NTLM profile and the org answers `RetrieveVersion` over its SOAP endpoint
with a `5.x` build, the profile is saved with `platform: crm2011` and
`read_only: true`, and its output carries a `platform` key. Credentials resolve
as for any profile (OS keyring, then a `0600` plaintext fallback, or
`--password`).

A `crm2011` profile is **always read-only**: `crm profile edit legacy2011
--no-read-only` refuses, with no terminal confirmation to bypass it. A server-side
read-only role is still the real boundary, so use a dedicated account with one
for any org that is not a development org: the allowlist stops the CLI from
writing, not the account from being able to.

## Run the commands

```bash
crm --json legacy version --profile legacy2011
crm --json legacy whoami --profile legacy2011
```

`legacy version` returns the build string:

```json
{ "ok": true, "data": { "version": "5.0.9690.3557" },
  "meta": { "profile": "legacy2011", "url": "https://crm.contoso.local/contoso/XRMServices/2011/Organization.svc/web" } }
```

`legacy whoami` returns `user_id`, `business_unit_id` and `organization_id`. Both
use the standard envelope, with `meta.url` set to the SOAP endpoint (not a Web
API URL).

## Read metadata

```bash
crm --json legacy metadata --profile legacy2011                  # every entity
crm --json legacy metadata account contact --profile legacy2011  # per-entity detail
crm --json legacy metadata --option-sets --profile legacy2011    # global option sets
```

With no entity, `data` is a list of `logical_name`, `display_name` (null for an
entity with no label, such as an intersect entity), `custom`,
`object_type_code`, `metadata_id` and `managed`. With entity names, `data` is `{"entities": [...], "errors":
[...]}`, each entity carrying its attributes, relationships and option sets. Entities,
attributes, relationships and option sets each carry their `metadata_id` (the id
[`legacy dependencies`](#read-dependencies) takes), and entities, attributes and
relationships carry `managed`. The
run is **continue-and-report** ([ADR 0007](../adr/0007-record-clone-no-rollback-continue-and-report.md)):
an entity that faults (a misspelt logical name, say) lands in `errors`, the other
entities still read, and the exit code is 1. `--option-sets` takes no entity.

## Read solution components

```bash
crm --json legacy components --profile legacy2011                       # every kind
crm --json legacy components web-resources processes --out ./dump --profile legacy2011
```

`data` has one list per kind plus `errors`. Each kind is read with a SOAP
`RetrieveMultiple` (FetchXML) that follows the server's paging cookie to the last
page, so a large org is read whole. The two limits it avoids: the OData v2
`OrganizationData.svc` caps pages at 50, and a FetchXML aggregate stops at the
server's 50,000-record limit, so no aggregate is used.

- **Continue-and-report per kind.** A kind that faults keeps the rows it already
  read, is listed in `errors`, and the next kind still runs; the exit code is 1.
  Under `--out`, a payload that cannot be written or decoded fails its kind the
  same way, with `code: null`. An auth failure (401) or an unreachable org is not per kind: it
  stops the run with the standard error envelope, since every later read would
  fail the same way.
- **`processes` are workflow definitions only** (type 1), not activations or
  templates.
- **Plug-in assemblies stored on disk or in the GAC** have no database binary:
  each is listed with `binary: "unavailable"` and a `binary_reason`, never
  skipped.
- **`--out DIR`** writes web resource content, process XAML and form XML to files
  under `DIR` and replaces each payload key with `<payload>_file` (a path relative
  to `DIR`). Without it the payload stays inline in the JSON.

## Read dependencies

```bash
crm --json legacy dependencies <metadata-id> --component-type 1 --profile legacy2011
crm --json legacy dependencies <metadata-id> --component-type 1 --for dependents --profile legacy2011
```

`legacy dependencies OBJECT_ID --component-type CODE` reads one component's
dependencies with a dependency request: `--for required` (the default) sends
`RetrieveRequiredComponents` (what the component needs), `dependents` sends
`RetrieveDependentComponents` (what needs it) and `delete` sends
`RetrieveDependenciesForDelete` (what blocks its deletion). `OBJECT_ID` is the
component's id: the `metadata_id` from `legacy metadata` for an entity, attribute,
relationship or option set, or the record `id` from `legacy components` for
anything else. The component type code is the platform's (1 entity, 2 attribute,
9 option set, 61 web resource, ...).

`data` is a list of rows, each naming both sides by component type and object id
(`dependentcomponenttype`, `dependentcomponentobjectid`,
`dependentcomponentparentid`, and the same three for `required`) plus
`dependencytype` (1 solution internal, 2 published, 4 unpublished). `meta.for`
echoes the choice. A fault (an id that is not a component of that type, say) is
one standard error envelope.

## Export a solution

```bash
crm --json legacy export-solution contoso_core -o ./contoso_core.zip --profile legacy2011
```

`legacy export-solution UNIQUE_NAME -o FILE` sends `ExportSolution` for an
**unmanaged** export and writes the zip to `FILE`, creating its folder. Nothing is
published first, because publishing writes, so the zip holds the org's
**published** customizations only: an unpublished edit is not in it. `data` is
`{solution, path, bytes}`.

## Extract a migration inventory

`crm inventory extract --out DIR` runs on a `crm2011` profile and reads the whole
org into a migration inventory, over the same read-only allowlist. See
[How-to: inventory](inventory.md).

## What is refused

- **A v9 or cloud command on a `crm2011` profile** (`crm entity get`,
  `crm query odata`, ...) fails with exit 1 and an error naming `crm legacy`.
  `crm inventory extract` is the one other command that runs on it.
- **`crm legacy` on any other profile** refuses and says it needs a `crm2011`
  profile.
- **A SOAP fault** becomes the standard error with the fault's `ErrorCode` as
  hex (`0x80040217` maps to `not_found`), so exit codes follow the same contract.
  A fault the request caused (a SOAP `Client` fault) reports status 400 and is
  not retryable, though SOAP sent it as HTTP 500.

## Connection retries

A failure with no HTTP response (connect timeout, connection refused, a stalled
TLS handshake) retries up to 4 attempts with 1, 2 and 4 second backoff, which
covers an org behind a client VPN whose first connection often times out. Any
HTTP response, a 500 included, is never retried, and neither is a TLS
certificate failure, which fails the same way every time.

## Caveats

- The transport uses the org's `/XRMServices/2011/Organization.svc/web` endpoint,
  which accepts plain HTTP NTLM on the tested build. Microsoft documents that
  endpoint for web resources, not for external clients, so it is checked per org
  and not guaranteed. The main `Organization.svc` endpoint needs WS-Trust SPNEGO
  message security and is not supported.
- IFD or claims authentication, CRM 4.0 and CRM 2013 or later are out of scope.
