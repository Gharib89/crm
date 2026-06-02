# CRMWorx walkthrough

This guide builds **CRMWorx** — an IT-company ticketing platform with SLA — end to
end using the `crm` CLI, demonstrating every command group. Each step shows the real
command and its captured output from a live run against a Dynamics 365 CE on-premises
v9.1 server (credentials redacted).

The build order is: **option sets → entities → attributes → relationships → seed data
→ read/verify → package → (optional teardown)**.

## Prerequisites

- A reachable D365 CE on-prem server and NTLM credentials (via `.env`: `CRM_BASE_URL`,
  `CRM_USERNAME`, `CRM_PASSWORD`, `CRM_AUTH=ntlm`, `CRM_API_VERSION`). The password is
  read from `D365_PASSWORD`, with `CRM_PASSWORD` accepted as an alias — both names work.
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

Records go to the **entity-set (plural) name** — `cwx_slas`, `cwx_tickets`, `accounts`
— not the logical name. First two SLA policies; each `create` returns the full row,
including its `cwx_slaid` GUID:

```bash
crm --json entity create cwx_slas --data '{"cwx_name":"Gold 4h/24h","cwx_responsehours":4,"cwx_resolutionhours":24,"cwx_tier":3,"cwx_active":true}'
crm --json entity create cwx_slas --data '{"cwx_name":"Bronze 24h/120h","cwx_responsehours":24,"cwx_resolutionhours":120,"cwx_tier":1,"cwx_active":true}'
```

A customer account:

```bash
crm --json entity create accounts --data '{"name":"Contoso IT Dept"}'
```

Now a ticket that **binds both lookups** with `@odata.bind`, using the SLA and account
GUIDs from above. The bind target is the *navigation property name*, which is the
PascalCase lookup schema name — **`cwx_SLA`** and **`cwx_CustomerId`**, not the
lowercase logical names. If you guess wrong, read the real name from the relationship:

```bash
crm --json query odata \
  "RelationshipDefinitions(SchemaName='cwx_account_cwx_ticket')/Microsoft.Dynamics.CRM.OneToManyRelationshipMetadata" \
  --select ReferencingAttribute,ReferencingEntityNavigationPropertyName
# -> ReferencingAttribute: cwx_customerid, NavigationPropertyName: cwx_CustomerId
```

```bash
crm --json entity create cwx_tickets --data '{
  "cwx_name":"Laptop won'\''t boot",
  "cwx_description":"Dell 5420 no POST after update",
  "cwx_priority":3, "cwx_severity":2, "cwx_category":1,
  "cwx_CustomerId@odata.bind":"/accounts(c2c130c3-c05d-f111-b65d-00155d467b90)",
  "cwx_SLA@odata.bind":"/cwx_slas(00d955b7-c05d-f111-b65d-00155d467b90)"
}'
```

The response echoes the bound foreign keys:

```json
{ "ok": true, "data": {
    "cwx_ticketid": "a41cfedb-...",
    "cwx_name": "Laptop won't boot",
    "_cwx_sla_value": "00d955b7-...",
    "_cwx_customerid_value": "c2c130c3-..."
} }
```

Modify a record with `update` (PATCH) and `upsert` (PATCH with create-if-missing). With
no alternate key configured on the ticket, both target the record by id:

```bash
crm --json entity update cwx_tickets a41cfedb-c05d-f111-b65d-00155d467b90 \
  --data '{"cwx_resolvedon":"2026-06-01T12:00:00Z"}'
crm --json entity upsert cwx_tickets c8c8f8e4-c05d-f111-b65d-00155d467b90 \
  --data '{"cwx_resolvedon":"2026-06-01T15:30:00Z"}'
```

Both return `{"ok": true}`.

## 4. Read & verify

Four read paths. An **OData** query with a filter and projection:

```bash
crm --json query odata cwx_tickets \
  --filter "cwx_priority eq 3" --select cwx_name,cwx_severity --top 10
```

```json
{ "ok": true, "data": { "value": [
  { "cwx_name": "Laptop won't boot", "cwx_severity": 2 }
] } }
```

A **FetchXML** query (server-side XML query language) returning all tickets ordered by
name:

```bash
crm --json query fetchxml cwx_tickets --xml '
<fetch top="20">
  <entity name="cwx_ticket">
    <attribute name="cwx_name"/>
    <attribute name="cwx_priority"/>
    <order attribute="cwx_name"/>
  </entity>
</fetch>'
```

