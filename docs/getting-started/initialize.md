# Initialize

`crm init` bootstraps a workspace — generate an env template, or run an
interactive wizard that saves a reusable connection profile.

## Generate an env template

```bash
crm init --template
```

Writes `.env.example` to the current directory (refuses to overwrite an
existing one). Copy it to `.env`, pick **one** auth block, and fill in values:

- **On-prem (NTLM, default):** `CRM_URL`, `CRM_USERNAME`, `CRM_PASSWORD`,
  `CRM_DOMAIN`, `CRM_AUTH=ntlm`.
- **Online / Dataverse cloud (OAuth):** `CRM_URL`, `CRM_AUTH=oauth`,
  `CRM_TENANT_ID`, `CRM_CLIENT_ID`, `CRM_CLIENT_SECRET`.

`CRM_*` and `D365_*` names are interchangeable — see [Configure](configure.md).

## Interactive wizard

```bash
crm init
```

Prompts for the server URL, auth scheme (`ntlm` | `kerberos` | `negotiate` |
`oauth`, default `ntlm`), credentials, and a profile name, then saves a
connection profile under `~/.crm/`. Secrets (password / client secret) are
**not** written to the profile — supply them via env vars at connect time.

The wizard is the quickest path to a working profile. For manual setup or the
full env-var reference, see [Configure](configure.md).
