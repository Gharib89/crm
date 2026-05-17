---
name: cli-anything-d365
description: Operate a Microsoft Dynamics 365 Customer Engagement on-premises (v9.x) server from the shell. Wraps the real Dataverse Web API (OData v4) over HTTPS with NTLM auth. Use for record CRUD, OData/FetchXML queries, metadata browsing, solution lifecycle, and bulk CSV exports. Triggers on Dynamics 365, D365 CE, Dataverse on-prem, Web API, FetchXML, NTLM CRM.
---

# cli-anything-d365

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
pip install cli-anything-d365   # or `pip install -e .` from source
which cli-anything-d365
cli-anything-d365 --version
```

Python ≥ 3.9. Depends on `requests`, `requests_ntlm`, `click`, `prompt_toolkit`.

## Configure

The CLI authenticates with **NTLM (Windows Integrated)**. Set env vars (canonical
`D365_*` form **or** `CRM_*` aliases — both work, matching common on-prem tooling):

```bash
# Canonical names
export D365_URL="https://crm.contoso.local/contoso"
export D365_USERNAME="alice"
export D365_PASSWORD="..."             # never persisted to disk
export D365_DOMAIN="CONTOSO"           # optional if username is a UPN
export D365_AUTH="ntlm"
export D365_API_VERSION="v9.2"         # default; v9.0 / v9.1 / v9.2 all valid

# Or the CRM_* aliases (same effect)
export CRM_BASE_URL="http://internalcrm.example.local/ORG"
export CRM_USERNAME="DOMAIN\\user"     # DOMAIN\user is parsed automatically
export CRM_PASSWORD="..."
export CRM_API_VERSION="v9.1"
export CRM_AUTH="ntlm"
```

A `.env` file in the current directory (or its parent, or the path in
`CLI_ANYTHING_DOTENV`) is auto-loaded on every command. Real env vars take
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
cli-anything-d365 connection connect \
    --url https://crm.contoso.local/contoso \
    --username alice --domain CONTOSO \
    --profile-name prod
```

State directory: `~/.cli-anything-d365/` (override with `CLI_ANYTHING_D365_HOME`).

## Command Groups

| Group        | Commands                                                                                | Purpose                                       |
|--------------|-----------------------------------------------------------------------------------------|-----------------------------------------------|
| `connection` | `connect`, `status`, `whoami`, `test`, `profiles`, `disconnect`                         | Profiles + auth probe                          |
| `entity`     | `get`, `create`, `update`, `upsert`, `delete`, `associate`, `disassociate`, `set-lookup`, `clear-lookup` | Record CRUD + relationships               |
| `query`      | `odata`, `fetchxml`, `saved`, `user`                                                    | OData v4, FetchXML, savedquery, userquery     |
| `metadata`   | `entities`, `entity`, `attributes`, `attribute`, `picklist`, `relationships`            | Schema introspection + option set values      |
| `solution`   | `list`, `info`, `components`, `export`, `import`, `publish-all`, `publish`              | Solution lifecycle + publish customizations    |
| `data`       | `export`                                                                                | Bulk CSV/JSON dataset export                   |
| `action`     | `function`, `invoke`                                                                    | Unbound OData functions/actions                |
| `session`    | `info`, `clear`, `history`                                                              | Local session state                            |
| _(top)_      | `service-document`                                                                      | List every entity set the server exposes       |

`cli-anything-d365 <group> --help` lists the per-command options.

## Agent guidance — JSON mode

**Always pass `--json` from agent contexts.** It produces a stable envelope:

```json
{ "ok": true,  "data": ..., "meta": {...} }
{ "ok": false, "error": "Record Not Found", "meta": {"status": 404, "code": "0x80040217"} }
```

Use `--dry-run` to preview the HTTP request (method/URL/headers/body) without issuing it.
This is the safe way to validate a mutation before commit.

```bash
cli-anything-d365 --json --dry-run entity create contacts --data '{"firstname":"Test"}'
```

REPL is the default when no subcommand is given. To stay in one-shot mode, always pass
a subcommand (e.g. `connection status`, `entity get`, etc.).

## Examples

### 1. Identity check

```bash
cli-anything-d365 --json connection whoami
# -> {"ok": true, "data": {"UserId": "...", "BusinessUnitId": "...", "OrganizationId": "..."}}
```

### 2. Read with OData filter