Returns both tickets (`Laptop won't boot` pri=3, `VPN drops every 10 min` pri=2).

A bulk **CSV export** to a file (the committed sample is
[`docs/artifacts/crmworx-tickets.csv`](../artifacts/crmworx-tickets.csv)):

```bash
crm data export cwx_tickets -o docs/artifacts/crmworx-tickets.csv \
  --select cwx_name,cwx_priority,cwx_severity,cwx_category
```

```text
output: docs/artifacts/crmworx-tickets.csv
format: csv
count: 2
```

And an **action/function** call — `RetrieveCurrentOrganization`:

```bash
crm --json action function RetrieveCurrentOrganization --params '{"AccessType":"Default"}'
```

```json
{ "ok": true, "data": { "Detail": {
  "FriendlyName": "MOCE",
  "OrganizationVersion": "9.1.44.15"
} } }
```

## 5. Package the solution

### Idempotency

Every metadata create uses `--if-exists skip`, so re-running the whole build is a safe
no-op. Re-running any create reports a skip rather than erroring or duplicating:

```bash
crm --json metadata create-optionset --name cwx_priority --display "CRMWorx Priority" \
  --option 1:Low --option 2:Normal --option 3:High --option 4:Critical --if-exists skip
crm --json metadata create-entity --schema-name cwx_SLA --display "SLA Policy" ... --if-exists skip
```

```json
{ "ok": true, "data": { "skipped": true, "exists": true } }
```

### Export

Export the unmanaged solution to a zip (committed sample:
[`docs/artifacts/crmworx.zip`](../artifacts/crmworx.zip)):

```bash
crm solution export CRMWorx -o docs/artifacts/crmworx.zip
```

```text
output: docs/artifacts/crmworx.zip
bytes: 83633
managed: False
solution: CRMWorx
action: ExportSolution
```

Verify the export contains the model. `solution components` returns component
**type + objectid (GUID)** rows — componenttype `9` is an option set, `1` is an
entity. CRMWorx contains all four option sets and both custom entities (plus the N:N
intersect entity):

```bash
crm --json solution components CRMWorx
# -> 8 components: 4 option sets (type 9) + 4 entities (type 1)
#    option sets  = cwx_priority, cwx_severity, cwx_ticketcategory, cwx_slatier
#    entities     = cwx_sla, cwx_ticket, cwx_ticket_systemuser (intersect), +1
```

!!! note "Solution-export defect fixed during this step"
    `solution export` only called `ExportSolutionAsync`, which **is not enabled on
    this on-prem 9.1 org** ("ExportSolutionAsync is not enabled for this org"). It now
    falls back to the synchronous `ExportSolution` action (which returns the zip bytes
    inline) when the async action is unavailable (commit `a87b8f2`). The response
    `action` field shows which path ran.

## 6. Views

A model-driven view is a **`savedquery`** row: `returnedtypecode` (the entity logical
name), `querytype` (`0` = public view), and two XML columns — `layoutxml` (grid columns
+ widths) and `fetchxml` (the query: columns, sort, filter). The grid's `object="<n>"`
attribute is the entity **ObjectTypeCode**, an org-specific integer — capture it live
rather than guessing (custom-entity OTCs on on-prem are typically ≥ 10000):

```bash
crm --json metadata entity cwx_ticket   # -> data.ObjectTypeCode = 10127
crm --json metadata entity cwx_sla       # -> data.ObjectTypeCode = 10126
```

!!! note "Column logical names ≠ option-set names"
    The picklist/lookup *columns* are `cwx_priority`, `cwx_severity`, `cwx_category`,
    `cwx_tier`, and the SLA lookup `cwx_sla` — **not** the global option-set names
    (`cwx_ticketcategory`, `cwx_slatier`). Confirm column logical names with
    `crm --json metadata attributes <entity>` before referencing them in FetchXml;
    the option set a picklist *binds to* can have a different name from the column.

Both XML blocks contain double quotes, so pass the record as a **JSON file**
(`--data-file`) rather than fighting shell quoting on `--data`. The LayoutXml + FetchXml
the server accepted for **Active Tickets** (newline-formatted here; stored single-line
in the JSON):

```xml
<grid name="resultset" object="10127" jump="cwx_name" select="1" icon="1" preview="1">
  <row name="result" id="cwx_ticketid">
    <cell name="cwx_name" width="220" />
    <cell name="cwx_priority" width="120" />
    <cell name="cwx_severity" width="120" />
    <cell name="cwx_customerid" width="180" />
    <cell name="createdon" width="140" />
    <cell name="statuscode" width="120" />
  </row>
</grid>
```

