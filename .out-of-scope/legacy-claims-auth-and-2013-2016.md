# Claims/IFD sign-in for the CRM 2011 adapter, and reading CRM 2013 to 2016

This project does not support claims-based or Internet-Facing Deployment (IFD)
authentication for the read-only CRM 2011 SOAP adapter (`crm legacy`, a
`crm2011` profile), and does not claim read support for CRM 2013, 2015 or 2016
orgs. The adapter is what ADR 0031 scoped and measured: CRM 2011 over NTLM.

## Why this is out of scope

**There is no org to build or verify either half against.** The adapter was
measured on one CRM 2011 UR15 org over plain NTLM. The project has no CRM 2011
org that is reachable only through IFD, and no 2013, 2015 or 2016 org. ADR 0031
requires version and auth claims to be measured, and ADR 0030 keeps live proof
out of CI, so both halves would be written against mocks of a protocol nobody
here can observe. That is the same trap as OAuth against AD FS
(`oauth-adfs-ifd.md`): mocked tests pass while the real flow fails.

**Claims auth for 2011 SOAP is a new adapter, not a reuse of the `adfs`
scheme.** The `adfs` auth scheme (#978) signs in to an on-premises 9.x org's
Web API: a WS-Trust token request against the AD FS STS, then a browser-style
post that yields session cookies. The 2011 SOAP adapter posts to the
`Organization.svc/web` endpoint with NTLM on the HTTP layer. A claims-mode 2011
org expects a SAML token inside the WS-Security header of the SOAP envelope, on
the main `Organization.svc` contract. Only the token request and the STS
discovery carry over. Microsoft Learn documents the protocol family (WS-Trust
active mode) and the .NET helper classes, and gives no CRM-specific recipe for
a non-.NET client.

**2013 to 2016 read support is unmeasured in every direction.** The adapter
recognises only a 5.x version when a profile is saved. Whether the 2011 SOAP
endpoint behaves identically on 2013, 2015, 2016 and 8.2 is not documented per
version. Whether the Web API could read a 2016 org instead is not settled
either: Learn says both that it was introduced with version 8.0 and that it was
introduced with the December 2016 update (8.2), and early versions lacked
parity. Each org would have to be probed.

**It does not speed a migration that can already start.** The intermediate
versions are upgrade hops. The baseline inventory is taken on the 2011 org and
the comparison is taken on 9.x, both of which work today. Widening the adapter
helps only an org that exposes nothing but IFD, or a team that wants an
inventory mid-chain.

## If this is ever reconsidered

Reopen when there is a concrete org to authenticate against and verify
end to end, and treat the two halves as separate issues:

- Claims/IFD for 2011 SOAP: design-gated. Decide the endpoint (the `/web`
  variant or the main contract) and the SAML-in-envelope flow from a recorded
  exchange with a real org. MFA and certificate trust stay out.
- 2013 to 2016 reads: measure `crm inventory extract` on such an org first,
  then widen profile detection and amend ADR 0031 with the measured versions.

Either way the profile stays read-only, the SOAP allowlist stays, and SOAP is
never used against a v9 or cloud profile (ADR 0003, ADR 0031).

## Prior requests

- #1200: "feat(legacy): IFD/claims auth for CRM 2011 and read support for 2013–2016 orgs"
