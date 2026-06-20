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
transform the XML. Dataverse online only accepts RDLs whose data source uses
the **fetch data provider**; on-prem v9.x accepts the standard D365 data
source. Report authoring and data-source configuration remain out of scope.

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
(link URL) in the `--json` envelope. Capture the RDL to source control:

```bash
crm --json report get <id> | jq -r '.data.bodytext' > report.rdl
```

## Create a report from an RDL file

```bash
crm report create --name "Sales Pipeline" --body-file pipeline.rdl
```

The `--filename` defaults to the file's basename; override with
`--filename "Custom Name.rdl"`. The server stores the RDL in `bodytext`
(`reporttypecode 1`).

## Create a link report

```bash
crm report create --name "External Dashboard" --url "https://example.com/dash"
```

Registers the URL as a link report (`reporttypecode 3`, stored in `bodyurl`).
Exactly one of `--body-file` or `--url` must be given; passing both or neither
is a usage error.

## Make a report organization-wide

By default a newly created report is personal (`ispersonal=true`). Pass
`--org` to make it available to the whole organization:

```bash
crm report create --name "Sales Pipeline" --body-file pipeline.rdl --org
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
    --description "SSRS report for sales pipeline by owner."
```

## Add the report to a solution

```bash
crm report create --name "Pipeline" --body-file pipeline.rdl \
    --solution cwx_crmworx
```

Use `--require-solution` to fail if no solution name resolves (from
`--solution` or the profile default). `set-category` also accepts `--solution`
to scope the `reportcategory` record.

## File under a report area

```bash
crm report set-category 1111aaaa-2222-bbbb-3333-cccccccccccc --category sales
```

Creates a `reportcategory` record linking the report to one of four areas:
`sales`, `service`, `marketing`, or `administrative`. A report can be filed
under more than one area by calling `set-category` multiple times. To remove a
category, delete the `reportcategory` record directly:

```bash
crm --json report set-category <id> --category sales   # returns reportcategoryid
crm entity delete reportcategories <reportcategoryid> --yes
```

## Preview without writing

`report create` honors the global `--dry-run` flag:

```bash
crm --dry-run report create --name "Pipeline" --body-file pipeline.rdl --org
```

Returns `{_dry_run: true, would_create: {entity_set, body}}` with the fully
resolved request body; no report is created.

## Delete a report

```bash
crm report delete 1111aaaa-2222-bbbb-3333-cccccccccccc
```

Deletes the `reports` record permanently. Any associated `reportcategory`
records are cascade-deleted by the server.