```bash
cli-anything-d365 --json query odata contacts \
    --filter "statecode eq 0" --select fullname,emailaddress1 --top 5
```

### 3. Create → update → delete a contact

```bash
# create
cli-anything-d365 --json entity create contacts \
    --data '{"firstname":"Rafel","lastname":"Shillo"}'
# returns {"ok": true, "data": {"contactid": "<guid>", ...}}

# update
cli-anything-d365 --json entity update contacts <guid> \
    --data '{"telephone1":"+1-555-0100"}'

# delete
cli-anything-d365 --json entity delete contacts <guid> --yes
```

### 4. FetchXML query

```bash
cli-anything-d365 --json query fetchxml accounts --xml '
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
cli-anything-d365 --json metadata entities --custom-only --top 20
cli-anything-d365 --json metadata attributes account
cli-anything-d365 --json metadata attribute account industrycode
```

### 6. Export a solution

```bash
cli-anything-d365 solution list --unmanaged
cli-anything-d365 solution export MyCustomSolution -o /tmp/snap.zip
# returns {"output": "/tmp/snap.zip", "bytes": 123456, "managed": false, ...}
```

### 7. Bulk CSV export

```bash
cli-anything-d365 data export opportunities -o /tmp/op.csv \
    --filter "statecode eq 0" --select name,estimatedvalue,closeprobability \
    --page-size 500
```

### 8. Call an arbitrary OData function

```bash
cli-anything-d365 --json action function RetrieveCurrentOrganization \
    --params '{"AccessType":"Default"}'
```

### 9. Picklist / option set values (critical for agents writing valid records)

```bash
cli-anything-d365 --json metadata picklist account industrycode
# returns {"OptionSet": {"Options": [{"Value": 1, "Label": {"UserLocalizedLabel": {"Label": "Accounting"}}}, ...]}}
```

### 10. Associate / disassociate records

```bash
# Associate a contact to an account's contact_customer_accounts collection (1:N)
cli-anything-d365 entity associate accounts <account-guid> \
    contact_customer_accounts contacts <contact-guid>

# Set a single-valued lookup (N:1) — sets parent account on a contact
cli-anything-d365 entity set-lookup contacts <contact-guid> \
    parentcustomerid_account accounts <account-guid>

# Disassociate (collection) — supply --related-set + --related-id
cli-anything-d365 entity disassociate accounts <account-guid> \
    contact_customer_accounts \
    --related-set contacts --related-id <contact-guid>

# Clear a single-valued lookup
cli-anything-d365 entity clear-lookup contacts <contact-guid> \
    parentcustomerid_account
```

### 11. Execute a saved system view by GUID

```bash
# First discover the saved query
cli-anything-d365 query odata savedqueries \
    --filter "name eq 'Active Accounts'" --select savedqueryid,name

# Then execute it against the entity set
cli-anything-d365 --json query saved accounts <savedqueryid>
```

### 12. Publish customizations after a metadata or solution change

```bash
cli-anything-d365 solution publish-all
# or selectively:
cli-anything-d365 solution publish --xml \
    '<importexportxml><entities><entity>account</entity></entities></importexportxml>'
```

### 13. Inspect the server's entity sets

```bash
cli-anything-d365 --json service-document
# returns {"value": [{"name": "accounts", "url": "accounts", ...}, ...]}
```

## Errors & recovery

- `D365Error` is the wrapper for any HTTP / API failure. In `--json` mode it
  becomes `{"ok": false, "error": "...", "meta": {"status": N, "code": "0x..."}}`.
- `404` with code `0x80040217` → record doesn't exist (or wrong entity set / GUID).
- `401` → auth failed; verify `D365_DOMAIN\D365_USERNAME` and password.
- `403` → user lacks the privilege for that operation in CRM security model.
- `400` with `OptimisticConcurrencyVersionMismatch` → another user changed the record;
  retrieve fresh and retry.

## Hard constraints

- **NTLM only.** OAuth/IFD/Claims is out of scope for this harness.
- **D365 CE on-prem 9.x only.** Online (Dataverse cloud) auth differs; not configured here.
- **Real server required.** No local mocking. E2E tests fail loudly when `D365_URL` is unset.
- **Passwords are never persisted.** They live in `D365_PASSWORD` or `--password` only.

## Related files

- Full SOP: `d365/agent-harness/D365.md`
- Test plan + results: `cli_anything/d365/tests/TEST.md`
- README with installation walkthrough: `cli_anything/d365/README.md`
