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

### 2.2 Custom entities

Two tables: **SLA Policy** (organization-owned reference data) and **Support Ticket**
(user-owned, note- and activity-enabled). The schema name is given in PascalCase with
the `cwx_` prefix; the server derives the lowercase logical name and the entity-set
(plural) name used by the Web API:

```bash
crm --json metadata create-entity \
  --schema-name cwx_SLA --display "SLA Policy" --display-collection "SLA Policies" \
  --primary-attr cwx_Name --primary-label "Policy Name" \
  --ownership OrganizationOwned --has-notes --if-exists skip

crm --json metadata create-entity \
  --schema-name cwx_Ticket --display "Support Ticket" --display-collection "Support Tickets" \
  --primary-attr cwx_Name --primary-label "Ticket Title" \
  --ownership UserOwned --has-notes --has-activities --if-exists skip
```

```json
{
  "ok": true,
  "data": {
    "created": true,
    "schema_name": "cwx_SLA",
    "logical_name": "cwx_sla",
    "entity_set_name": "cwx_slas",
    "primary_attribute": "cwx_name",
    "solution": "CRMWorx",
    "published": true
  }
}
```

**Note the `entity_set_name`** in each response — `cwx_slas` and `cwx_tickets`. These
plural names are what you pass to `entity`/`query` commands later (§3–§4), not the
logical name. Verify both tables exist:

```bash
crm --json metadata entities --custom-only | grep -oE 'cwx_(sla|ticket)\b' | sort -u
```

```text
cwx_sla
cwx_ticket
```

### 2.3 Attributes (all kinds)

Add columns to both tables. The `cwx_sla` table gets two integer bounds, a global
picklist, and a boolean:

```bash
crm --json metadata add-attribute cwx_sla --kind integer \
  --schema-name cwx_ResponseHours --display "Response Hours" --min 0 --max 720 --if-exists skip
crm --json metadata add-attribute cwx_sla --kind integer \
  --schema-name cwx_ResolutionHours --display "Resolution Hours" --min 0 --max 2160 --if-exists skip
crm --json metadata add-attribute cwx_sla --kind picklist \
  --schema-name cwx_Tier --display "Tier" --optionset-name cwx_slatier --if-exists skip
crm --json metadata add-attribute cwx_sla --kind boolean \
  --schema-name cwx_Active --display "Active" --true-label Yes --false-label No --if-exists skip
```

The `cwx_ticket` table gets a memo, three global picklists, and three datetimes:

```bash
crm --json metadata add-attribute cwx_ticket --kind memo \
  --schema-name cwx_Description --display "Description" --max-length 4000 --if-exists skip
crm --json metadata add-attribute cwx_ticket --kind picklist \
  --schema-name cwx_Priority --display "Priority" --optionset-name cwx_priority --if-exists skip
crm --json metadata add-attribute cwx_ticket --kind picklist \
  --schema-name cwx_Severity --display "Severity" --optionset-name cwx_severity --if-exists skip
crm --json metadata add-attribute cwx_ticket --kind picklist \
  --schema-name cwx_Category --display "Category" --optionset-name cwx_ticketcategory --if-exists skip
crm --json metadata add-attribute cwx_ticket --kind datetime \
  --schema-name cwx_OpenedOn --display "Opened On" --if-exists skip
crm --json metadata add-attribute cwx_ticket --kind datetime \
  --schema-name cwx_ResolvedOn --display "Resolved On" --if-exists skip
crm --json metadata add-attribute cwx_ticket --kind datetime \
  --schema-name cwx_DueBy --display "Due By" --if-exists skip
```

Each returns the created column with its resolved logical name and type:

```json
{
  "ok": true,
  "data": {
    "created": true,
    "entity": "cwx_ticket",
    "schema_name": "cwx_Priority",
    "logical_name": "cwx_priority",
    "attribute_type": "Picklist",
    "solution": "CRMWorx",
    "published": true
  }
}
```

A picklist that references a global option set binds to it through the
`GlobalOptionSet` navigation property. Confirm the binding by expanding it from the
metadata endpoint:

