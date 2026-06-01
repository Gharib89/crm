# CRMWorx walkthrough

This guide builds **CRMWorx** — an IT-company ticketing platform with SLA — end to
end using the `crm` CLI, demonstrating every command group. Each step shows the real
command and its captured output from a live run against a Dynamics 365 CE on-premises
v9.1 server (credentials redacted).

The build order is: **option sets → entities → attributes → relationships → seed data
→ read/verify → package → (optional teardown)**.

## Prerequisites

- A reachable D365 CE on-prem server and NTLM credentials (via `.env`: `CRM_BASE_URL`,
  `CRM_USERNAME`, `CRM_PASSWORD`, `CRM_AUTH=ntlm`, `CRM_API_VERSION`).
- **A `CRMWorx` *unmanaged* solution and a publisher with prefix `cwx` must already
  exist on the server.** The CLI cannot create either today — it only lists, exports,
  imports, and publishes solutions (filed as
  [#34](https://github.com/Gharib89/crm/issues/34)). Create both once in the D365 web
  UI before running this guide:
    1. **Settings → Customizations → Publishers → New** — Display Name `CRMWorx`,
       Prefix `cwx`, save.
    2. **Settings → Solutions → New** — Display Name `CRMWorx`, Name `CRMWorx`,
       Publisher = the `cwx` publisher, Version `1.0.0.0`, save.

## 1. Pre-flight & connection

Confirm reachability and identity. A non-zero exit (e.g. `401`) means the
`DOMAIN\username` credentials are wrong — fix them before continuing.

```bash
crm --json connection whoami
```

```json
{
  "ok": true,
  "data": {
    "@odata.context": ".../api/data/v9.1/$metadata#Microsoft.Dynamics.CRM.WhoAmIResponse",
    "BusinessUnitId": "078a426a-8339-f111-b65d-00155d467b90",
    "UserId": "f890426a-8339-f111-b65d-00155d467b90",
    "OrganizationId": "b948cd5f-8339-f111-b65d-00155d467b90"
  }
}
```

Save a **targeting profile** so every mutating metadata command lands in the `CRMWorx`
solution and uses the `cwx` schema-name prefix by default. `connect` saves the profile
and validates the credentials with a WhoAmI call (the password is read from the
`D365_PASSWORD` environment variable, never passed on the command line):

```bash
crm --json connection connect \
  --url "$CRM_BASE_URL" --username "$CRM_USERNAME" \
  --api-version v9.1 \
  --default-solution CRMWorx --publisher-prefix cwx \
  --profile-name crmworx
```

```json
{
  "ok": true,
  "data": {
    "ok": true,
    "user_id": "f890426a-8339-f111-b65d-00155d467b90",
    "api_base": "http://<server>/<org>/api/data/v9.1/",
    "api_version": "v9.1"
  },
  "meta": { "profile": "crmworx" }
}
```

Verify the active profile resolves the solution and prefix:

```bash
crm --json connection profiles
crm --json connection status
```

```json
{
  "ok": true,
  "meta": {
    "profiles": [
      { "name": "crmworx", "default_solution": "CRMWorx", "publisher_prefix": "cwx" }
    ]
  }
}
```

`connection status` confirms `active_profile: crmworx` with `default_solution: CRMWorx`
and `publisher_prefix: cwx`. From here, `metadata create-*` commands target `CRMWorx`
automatically (override per-command with `--solution`, or hard-fail when none resolves
with `--require-solution` / `CRM_REQUIRE_SOLUTION`).

## 2. Metadata build (option sets → entities → attributes → relationships)

### 2.1 Global option sets

CRMWorx uses four global (reusable) option sets. Preview the request first with
`--dry-run` to confirm the shape and that the `CRMWorx` solution is targeted — note the
`MSCRM.SolutionUniqueName: CRMWorx` header and the `GlobalOptionSetDefinitions` endpoint:

```bash
crm --json --dry-run metadata create-optionset \
  --name cwx_priority --display "CRMWorx Priority" \
  --option 1:Low --option 2:Normal --option 3:High --option 4:Critical \
  --if-exists skip
```

```json
{
  "ok": true,
  "data": {
    "_dry_run": true,
    "method": "POST",
    "url": ".../api/data/v9.1/GlobalOptionSetDefinitions",
    "headers": { "MSCRM.SolutionUniqueName": "CRMWorx" },
    "body": {
      "@odata.type": "Microsoft.Dynamics.CRM.OptionSetMetadata",
      "Name": "cwx_priority", "IsGlobal": true, "OptionSetType": "Picklist",
      "Options": [ { "Value": 1, "Label": { "...": "Low" } }, "...etc" ]
    }
  }
}
```

Now create all four. `--if-exists skip` makes each create idempotent (proven in §5):

```bash
crm --json metadata create-optionset --name cwx_priority --display "CRMWorx Priority" \
  --option 1:Low --option 2:Normal --option 3:High --option 4:Critical --if-exists skip
crm --json metadata create-optionset --name cwx_severity --display "CRMWorx Severity" \
  --option 1:Minor --option 2:Major --option 3:Critical --if-exists skip
crm --json metadata create-optionset --name cwx_ticketcategory --display "CRMWorx Category" \
  --option 1:Hardware --option 2:Software --option 3:Network --option 4:Access --if-exists skip
crm --json metadata create-optionset --name cwx_slatier --display "CRMWorx SLA Tier" \
  --option 1:Bronze --option 2:Silver --option 3:Gold --if-exists skip
```

Each returns `created: true` with the new metadata id, the target solution, and
`published: true`:

```json
{
  "ok": true,
  "data": {
    "created": true,
    "name": "cwx_priority",
    "metadata_id_url": ".../GlobalOptionSetDefinitions(a2ca6b21-...)",
    "solution": "CRMWorx",
    "published": true
  }
}
```

Verify all four landed:

```bash
crm --json metadata list-optionsets --custom-only | grep -oE 'cwx_[a-z]+' | sort -u
```

```text
cwx_priority
cwx_severity
cwx_slatier
cwx_ticketcategory
```

## 3. Seed data

_Filled in by the live run._

## 4. Read & verify

_Filled in by the live run._

## 5. Package the solution

_Filled in by the live run._

## 6. Teardown (optional, for a clean replay)

_Filled in by the live run._
