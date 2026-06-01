# Configure

The CLI authenticates with **NTLM (Windows Integrated)**. Set the `D365_*` env vars
(or `CRM_*` aliases):

```bash
export D365_URL="https://crm.contoso.local/contoso"
export D365_USERNAME="alice"
export D365_PASSWORD="..."        # never persisted to disk
export D365_DOMAIN="CONTOSO"      # optional if username is a UPN
```

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
