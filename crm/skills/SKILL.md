---
name: crm
description: Operate a Microsoft Dynamics 365 Customer Engagement on-premises (v9.x) server from the shell. Wraps the real Dataverse Web API (OData v4) over HTTPS with NTLM auth. Use for record CRUD, OData/FetchXML queries, metadata browsing, solution lifecycle, and bulk CSV exports. Triggers on Dynamics 365, D365 CE, Dataverse on-prem, Web API, FetchXML, NTLM CRM.
---

# crm

A stateful CLI for **Microsoft Dynamics 365 Customer Engagement (on-premises),
version 9.x**. Every command issues a real HTTP request to the Dataverse Web API
at `<url>/api/data/v9.x/`. There is no local mocking — the live D365 server is
a hard runtime dependency.

## When to use

- Issue ad-hoc record CRUD (accounts, contacts, opportunities, custom entities).
- Run OData v4 (`$filter`/`$select`/`$top`) or FetchXML queries.
- Browse schema metadata (entity / attribute / relationship definitions).
- Export/import D365 solutions (`.zip`).
- Pull bulk datasets to CSV/JSON for analysis.
- Anything you'd otherwise script against the SOAP Organization Service.

## Install

```bash
pip install -e .         # from source (repo root)
which crm
crm --version
```

Python ≥ 3.9. Depends on `requests`, `requests_ntlm`, `click`, `prompt_toolkit`.

## Configure

The CLI authenticates with **NTLM (Windows Integrated)** for on-prem, or
**OAuth 2.0 client-credentials** for Dataverse online. Set env vars (canonical
`D365_*` form **or** `CRM_*` aliases — both work, matching common on-prem tooling):

```bash
# Canonical names
export D365_URL="https://crm.contoso.local/contoso"
export D365_USERNAME="alice"
export D365_PASSWORD="..."             # never persisted to disk
export D365_DOMAIN="CONTOSO"           # optional if username is a UPN
export D365_AUTH="ntlm"
export D365_API_VERSION="v9.2"         # online: v9.2 · on-prem caps at v9.1 (v9.2 → HTTP 501)

# Or the CRM_* aliases (same effect)
export CRM_BASE_URL="http://internalcrm.example.local/ORG"
export CRM_USERNAME="DOMAIN\\user"     # DOMAIN\user is parsed automatically
export CRM_PASSWORD="..."
export CRM_API_VERSION="v9.1"
export CRM_AUTH="ntlm"
```

For **Dataverse online**, set `D365_AUTH=oauth` and supply the app-registration
credentials instead of username/password/domain:

```bash
export D365_URL="https://contoso.crm.dynamics.com"
export D365_AUTH="oauth"
export D365_TENANT_ID="<aad-tenant-id>"
export D365_CLIENT_ID="<app-registration-id>"
export D365_CLIENT_SECRET="..."        # never persisted to disk
```

Scope (`https://<host>/.default`) and authority
(`https://login.microsoftonline.com/<tenant>`) are derived automatically; the
public cloud only. The bearer token is cached at `~/.crm/msal_token_cache.json`
(`0600`) and reused across invocations until expiry. The app registration needs
an **application user** with a security role in Dynamics. `CRM_*` aliases
(`CRM_TENANT_ID`, `CRM_CLIENT_ID`, `CRM_CLIENT_SECRET`) work here too.

A `.env` file in the current directory (or its parent, or the path in
`CRM_DOTENV`) is auto-loaded on every command. Real env vars take
precedence. Example `.env`:

```
CRM_BASE_URL=http://internalcrm.example.local/ORG
CRM_API_VERSION=v9.1
CRM_USERNAME=DOMAIN\user
CRM_PASSWORD=secret
CRM_AUTH=ntlm
```

Optional: save a named profile for repeat use.

```bash
crm connection connect \
    --url https://crm.contoso.local/contoso \
    --username alice --domain CONTOSO \
    --profile-name prod
```

State directory: `~/.crm/` (override with `CRM_HOME`).

## On-prem vs cloud

Same Dataverse Web API; **only auth + API version differ** — the same commands run
against both targets.

