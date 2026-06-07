# Configure

The CLI authenticates with **NTLM (Windows Integrated)** for on-prem, or
**OAuth 2.0 client-credentials** for Dataverse online. Set the `D365_*` env vars
(or `CRM_*` aliases).

**On-prem (NTLM, default):**

```bash
export D365_URL="https://crm.contoso.local/contoso"
export D365_USERNAME="alice"
export D365_PASSWORD="..."        # not persisted by default (opt-in: connect/set-password --store-password)
export D365_DOMAIN="CONTOSO"      # optional if username is a UPN
```

**Online / Dataverse cloud (OAuth):**

```bash
export D365_URL="https://contoso.crm.dynamics.com"
export D365_AUTH="oauth"
export D365_TENANT_ID="<aad-tenant-id>"
export D365_CLIENT_ID="<app-registration-id>"
export D365_CLIENT_SECRET="..."   # not persisted by default (opt-in: connection set-password)
```

The OAuth scope and authority are derived automatically (public cloud only). The
app registration needs an **application user** with a security role in Dynamics.
The bearer token is cached under `~/.crm/` (`0600`) and reused until it expires;
username/password/domain are not used in this mode.

To avoid passing the secret on every run, store it once for a saved profile with
`crm connection set-password --profile <name> --store-password` (OS keyring) or
`--store-password-plaintext` (headless/CI). This works for the OAuth client secret
and the NTLM password alike. See the [README](https://github.com/Gharib89/crm#storing-credentials-once)
for the full storage reference.

Or save a reusable profile, including the default solution and publisher prefix used
by metadata write commands:

```bash
crm connection connect \
    --url https://crm.contoso.local/contoso \
    --username alice --domain CONTOSO \
    --default-solution CRMWorx --publisher-prefix cwx \
    --profile-name crmworx
```

State lives under `~/.crm/` (override with `CRM_HOME`). See the
[README](https://github.com/Gharib89/crm#configure) for the full reference.
