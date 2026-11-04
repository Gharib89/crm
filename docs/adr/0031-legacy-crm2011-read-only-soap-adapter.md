---
status: accepted
---

# Legacy CRM 2011: one read-only, allowlisted SOAP adapter (exception to ADR 0003)

[ADR 0003](0003-web-api-only-no-soap.md) makes the Web API the only transport.
A CRM 2011 org has no Web API (`/api/data` answers 404), so under that rule
no command can reach it at all. This ADR scopes one exception: a **read-only SOAP
adapter for legacy CRM 2011 discovery only**, isolated from the v9/cloud backend.
Everything else in ADR 0003 stands.

Tested on CRM 2011 UR15 (5.0.9690.3557), Linux. No wider version or Windows claim
is made.

## Decision

- **Isolation.** A separate client type in `crm/core/legacy.py` (`LegacyClient`),
  not a mode of `D365Backend`. It shares the NTLM auth and the standard error
  taxonomy, nothing else. `D365Backend` refuses a `crm2011` profile outright, so no
  v9 or cloud command can reach the SOAP code or run against a 2011 org.
- **Endpoint.** SOAP 1.1 POST to `<org url>/XRMServices/2011/Organization.svc/web`
  with plain HTTP NTLM. Measured on 5.0.9690.3557: `/web` accepts plain HTTP NTLM,
  while the main `Organization.svc` endpoint demands WS-Trust SPNEGO message
  security (`SymmetricBinding` + `SpnegoContextToken`).
  **Caveat:** Microsoft documents `/web` for web resources, not for external
  clients. It works on this build; it is checked per org (the `profile add` probe
  is what finds out), not guaranteed.
- **Allowlist as the write guard.** An HTTP-method guard cannot work here (every
  SOAP read is a POST), so the guard is one frozen set, `READ_ONLY_REQUESTS`,
  enforced before any network I/O. Only `Retrieve`, `RetrieveMultiple` and
  `Execute` with an allowlisted `RequestName` pass; this slice allowlists `WhoAmI`
  and `RetrieveVersion`, and later slices extend the one set. `Create`, `Update`,
  `Delete`, `Associate`, `Disassociate` and any unlisted `RequestName` raise a
  refusal naming the message, with no connection opened.
- **Always-read-only profile.** A profile carries `platform` (`v9`, the default and
  the meaning of a profile JSON without the key, or `crm2011`). A `crm2011`
  profile is read-only by invariant: the flag is forced on load and
  `profile edit --no-read-only` refuses, with no TTY escape hatch (contrast
  [ADR 0021](0021-read-only-profile-tty-gated-guardrail.md), where a person may
  clear it). `profile add` saves the marker when the Web API WhoAmI returns 404 on
  an NTLM profile and `RetrieveVersion` over `/web` answers a `5.x` build.
- **Faults and non-SOAP replies.** A SOAP fault becomes the standard error with the
  fault's `ErrorCode` as hex (for example `0x80040217` maps to `not_found`). SOAP
  sends every fault as HTTP 500, so a `Client` fault is reported as status 400,
  not a retryable server error. It follows the exit-code contract ([ADR 0001](0001-cli-exit-code-contract.md)). A
  non-SOAP reply (a 401 text body, an IIS HTML page) is classified by status, never
  parsed as XML. Envelopes are hand-built and parsed with the entity-safe
  `crm.utils.safe_xml`, as the ADFS WS-Trust code does; no SOAP library is added.
- **Bounded connect retries.** A failure with no HTTP response (connect timeout,
  refused, TLS handshake timeout) retries up to 4 attempts with 1, 2 and 4 second
  backoff, because the dev org sits behind a client VPN whose first connection
  often times out. Any HTTP response, a 500 included, is never retried.
- **Not widened.** SOAP-only messages on v9 or cloud stay out of scope
  (`.out-of-scope/soap-only-messages.md` is unchanged); the exception exists
  because a CRM 2011 org has no alternative, not because SOAP could reach more.

## Considered options

- **Authenticate to the main `Organization.svc` endpoint (WS-Trust SPNEGO).**
  Rejected: a second auth stack (SymmetricBinding with a SPNEGO security context)
  to hand-build in Python, for no capability `/web` lacks on this build.
- **Bridge to the Windows SDK (a .NET helper, `Microsoft.Xrm.Sdk`).** Rejected: it
  breaks the self-contained cross-platform binary and ties a Linux-first tool to
  Windows.
- **OData v2 `OrganizationData.svc`.** Rejected: it is table CRUD only, with no
  `Execute` messages (`WhoAmI`, `RetrieveVersion`, and the metadata and solution
  messages later slices need).

## Consequences

- **The allowlist is the enforced guard, not the server.** The dev account is a
  System Administrator, so the server would accept a write. Any non-dev org
  should use a dedicated account with a read-only CRM security role; the allowlist
  and the profile flag are guardrails against accidents, as in ADR 0021.
- `crm legacy` is a new D365-touching group: its verbs carry `@covers` live e2e
  tests that run only when a `crm2011` profile is configured
  (`D365_E2E_LEGACY_PROFILE`).
- Later slices (metadata, component, dependency and solution reads) extend the
  allowlist; none adds a write.

## Out of scope

- The main `Organization.svc` SPNEGO endpoint.
- IFD or claims authentication, CRM 4.0, and CRM 2013 or later.
- Any SOAP use against a v9 or cloud profile.
- Windows verification.