| | On-prem (NTLM) | Cloud / online (OAuth) |
|---|---|---|
| `D365_AUTH` | `ntlm` (also `kerberos` / `negotiate`) | `oauth` |
| API version | **v9.1 max** (`v9.2` → HTTP 501) | `v9.2` |
| `CreateMultiple` / `UpdateMultiple` / `DeleteMultiple` | not available | available |
| Solution import (sync + `ImportSolutionAsync` / `StageSolution`) | available | available |

**Pin your profile when both credential sets are present.** If the environment
defines both `CRM_*`/NTLM and `D365_*`/OAuth variables, a bare `crm` command lets
the `D365_*` vars override the active profile and silently connect to cloud. Always
pass `--profile <name>` and confirm the real target with
`crm --json connection whoami` (check the `@odata.context` host).

## Command Groups

| Group        | Commands                                                                                | Purpose                                       |
|--------------|-----------------------------------------------------------------------------------------|-----------------------------------------------|
| `connection` | `connect`, `status`, `whoami`, `test`, `profiles`, `disconnect`                         | Profiles + auth probe                          |
| `entity`     | `get`, `create`, `update`, `upsert`, `delete`, `associate`, `disassociate`, `set-lookup`, `clear-lookup` | Record CRUD + relationships               |
| `query`      | `odata`, `fetchxml`, `saved`, `user`                                                    | OData v4, FetchXML, savedquery, userquery     |
| `metadata`   | `entities`, `entity`, `attributes`, `attribute`, `picklist`, `relationships`            | Schema introspection + option set values      |
| `solution`   | `create-publisher`, `create`, `list`, `info`, `components`, `export`, `import`, `publish-all`, `publish` | Solution lifecycle + publish customizations    |
| `view`       | `create`                                                                                | System views (savedquery)                      |
| `app`        | `create`, `add-components`, `set-sitemap`                                               | Model-driven apps (appmodule)                  |
| `data`       | `export`                                                                                | Bulk CSV/JSON dataset export                   |
| `action`     | `function`, `invoke`                                                                    | Unbound OData functions/actions                |
| `session`    | `info`, `clear`, `history`                                                              | Local session state                            |
| _(top)_      | `apply -f spec.yaml`                                                                    | Declarative desired-state (publisher→…→views)  |
| _(top)_      | `service-document`                                                                      | List every entity set the server exposes       |

`crm <group> --help` lists the per-command options.

## Agent guidance — JSON mode

**Always pass `--json` from agent contexts.** It produces a stable envelope:

```json
{ "ok": true,  "data": ..., "meta": {...} }
{ "ok": false, "error": "Record Not Found", "meta": {"status": 404, "code": "0x80040217", "category": "not_found", "retryable": false} }
```

**Exit codes** — check `$?`, then read the envelope:
| code | meaning |
|------|---------|
| 0 | success (`ok: true`) |
| 1 | operational failure: server / validation / declined |
| 2 | usage error: bad or unknown flag (not JSON-wrapped) |

Non-zero = the operation did not take effect. Pass `--yes` to skip confirmations non-interactively.

Use `--dry-run` to preview the HTTP request (method/URL/headers/body) without issuing it.
This is the safe way to validate a mutation before commit.

In `--json` mode every dry-run carries `meta.dry_run: true` — the canonical signal for
detecting a preview (covers batch and poll list-shaped previews uniformly).

```bash
crm --json --dry-run entity create contacts --data '{"firstname":"Test"}'
```

REPL is the default when no subcommand is given. To stay in one-shot mode, always pass
a subcommand (e.g. `connection status`, `entity get`, etc.).

## Examples

### 1. Identity check

```bash
crm --json connection whoami
# -> {"ok": true, "data": {"UserId": "...", "BusinessUnitId": "...", "OrganizationId": "..."}}
```

### 2. Read with OData filter

```bash
crm --json query odata contacts \
    --filter "statecode eq 0" --select fullname,emailaddress1 --top 5
```

### 3. Create → update → delete a contact