```xml
<fetch version="1.0" output-format="xml-platform" mapping="logical">
  <entity name="cwx_ticket">
    <attribute name="cwx_ticketid" />
    <attribute name="cwx_name" />
    <attribute name="cwx_priority" />
    <attribute name="cwx_severity" />
    <attribute name="cwx_customerid" />
    <attribute name="createdon" />
    <attribute name="statuscode" />
    <order attribute="cwx_name" descending="false" />
    <filter type="and">
      <condition attribute="statecode" operator="eq" value="0" />
    </filter>
  </entity>
</fetch>
```

`/tmp/cwx_view_active_tickets.json` wraps those two blocks as escaped single-line strings:

```json
{
  "name": "Active Tickets",
  "returnedtypecode": "cwx_ticket",
  "querytype": 0,
  "isdefault": false,
  "layoutxml": "<grid name=\"resultset\" object=\"10127\" ...></grid>",
  "fetchxml":  "<fetch ...><entity name=\"cwx_ticket\">...</entity></fetch>"
}
```

Preview the POST, guard against a duplicate (`savedquery` has no alternate key, so
filter by name + entity), then create:

```bash
crm --json --dry-run entity create savedqueries --data-file /tmp/cwx_view_active_tickets.json
crm --json query odata savedqueries \
  --filter "name eq 'Active Tickets' and returnedtypecode eq 'cwx_ticket'" --select name,savedqueryid
crm --json entity create savedqueries --data-file /tmp/cwx_view_active_tickets.json
```

```json
{ "ok": true, "data": { "savedqueryid": "72313649-6f5e-f111-b65d-00155d467b90", "...": "..." } }
```

Two more views follow the same guard → create flow — **Tickets by Priority** (cwx_ticket;
LayoutXml leads with `cwx_priority`, FetchXml ordered by `cwx_priority`) and **Active SLAs**
(`returnedtypecode": "cwx_sla"`, `object="10126"`; cells `cwx_name`, `cwx_tier`,
`cwx_responsehours`, `cwx_resolutionhours`):

```text
Active Tickets       72313649-6f5e-f111-b65d-00155d467b90
Tickets by Priority  74313649-6f5e-f111-b65d-00155d467b90
Active SLAs          76313649-6f5e-f111-b65d-00155d467b90
```

Publish so the views surface in the app, then read them back (the build's three plus the
two auto-generated defaults D365 created with the entity):

```bash
crm --json solution publish-all
crm --json query odata savedqueries \
  --filter "returnedtypecode eq 'cwx_ticket' and querytype eq 0" --select name,savedqueryid
```

```text
Active Tickets             72313649-6f5e-f111-b65d-00155d467b90
Tickets by Priority        74313649-6f5e-f111-b65d-00155d467b90
Active Support Tickets     055e2e32-dc59-4190-a9ff-0a8c1d88cf7f   (auto-generated default)
Inactive Support Tickets   9665360a-d424-4fd5-8a16-7a979e9988a4   (auto-generated default)
```

## 7. Forms

Forms are also `systemform` rows: `objecttypecode` (entity logical name), `type`
(`2` = main, `7` = quickCreate), and a `formxml` layout. Authoring main FormXml from
scratch has a high 9.1 rejection rate, so **clone-and-augment** the auto-generated main
form instead — fetch it, inject controls for the custom columns, PATCH it back.

### Main form (clone-and-augment)

Fetch the auto-generated "Information" form and note its `formid` + FormXml length:

```bash
crm --json query odata systemforms \
  --filter "objecttypecode eq 'cwx_ticket' and type eq 2" --select name,formid,formxml
# -> Information | 8f0db50d-b90c-4b4d-9ecb-f16f9e7db491 | formxml 1625 chars
```

The auto form has one tab/two columns: `cwx_name` + `ownerid` on the left, the notes
control on the right. Keep all of it intact and add one `<row>` per missing custom
column to the first section, copying the existing `<cell>`/`<control>` shape and swapping
`datafieldname` + `classid`. The control `classid` is per **AttributeType** (read it from
`crm --json metadata attributes cwx_ticket`):

