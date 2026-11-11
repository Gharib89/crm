# How-to: report

Register and manage custom D365 reports headlessly — list, get, create,
set-category, and delete `reports` records over the Web API without the Report
Wizard. See the [CLI reference](../reference/cli.md) for every flag.

Two kinds of report are supported:

- **SSRS RDL** (`--body-file <path>`) — uploads a Reporting Services Report
  Definition file; stored in `bodytext`; `reporttypecode 1`.
- **Link report** (`--url <url>`) — registers an external URL; stored in
  `bodyurl`; `reporttypecode 3`.

The CLI uploads RDL content verbatim — it does not author, validate, or
transform the XML, and does not check its data provider. Microsoft documents
Dataverse online as supporting **Fetch-based** reports (data provider
`MSCRMFETCH`), and its on-premises-to-online migration removes custom SQL-based
reports that were not converted to FetchXML. On-prem v9.x also runs SQL-based
reports. Report authoring and data-source configuration remain out of scope;
`crm inventory assess --target online` flags the custom SQL-based reports of an
inventory (see [Inventory](inventory.md)).

## List reports

```bash
crm report list
```

Output columns: `name`, `reportid`, `reporttypecode`, `ispersonal`. `list`
returns only these summary columns — to read the body (RDL text or link URL),
use `report get <id>`.

## Get a single report

```bash
crm report get 1111aaaa-2222-bbbb-3333-cccccccccccc
```

Returns the full report record including `bodytext` (RDL content) or `bodyurl`
(link URL) in the `--json` envelope, plus `data_provider`: how the RDL reads its
data, parsed from its data sources (not matched as text):

| `data_provider` | Meaning |
|---|---|
| `fetch` | Every data source uses the `MSCRMFETCH` provider. |
| `sql` | Any data source uses the `SQL` provider (decides the report even beside a Fetch or shared source). |
| `unknown` | Not decidable from the document; `data_provider_reason` says why: a shared data source reference defined only in Reporting Services (the reason names it), unparseable XML, a root that is not a report definition, an unrecognised provider (named), no data source, or no RDL body (a link report). |

Capture the RDL to source control:

```bash
crm --json report get <id> | jq -r '.data.bodytext' > report.rdl
```

## Create a report from an RDL file

```bash
crm report create --name "Sales Pipeline" --body-file pipeline.rdl --solution cwx_crmworx
```

`--solution` is required on `create` and `set-category` — a component created
without an explicit target solution would otherwise land only in the system
Default Solution. Pass `--solution Default` for a deliberate
Default-Solution-only write.

The `--filename` defaults to the file's basename; override with
`--filename "Custom Name.rdl"`. The server stores the RDL in `bodytext`
(`reporttypecode 1`).

## Create a link report

```bash
crm report create --name "External Dashboard" --url "https://example.com/dash" --solution cwx_crmworx
```

Registers the URL as a link report (`reporttypecode 3`, stored in `bodyurl`).
Exactly one of `--body-file` or `--url` must be given; passing both or neither
is a usage error.

## Make a report organization-wide

By default a newly created report is personal (`ispersonal=true`). Pass
`--org` to make it available to the whole organization:

```bash
crm report create --name "Sales Pipeline" --body-file pipeline.rdl --org --solution cwx_crmworx
```

`--org` sets `ispersonal=false` on the `reports` record. This is the
documented Web API path for org-wide visibility; the deprecated SDK message
`MakeAvailableToOrganizationReport` has no Web API binding and is never used.
To change an existing report's visibility, update the record directly:

```bash
crm entity update reports <id> --data '{"ispersonal":false}'
```

## Add a description

```bash
crm report create --name "Pipeline" --body-file pipeline.rdl \
    --description "SSRS report for sales pipeline by owner." --solution cwx_crmworx
```

## File under a report area

```bash
crm report set-category 1111aaaa-2222-bbbb-3333-cccccccccccc --category sales --solution cwx_crmworx
```

Creates a `reportcategory` record linking the report to one of four areas:
`sales`, `service`, `marketing`, or `administrative`. A report can be filed
under more than one area by calling `set-category` multiple times (`--solution`
scopes the `reportcategory` record and is required). To remove a category,
delete the `reportcategory` record directly:

```bash
crm --json report set-category <id> --category sales --solution cwx_crmworx   # returns reportcategoryid
crm entity delete reportcategories <reportcategoryid> --yes
```

## Preview without writing

`report create` honors the global `--dry-run` flag:

```bash
crm --dry-run report create --name "Pipeline" --body-file pipeline.rdl --org --solution cwx_crmworx
```

Returns `{_dry_run: true, would_create: {entity_set, body}}` with the fully
resolved request body; no report is created. `--solution` is still required
under `--dry-run` — it is validated before any backend call.

## Delete a report

```bash
crm report delete 1111aaaa-2222-bbbb-3333-cccccccccccc --yes
```

`delete` is destructive: omitting `--yes` prompts on a TTY, and under `--json`
or a non-TTY it fails fast with an error that names `--yes`.

Deletes the `reports` record permanently. Any associated `reportcategory`
records are cascade-deleted by the server.