```text
attr: cwx_priority -> GlobalOptionSet.Name: cwx_priority
```

!!! note "Two CLI defects fixed during this step"
    Building these columns against the live 9.1 server surfaced two `add-attribute`
    bugs, fixed inline (commit `cf7d41d`):

    - **Integer bounds** were serialized as floats (`--min 0` → `0.0`), which the
      server rejected for an `Edm.Int32` column. Integer/bigint bounds are now coerced
      to integers.
    - **Global picklists** were sent as an inline option set with `IsGlobal=true`,
      which the server rejects on attribute create. They now bind via
      `GlobalOptionSet@odata.bind`; on-prem 9.1 requires the option set's `MetadataId`
      GUID for the bind (the `Name` alternate key is rejected), so the CLI resolves
      `Name → MetadataId` first.

### 2.4 Relationships (1:N + 1:N + N:N)

Three relationships wire the model together: SLA→Ticket and Account→Ticket (each a
1:N that creates a lookup column on the ticket), and Ticket↔SystemUser (an N:N
"watchers" link with an intersect table).

```bash
# SLA -> Ticket (creates the cwx_sla lookup on the ticket)
crm --json metadata create-one-to-many \
  --schema-name cwx_sla_cwx_ticket \
  --referenced-entity cwx_sla --referencing-entity cwx_ticket \
  --lookup-schema cwx_SLA --lookup-display "SLA Policy" --if-exists skip

# Account -> Ticket (creates the cwx_customerid lookup on the ticket)
crm --json metadata create-one-to-many \
  --schema-name cwx_account_cwx_ticket \
  --referenced-entity account --referencing-entity cwx_ticket \
  --lookup-schema cwx_CustomerId --lookup-display "Customer" --if-exists skip

# Ticket <-> SystemUser (N:N watchers)
crm --json metadata create-many-to-many \
  --schema-name cwx_ticket_systemuser \
  --entity1 cwx_ticket --entity2 systemuser \
  --intersect-entity cwx_ticket_systemuser --if-exists skip
```

A 1:N response reports the created relationship and the lookup column the server
generated on the referencing entity:

```json
{
  "ok": true,
  "data": {
    "created": true,
    "kind": "OneToMany",
    "schema_name": "cwx_sla_cwx_ticket",
    "referencing_attribute": "cwx_sla",
    "solution": "CRMWorx"
  }
}
```

Verify all three (note `metadata relationships` lists an entity's one-to-many,
many-to-one, *and* many-to-many links) and publish:

```bash
crm --json metadata relationships cwx_ticket \
  | grep -oE 'cwx_(sla_cwx_ticket|account_cwx_ticket|ticket_systemuser)' | sort -u
crm --json solution publish-all
```

```text
cwx_account_cwx_ticket
cwx_sla_cwx_ticket
cwx_ticket_systemuser
```

!!! note "Three more CLI defects fixed during this step"
    Creating relationships against the live server exposed (commits `c62d57a`,
    `980ab95`):

    - **Wrong endpoint** — `create-one-to-many`/`create-many-to-many` POSTed to
      `CreateOneToManyRequest`/`CreateManyToManyRequest`, which are SDK message names,
      not Web API segments ("Resource not found for the segment ..."). They now POST
      to the `RelationshipDefinitions` entity set with an `@odata.type` discriminator
      (the 1:N lookup is a `Lookup` deep insert).
    - **Invalid default menu** — the 1:N associated-menu defaulted to `UseLabel` with
      no label, which the server rejects. Default is now `UseCollectionName`.
    - **Incomplete read-back / listing** — `ReferencingAttribute` came back `null`
      (the read-back didn't cast to the relationship subtype), and `metadata
      relationships` omitted the many-to-one side. Both fixed.

## 3. Seed data

_Filled in by the live run._

## 4. Read & verify

_Filled in by the live run._

## 5. Package the solution

_Filled in by the live run._

## 6. Teardown (optional, for a clean replay)

_Filled in by the live run._