| AttributeType | Control `classid` |
| --- | --- |
| String | `{4273EDBD-AC1D-40d3-9FB2-095C621B552D}` |
| Picklist | `{3EF39988-22BB-4f0b-BBBE-64B5A3748AEE}` |
| Lookup | `{270BD3DB-D9AF-4782-9025-509E298DEC0A}` |

Each added row (every `<cell>` needs a unique GUID `id`):

```xml
<row>
  <cell id="{c0ffee00-…}">
    <labels><label description="Priority" languagecode="1033" /></labels>
    <control id="cwx_priority" classid="{3EF39988-22BB-4f0b-BBBE-64B5A3748AEE}"
             datafieldname="cwx_priority" />
  </cell>
</row>
```

Five rows added — `cwx_priority`, `cwx_severity`, `cwx_category` (Picklist), `cwx_sla`,
`cwx_customerid` (Lookup) — taking the FormXml 1625 → 2834 chars. Preview, PATCH, publish:

```bash
crm --json --dry-run entity update systemforms 8f0db50d-… --data-file /tmp/cwx_ticket_mainform_patch.json
crm --json entity update systemforms 8f0db50d-… --data-file /tmp/cwx_ticket_mainform_patch.json
crm --json solution publish-all
```

Read back proves the five columns are now on the form:

```bash
crm --json query odata systemforms --filter "formid eq 8f0db50d-…" --select formxml
# cwx_priority True | cwx_severity True | cwx_category True | cwx_sla True | cwx_customerid True
```

### Quick-create form (`type=7`)

A quick-create form is authored from scratch (it's small): a single 100%-width column with
`cwx_name`, `cwx_priority`, `cwx_customerid`. Guard, create, publish, read back:

```bash
crm --json query odata systemforms --filter "objecttypecode eq 'cwx_ticket' and type eq 7" --select name,formid
crm --json entity create systemforms --data-file /tmp/cwx_ticket_qc_form.json
crm --json solution publish-all
```

```json
{ "ok": true, "data": { "formid": "94d4181e-705e-f111-b65d-00155d467b90", "...": "..." } }
```

!!! success "Quick-create via the Web API works on 9.1"
    On-prem 9.1's Web API accepted the `systemforms` POST with `type=7` on the first
    attempt — no manual-portal fallback was needed. Read-back confirms `cwx_priority`
    and `cwx_customerid` are present in the stored FormXml.

## Capability coverage

Every `crm` command group is exercised by this walkthrough:

| Group | Exercised by |
| --- | --- |
| connection | §1 — `whoami`, `connect`, `profiles`, `status` |
| session | `session info` (active profile, current entity set, last query) |
| metadata | §2 — `create-optionset`, `create-entity`, `add-attribute`, `create-one-to-many`, `create-many-to-many`, `relationships`, `list-optionsets`, `entities` |
| entity | §3 — `create`, `update`, `upsert` (lookups via `@odata.bind`) |
| query | §4 — `odata` + `fetchxml` |
| data | §4 — `export` (CSV) |
| action | §4 — `function RetrieveCurrentOrganization` |
| solution | §5 — `publish-all`, `export`, `components`, `list`, `info` |

## Teardown (optional — full reset for a clean replay)

> **Destructive.** Each command requires `--yes`; the `destructive_op_gate` PreToolUse
> hook blocks them otherwise. Deleting an entity drops the table **and every row in
> it**. Run these only to reset the org for a clean replay — CRMWorx is otherwise left
> deployed.

Reverse order — records are removed with their tables, then relationships and
attributes go with the entities, leaving only the global option sets to delete last:

```bash
crm --json metadata delete-entity cwx_ticket --yes   # drops the table + all rows + its relationships
crm --json metadata delete-entity cwx_sla --yes
crm --json metadata delete-optionset cwx_priority --yes
crm --json metadata delete-optionset cwx_severity --yes
crm --json metadata delete-optionset cwx_ticketcategory --yes
crm --json metadata delete-optionset cwx_slatier --yes
```

After teardown, `crm --json metadata entities --custom-only | grep -c cwx_` returns `0`.
The entire guide then replays from clean by re-running §2 onward — every create is
idempotent, so a partial replay is safe to resume.

This teardown + replay was run once against the live server to validate it: the
deletes dropped both tables and all four option sets (entities → `0`), then the full
§2–§3 build was re-applied, leaving CRMWorx deployed with fresh metadata ids. One note —
`delete-entity` has no `--if-exists skip`, so deleting an already-removed table returns
an error (`EntityMetadata ... does not exist`) rather than a no-op; delete in the
documented order and it won't recur.
