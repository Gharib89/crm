# How-to: legacy

Read a **CRM 2011** org. A CRM 2011 org has no Web API, so every other `crm`
command fails against it; `crm legacy` reaches it over a read-only SOAP
transport instead. Tested on CRM 2011 UR15 (5.0.9690.3557), Linux. See the
[CLI reference](../reference/cli.md) for every flag.

`crm legacy` only reads. It sends an allowlisted set of messages (`WhoAmI` and
`RetrieveVersion` today) over NTLM, and refuses anything else (`Create`,
`Update`, `Delete`, `Associate`, `Disassociate`, or an unlisted request) before
it opens a connection. The reasoning is in
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

## What is refused

- **A v9 or cloud command on a `crm2011` profile** (`crm entity get`,
  `crm query odata`, ...) fails with exit 1 and an error naming `crm legacy`.
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
