# Concepts

A few terms used throughout these docs.

## On-prem vs cloud

`crm` talks to two kinds of Dynamics 365 CE servers. It picks the auth scheme from
your server URL — you don't choose it manually.

| | On-premises | Cloud (Dataverse online) |
|---|---|---|
| URL shape | `https://crm.contoso.local/org` | `https://contoso.crm.dynamics.com` |
| Auth | **NTLM** (Windows Integrated) | **OAuth 2.0** client-credentials |
| You provide | username (+ domain) and password | tenant id, client id, client secret |
| API version | caps at v9.1 (auto-negotiated) | v9.2 |

The same `crm` commands work against both targets.

## Profile

A **profile** is a saved connection: the server URL, the auth scheme, the identity
fields, and the secret. You create one with [`crm profile add`](add-profile.md) and
switch between several with `crm profile use`. There is no `.env` file and no
credential environment variables — credentials live only in a profile.

State (profiles, cached tokens, completion scripts) lives under `~/.crm/`. The only
environment knob that affects connections is `CRM_HOME`, which relocates that
directory.

## Solution and publisher prefix

In Dynamics, customizations belong to a **solution**, and new schema names carry a
**publisher prefix** (e.g. `cwx_caseid`). Metadata-write commands need both. Attach
defaults to a profile so you don't pass them every time:

```bash
crm profile add --url ... --default-solution CRMWorx --publisher-prefix cwx --name crmworx
```

See [Configure & switch](configure.md) for the full field reference.