```bash
# create
crm --json entity create contacts \
    --data '{"firstname":"Rafel","lastname":"Shillo"}'
# returns {"ok": true, "data": {"contactid": "<guid>", ...}}

# update
crm --json entity update contacts <guid> \
    --data '{"telephone1":"+1-555-0100"}'

# delete
crm --json entity delete contacts <guid> --yes
```

### 4. FetchXML query

```bash
crm --json query fetchxml accounts --xml '
<fetch top="10">
  <entity name="account">
    <attribute name="name"/>
    <attribute name="industrycode"/>
    <filter><condition attribute="statecode" operator="eq" value="0"/></filter>
  </entity>
</fetch>'
```

### 5. Browse metadata

```bash
crm --json metadata entities --custom-only --top 20
crm --json metadata attributes account
crm --json metadata attribute account industrycode
```

### 6. Export a solution

```bash
crm solution list --unmanaged
crm solution export MyCustomSolution -o /tmp/snap.zip
# returns {"output": "/tmp/snap.zip", "bytes": 123456, "managed": false, ...}
```

### 7. Bulk CSV export

```bash
crm data export opportunities -o /tmp/op.csv \
    --filter "statecode eq 0" --select name,estimatedvalue,closeprobability \
    --page-size 500
```

### 8. Call an arbitrary OData function

```bash
crm --json action function RetrieveCurrentOrganization \
    --params '{"AccessType":"Default"}'
```

### 9. Picklist / option set values (critical for agents writing valid records)

```bash
crm --json metadata picklist account industrycode
# returns {"OptionSet": {"Options": [{"Value": 1, "Label": {"UserLocalizedLabel": {"Label": "Accounting"}}}, ...]}}
```

### 10. Associate / disassociate records

```bash
# Associate a contact to an account's contact_customer_accounts collection (1:N)
crm entity associate accounts <account-guid> \
    contact_customer_accounts contacts <contact-guid>

# Set a single-valued lookup (N:1) — sets parent account on a contact
crm entity set-lookup contacts <contact-guid> \
    parentcustomerid_account accounts <account-guid>

# Disassociate (collection) — supply --related-set + --related-id
crm entity disassociate accounts <account-guid> \
    contact_customer_accounts \
    --related-set contacts --related-id <contact-guid>

# Clear a single-valued lookup
crm entity clear-lookup contacts <contact-guid> \
    parentcustomerid_account
```

### 11. Execute a saved system view by GUID

```bash
# First discover the saved query
crm query odata savedqueries \
    --filter "name eq 'Active Accounts'" --select savedqueryid,name

# Then execute it against the entity set
crm --json query saved accounts <savedqueryid>
```

### 12. Publish customizations after a metadata or solution change

```bash
crm solution publish-all
# or selectively:
crm solution publish --xml \
    '<importexportxml><entities><entity>account</entity></entities></importexportxml>'
```

#### Stage many changes, then publish once

By default each create/update metadata command auto-publishes. Use the global
`--stage-only` flag (or `CRM_STAGE_ONLY=1`) to suppress publishing across a batch
of changes, then run `publish-all` once at the end. `--stage-only` forces every
create/update command to `--no-publish`; in `--json` mode the envelope `meta`
records `staged: true`. Combining `--stage-only` with an explicit `--publish` is
rejected.

```bash
crm --stage-only metadata add-attribute new_widget \
    --kind string --schema-name new_Label --display Label --max-length 100
crm --stage-only metadata create-optionset --name new_priority --display Priority \
    --option 1:Low --option 2:High
# ... more staged changes ...
crm solution publish-all   # single publish for all staged customizations
```

### 13. Inspect the server's entity sets

```bash
crm --json service-document
# returns {"value": [{"name": "accounts", "url": "accounts", ...}, ...]}
```

## Destructive operations — `--yes` confirm contract

These verbs permanently delete or cancel server-side state. Every one accepts a
`--yes` flag to skip the interactive confirmation; without `--yes` in a non-TTY
context they abort safely and emit `{"ok": false, "error": "aborted by user"}`
(exit 1). Always pass `--yes` when invoking them non-interactively (e.g. from an
agent), and only after you have confirmed intent.

