---
status: accepted
---

# Delegated Entra sign-in: a new scheme, a per-tenant app, an encrypted token cache

Cloud profiles could only use app sign-in (client credentials), so the server
applied an application user's roles, not the developer's. We add **delegated
sign-in** as its own `auth_scheme`, `oauth-user`. It signs in with a public-client
Entra app that **each tenant registers itself**. Its `tenant_id` and `client_id`
live on the profile, which stores no secret. The token cache is an **encrypted
file whose key sits in the OS keyring**. Device code runs only when asked for
explicitly (#1129).

## Decision

- **A new scheme, not a mode on `oauth`.** Every place that handles secrets
  branches on `auth_scheme`. A flag on `oauth` would make each of those places
  read a second field. About half of the `== "oauth"` checks mean "this is a cloud
  profile", so a prep refactor first routes them through one `is_cloud` predicate.
  Without it, a new scheme would silently slip past the cloud gates.
- **Each tenant registers its own app.** The project ships no multi-tenant
  `client_id`. Tenants that allow consent only for verified publishers would block
  a shipped app, and the project would own that app forever. A shared default ID
  also looks like a phishing setup. Microsoft's sample client and borrowed
  first-party IDs were rejected for the same reasons, plus doubts about their
  loopback redirect. The setup page tells the registrant to grant admin consent
  once.
- **The cache is an encrypted file with its key in the keyring.** A delegated
  cache holds a refresh token that carries the user's full Dataverse role. A
  one-account cache serialized to about 7.6 KB, or 15 KB as UTF-16. That is about
  six times Windows Credential Manager's 2,560-byte blob limit, so the whole cache
  cannot go in the keyring. `msal-extensions` would need PyGObject on Linux, which
  the PyInstaller binary does not carry. AES-256-GCM comes from `cryptography`,
  which `msal` already depends on. On a host with no OS credential store the
  cache falls back to a plaintext `0600` file with the existing plaintext warning,
  the same rule profile secrets follow. App sign-in's cache stays as it is.
- **Device code only behind `--device-code`.** Microsoft recommends that tenants
  block device code flow. A session started by device code stays protocol-tracked,
  so a later refresh can fail long after sign-in. An automatic fallback would
  create such sessions silently. With no browser and no flag, `crm profile login`
  fails at once and names the flag.
- **Only `crm profile login` interacts.** Every other command acquires a token
  silently from the cache. On failure it raises a stable `reauth_required` error
  that names `crm profile login`. It never opens a browser and never prints a
  token.

## Consequences

- Every tenant that wants delegated sign-in needs one app registration before
  first use.
- The live proof is owner-run under ADR 0030, in the tenant that already hosts
  the cloud test org. It signs in as a dedicated non-admin test user, since its
  last step revokes that user's sessions. The `--device-code` leg passes either
  way: if the tenant's Conditional Access blocks device code, crm must report
  the block clearly.
- This work ranks behind on-prem work, since on-prem is the priority target. The
  `is_cloud` refactor can go first because it changes no behavior.
- Windows WAM broker support (`msal[broker]`) touches the five PyInstaller sites.
  It is added only if the live proof hits a Conditional Access policy that
  requires a compliant device.
