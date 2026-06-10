---
status: accepted
---

# Web API (OData) is the only transport — no SOAP

The CLI speaks exclusively the Dataverse Web API (OData v4) over HTTPS, for
on-prem and online alike. We will not add a SOAP transport
(`SetStateRequest`, `ExecuteRequest`, the 2011 Organization Service endpoint),
even when a SOAP message could reach something the Web API cannot. Issues that
require one are routed to a workaround (D365 UI, one-off script outside the
CLI) or `.out-of-scope/`, not implemented.

Recorded because the ask keeps recurring (#164 proposed embedding SOAP
`SetStateRequest` for workflow state; the `.out-of-scope/` family — BPF
clientdata #37, solution clone #166 — orbits the same boundary) and, so far,
every observed "Web API can't do it" case was a misdiagnosis: the
`0x80045003`/`0x80045004` errors that motivated #164 came from targeting an
**activation record** instead of its **workflow definition** (see
[CONTEXT.md](../../CONTEXT.md)); the definition accepts the plain OData state
PATCH and delete.

## Considered options

- **Embed a minimal SOAP client for the few gap cases.** Rejected: a second
  transport is a second auth stack (WS-Security / NTLM-over-SOAP on-prem,
  OAuth token shaping online), a second failure taxonomy to map onto the
  exit-code contract, and a second surface to keep pyright-strict — all for
  edge cases that have so far evaporated under triage. The full D365 SDK
  exists for genuinely SOAP-only work.
- **Shell out to an external SOAP tool when present.** Rejected: breaks the
  self-contained PyInstaller distribution and makes behavior depend on host
  tooling.

## Consequences

- A capability with no Web API path is out of scope by definition; triage can
  cite this ADR instead of re-arguing transport.
- Apparent Web API limitations must be probed against a live org before being
  believed — the burden of proof is on "OData can't", not on adding SOAP.