| Command | What it destroys |
| --- | --- |
| `crm metadata delete-entity <logical>` | A custom entity (table) and ALL its rows |
| `crm metadata delete-optionset <name>` | A custom global option set |
| `crm metadata delete-attribute ...` | A column (when shipped) |
| `crm metadata delete-relationship ...` | A relationship (when shipped) |
| `crm entity delete <set> <guid>` | A single record |
| `crm solution job-cancel <id>` | A running async job |
| `crm async cancel <id>` | A pending/suspended async operation |

A deterministic Claude Code PreToolUse hook (`.claude/hooks/destructive_op_gate.py`)
hard-blocks any of these Bash invocations (exit 2, reason on stderr) unless the
`--yes` token is present — so the gate holds even if a prompt instruction is
ignored. The hook matches by verb name, so not-yet-shipped delete verbs are
gated the moment they ship.

## Solution scaffolding — publisher + solution

```bash
# Publisher: --prefix is 2–8 alphanumeric, letter-first, not 'mscrm';
# --option-value-prefix is an integer 10000–99999.
crm --json solution create-publisher --name crmworx --display CRMWorx \
    --prefix cwx --option-value-prefix 30000 --if-exists skip

# Solution: --publisher XOR --publisher-id (mutually exclusive).
crm --json solution create --name CRMWorx --publisher crmworx --if-exists skip
```

With a named profile active, both verbs auto-wire `publisher_prefix` (from
`create-publisher`) and `default_solution` (from `create`) back into it, so
later `metadata create-*` commands target that prefix/solution by default. Pass
`--no-set-default` to opt out. See `docs/adr/0002-create-verbs-auto-wire-profile.md`.

## Views — `view create` (savedquery)

```bash
# --otc is the entity ObjectTypeCode (from `metadata entity <name>`); --column
# is repeatable 'logical[:width]' (order preserved); --order sets ascending sort
# attribute; --filter-active scopes to statecode=0; --if-exists [error|skip].
crm --json view create cwx_ticket --name "Active Tickets" --otc 10127 \
    --column "cwx_name:220" --column "cwx_priority:120" \
    --filter-active --if-exists skip
```

The LayoutXml `object` attribute is the entity ObjectTypeCode (OTC) — get it from `metadata entity <name>`.

## Model-driven apps — `app` (appmodule)

```bash
# create: --unique-name is publisher-prefixed, e.g. 'cwx_crmworx'.
crm --json app create --name CRMWorx --unique-name cwx_crmworx --if-exists skip

# add-components: APP_ID positional + repeatable --component 'kind:guid'.
# kind ∈ view|chart|form|dashboard|sitemap|bpf (NOT 'entity' — tables surface
# via the sitemap's Entity= subareas, not AddAppComponents).
crm --json app add-components <appmoduleid> \
    --component view:<savedqueryid> --component chart:<savedqueryvisualizationid>

# set-sitemap: SITEMAP_NAME positional is the sitemap's descriptive name
# (stored as sitemapname); --unique-name is the app's uniquename and sets
# sitemapnameunique to auto-associate the sitemap with that app.
crm --json app set-sitemap "CRMWorx Sitemap" --xml-file /tmp/sitemap.xml --unique-name cwx_crmworx
```

## Declarative apply — `apply -f spec.yaml`

Stand up a whole table from one YAML/JSON spec instead of many imperative
commands. `apply` runs the metadata cores in dependency order (publisher →
solution → entities → option sets → attributes → relationships → views), each
with `if_exists=skip`, and publishes **once** at the end — re-applying an
unchanged spec is a no-op.

```bash
crm --json apply -f project.yaml              # create/skip, publish once
crm --dry-run --json apply -f project.yaml    # plan: dependents reported "planned"
crm --stage-only --json apply -f project.yaml # create without publishing
```

Emits `{ok, data:{applied, skipped, planned, failed}, meta:{staged}}`; each entry
is `{kind, name}` (a failed entry adds `error`). Metadata POSTs are
non-transactional, so a failure aborts-and-reports and leaves
staged-but-unpublished residue. A new table's views may report `planned` until
the first publish assigns its ObjectTypeCode — re-apply to land them. Full spec
schema: `docs/how-to/apply.md`.

