# SOAP-only Organization Service messages

This CLI does not implement Dynamics 365 / Dataverse operations that exist
**only** as Organization Service (SOAP) messages and have **no Dataverse Web
API surface**. The CLI speaks the Web API (OData v4) over HTTPS as its *only*
transport — on-prem v9.x (NTLM) and Dataverse online (OAuth) alike. A message
that the platform never projects into the Web API metadata cannot be invoked,
and the CLI will not grow a second SOAP transport to reach it (see "Why" below).

The concrete trigger is `ConvertDateAndTimeBehavior` (#453): the request was
`metadata convert-datetime <entity> <attr> --rule SpecificTimeZone --tz <code>`
— an async job that backfills stored UTC values after a Date/Time **behavior**
change (e.g. `UserLocal` → `DateOnly`). That message is
`Microsoft.Xrm.Sdk.Messages.ConvertDateAndTimeBehaviorRequest`, a SOAP-only
message with no Web API equivalent.

## Why this is out of scope

The Web API does not expose every SDK message. Operations that live only in the
Organization Service — `ConvertDateAndTimeBehavior` and its kin — are simply
absent from the OData metadata, so there is no segment to POST to.

Evidence for `ConvertDateAndTimeBehavior`, verified live against a v9.2 cloud
org (2026-06-20):

- The full `$metadata` CSDL (≈6.2 MB; 429 `<Action>`, 252 `<Function>`
  elements) contains **zero** `convertdate` matches — any casing, any namespace.
- `crm action invoke ConvertDateAndTimeBehavior …` →
  `Resource not found for the segment 'ConvertDateAndTimeBehavior'`.
- MS Learn has **no** Web API reference page for it; the
  `…/webapi/reference/convertdateandtimebehavior` URL 404s. It exists only as
  `Microsoft.Xrm.Sdk.Messages.ConvertDateAndTimeBehaviorRequest` (SOAP).
- There is no alternative Web API action that backfills stored datetime values.

on-prem v9.1 was not separately re-checked, but is near-certain to be absent
too: the message has no Web API surface in the newer cloud, and on-prem is the
older platform.

The only way to reach such a message would be to add an **Organization Service
(SOAP) transport** alongside the Web API client — architecturally new, large,
and worth it only if *several* SOAP-only messages were in demand. There is no
such demand (this issue was self-filed with zero external requests), so the cost
is not justified.

**Rule for future triage:** before building any new `crm` verb that wraps an SDK
message, grep the live `$metadata` CSDL for the action/function name. If it is
absent there and on MS Learn's Web API reference, it is SOAP-only and belongs
here, not in the CLI.

## Supported alternative

Setting a Date/Time attribute's behavior **at create time** is already
supported: `crm metadata add-attribute <entity> --kind datetime --behavior
<UserLocal|DateOnly|TimeZoneIndependent>`. What has no Web API path is the
**post-create conversion of values already stored** under a different behavior —
that is the part this CLI cannot do. The Microsoft-supported route is to run the
conversion job from the maker portal / classic UI, or via a SOAP client.

## Prior requests

- #453 — "metadata convert-datetime: ConvertDateAndTimeBehavior is SOAP-only, not in the Web API"
