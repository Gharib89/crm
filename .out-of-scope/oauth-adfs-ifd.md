# OAuth against on-prem AD FS / IFD

This project does not support OAuth authentication against an on-premises
Internet-Facing Deployment (IFD) backed by AD FS. OAuth is supported only
against Entra ID / Dataverse online; on-premises is served by NTLM (and the
kerberos/negotiate variants).

## Why this is out of scope

**No target needs it.** The two real deployments this CLI drives are an
intranet on-prem org (NTLM) and a Dataverse online org (OAuth client
credentials). Between them, every authentication path in use is already
covered. An IFD-with-AD-FS target is hypothetical — building configurable
authority/scope for it would be speculative configuration with no consumer,
which the project's simplicity-first stance rejects.

**The "easy fix" isn't the real fix.** The originating issue framed this as
making two hardcoded strings overridable:

```python
# crm/utils/d365_backend.py — _make_oauth_auth()
host = urllib.parse.urlparse(self.profile.url).netloc
scope = f"https://{host}/.default"                       # Entra-only convention
authority = f"https://login.microsoftonline.com/{tenant}"  # public cloud
```

Swapping the authority host and scope treats AD FS as "Entra with a different
URL." It is not:

- `msal`'s confidential-client / client-credentials flow targets Entra ID. AD FS
  uses a legacy OAuth2 dialect that wants a **`resource`** parameter (the org
  URL) rather than a `scope` of `https://<host>/.default` — the `.default`
  convention is Entra-specific.
- App-only (client-credentials) tokens from AD FS for Dataverse are not a
  standard `msal` path; getting a usable token likely needs an AD FS-specific
  request flow, possibly bypassing `msal` entirely.

So a working IFD+OAuth capability is real design work, not an authority-string
override — and it can only be verified against a **live AD FS** (mocked tests
would pass while the real flow fails, the same class of trap that bit the
online-OAuth work where `msal` does network discovery at construction).

## If this is ever reconsidered

Reopen only when there is a concrete IFD+AD FS target to authenticate against
and to verify the implementation end-to-end. The work is a new AD FS token
flow (resource-based), not a config knob — and NTLM already covers intranet
on-prem, so the bar is "a target that is *only* reachable via IFD OAuth."

## Prior requests

- #53 — "feat(auth): configurable OAuth authority/scope for on-prem IFD (AD FS), not just public cloud"