```yaml
publisher: {unique_name: mocepub, prefix: moce, option_value_prefix: 10000}
solution:  {unique_name: MoceCore}
optionsets:
  - {name: moce_priority, display_name: Priority, options: [{value: 100000000, label: Low}]}
entities:
  - schema_name: moce_Project
    display_name: Project
    primary_attr: {schema_name: moce_Name, label: Name}
    attributes:
      - {kind: string,   schema_name: moce_Code,     display_name: Code, max_length: 100}
      - {kind: picklist, schema_name: moce_Priority, display_name: Priority, optionset_name: moce_priority}
      - {kind: lookup,   schema_name: moce_Owner,    display_name: Owner, target_entity: systemuser}
    views:
      - {name: Active Projects, columns: [moce_name, moce_code]}
```

## Agent guidance — record-create payloads (`@odata.bind`)

When constructing `entity create` payloads, lookup fields require an `@odata.bind`
suffix on the **navigation-property name** (the PascalCase schema name, e.g.
`cwx_CustomerId@odata.bind`), **not** the lowercase logical attribute.
A picklist bound to a global option set binds through `GlobalOptionSet@odata.bind`,
and on-prem 9.1 requires the option set's `MetadataId` GUID there (the `Name`
alternate key is rejected).

## Errors & recovery

`D365Error` wraps any HTTP / API failure. In `--json` mode it becomes
`{"ok": false, "error": "...", "meta": {"status": N, "code": "0x...", "category": "...", "retryable": bool}}`.

`meta.category` is a closed enum; `meta.retryable` flags the transient classes. The
backend auto-retries the `transport_error` / `throttled` (429) / `server_error` (5xx)
classes, so for those `retryable: true` is a post-exhaustion hint — act on it only
after the error surfaces. `concurrency_conflict` (412) is the exception: the backend
does NOT auto-retry it; the caller must refetch a fresh ETag and retry.

| `category` | trigger | `retryable` | recovery |
|---|---|---|---|
| `not_found` | 404 / code `0x80040217` | no | record doesn't exist, or wrong entity set / GUID |
| `auth_failed` | 401 | no | NTLM: check `D365_DOMAIN\D365_USERNAME` + password. OAuth: app-registration (client id/secret, tenant) + an application user with a role |
| `forbidden` | 403 | no | the user lacks the privilege for that operation in the CRM security model |
| `concurrency_conflict` | 412 | yes | another change won the race — retrieve a fresh ETag and retry |
| `duplicate_detected` | code `0x80040237` | no | a matching record exists; merge/resolve or pass `--suppress-dup-detection` |
| `validation` | other 4xx (e.g. 400), or a status-less client-side error (bad CLI input, schema/spec validation) | no | fix the request: bad payload / CLI input, alternate key, or OData syntax |
| `throttled` | 429 | yes | service-protection limit; the backend honors `Retry-After` |
| `server_error` | 5xx | yes | transient server fault |
| `transport_error` | request never got a response (network / TLS / timeout); message starts `HTTP transport failure` | yes | network / TLS / timeout before any response reached the client |

## Hard constraints

- **NTLM (on-prem) or OAuth client-credentials (online).** IFD/Claims, certificate
  credentials, and other OAuth flows (device-code, interactive, ROPC) are out of
  scope; OAuth targets the public cloud only.
- **D365 CE on-prem 9.x or Dataverse online.** Same Web API; only auth differs.
- **Real server required.** No local mocking. E2E tests fail loudly when `D365_URL` is unset.
- **Credentials are never persisted; the OAuth bearer token is.** The NTLM password
  / OAuth client secret live in `D365_PASSWORD` / `D365_CLIENT_SECRET` or `--password`
  only — never on a saved profile. The OAuth **bearer token** (a secret until it
  expires) IS cached on disk at `~/.crm/msal_token_cache.json` (`0600`).

## Related files

- Full SOP: `D365.md`
- Test plan + results: `crm/tests/TEST.md`
- README with installation walkthrough: `README.md`
