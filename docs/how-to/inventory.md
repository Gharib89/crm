# How-to: inventory

`crm inventory extract` reads an org's customizations into a **migration
inventory**: a `manifest.json` in [inventory manifest v1](../reference/inventory-manifest.md)
format plus the raw artifacts it points at. An inventory describes what is
deployed; it is never a desired state for `crm apply`
([ADR 0032](../adr/0032-inventory-descriptive-contract-keyed-by-component-key.md)).

It runs on **every profile**. A `crm2011` profile is read over the CRM 2011 SOAP
endpoint (see [How-to: legacy](legacy.md) to create one); a v9 on-prem or Dataverse
online profile is read over the Web API. The manifest's `source.platform` says
which: `crm2011`, `cloud` when the profile authenticates with `oauth`, else
`onprem`. See the [CLI reference](../reference/cli.md) for every flag.

## Extract

```bash
crm --json inventory extract --out ./inventory --profile legacy2011
crm --json inventory extract --out ./inventory --profile prod
```

The run is **read-only**, and nothing is published first: the inventory holds the
**published** customizations (`publication: "published"`). On a `crm2011` profile
every request goes through the same SOAP allowlist as `crm legacy`, so
`PublishXml`, `PublishAllXml` and `ImportSolution` are refused. On a v9 profile
every call is a GET, so a [read-only profile](../adr/0021-read-only-profile-tty-gated-guardrail.md)
runs it too. It reads every v1 kind (`scope.mode: "org"` unless you pass
`--solution`):

- entities, global option sets, and each entity's attributes, relationships and
  local option sets, from the metadata reads (`EntityDefinitions`,
  `GlobalOptionSetDefinitions` and one `RetrieveEntity` per entity on the Web API);
- solutions, web resources, plug-in assemblies, plug-in types, steps, step images,
  processes (workflow definitions, type 1), forms, views, sitemaps, solution
  membership and dependencies, each with one FetchXML read paged to its last page
  by the paging cookie (SOAP `RetrieveMultiple` on `crm2011`, a `?fetchXml=` query
  on the Web API). Dependencies are read whole, not one component at a time: the
  `dependency` entity on `crm2011`, the `dependencies` entity set on the Web API;
- the application ribbon and the ribbon of each entity with a display label (an
  entity with none, such as an intersect entity, has no UI and no ribbon).

Each entity gets one metadata read and one ribbon read, so the whole-org read takes
tens of minutes on a large org.

`DIR` receives:

```text
<DIR>/
  manifest.json
  artifacts/<kind>/<slug>-<hash>.<name>
```

The artifacts are web resource content (`content.js`, `content.png`, ...), the
plug-in assembly binary (`content.dll`), workflow XAML (`workflow.xaml`), form XML
(`form.xml`), view fetch and layout XML (`fetch.xml`, `layout.xml`), sitemap XML
(`sitemap.xml`) and ribbon XML (`ribbon.xml`).

## Narrow to solutions

```bash
crm --json inventory extract --out ./inventory --solution ContosoCore --solution ContosoSales
```

`--solution UNIQUE_NAME` (repeatable, on every platform, matched case-insensitively)
narrows the inventory to the named solutions: their solution records and memberships,
each member component, each attribute, local option set, form, view and ribbon of a
member entity, each relationship with a member entity on either side, and each
dependency whose dependent component is kept. On v9 and cloud, an entity a solution
holds without its subcomponents, or as a shell, brings only the subcomponents the
solution lists on their own. The manifest header records the scope with each name as
the org spells it (`scope.mode: "solutions"`, `scope.solutions`).

Entity details and entity ribbons are read only for member entities, and the
application ribbon is not read, so a narrowed run reads less per entity. The
org-wide component reads still run, because a solution membership names its
component only by id, so expect the component reads to take as long as a whole-org
run.

An unknown solution name is a read error on the `solution` kind: the run goes on,
exits **1** and keeps its checkpoint (see [When a read fails](#when-a-read-fails)).

## The summary

`data` is a short summary; the manifest holds the records, coverage and gaps:

| Key | Value |
|---|---|
| `out`, `manifest` | the output folder and the `manifest.json` path |
| `records` | the number of records written |
| `kinds` | per kind, `{status, records}`; `status` is `complete`, `partial` or `failed` |
| `gaps` | the number of extraction gaps |
| `errors` | one line per failed read |
| `reused` | per kind, the reads taken from the checkpoint instead of the org |
| `resumed` | `true` when the run continued an earlier one |
| `complete` | `true` when every kind read without an error |

## Resume an interrupted run

Each finished read (one metadata request, one entity's detail, one
FetchXML page, one ribbon) is saved under `DIR/.checkpoint/` before the
next starts. To resume, **re-run the same command on the same `DIR`**: finished
reads are reused, and a kind's paging continues after its last finished page from
that page's paging cookie. `extracted_at` stays the first run's start time.

`DIR` is refused when:

- it holds a checkpoint of **another org** (a different profile URL);
- it holds a checkpoint of **another scope** (a different `--solution` set, or a
  narrowed run against a whole-org checkpoint, or the reverse): "holds a checkpoint
  of another scope";
- it holds files but **no checkpoint**, which includes a finished inventory, so a
  new extraction is never mixed into an old one. Pass an empty or new folder.

## When a read fails

A read that faults is recorded and the run goes on (continue-and-report,
[ADR 0007](../adr/0007-record-clone-no-rollback-continue-and-report.md)): its kind's
coverage is `failed` (no records) or `partial` (some records), its error lands in
the coverage entry and in `errors`, and the manifest is still written. The command
then exits **1** with `ok: false` and the summary still in `data`, and the
checkpoint is kept, so a re-run on the same `DIR` retries only the reads that
failed. A page that reports more records but carries no paging cookie marks its
kind `truncated` and `partial`.

An auth failure (401) or an unreachable org is not per read: it stops the run with
the standard error envelope and no summary. The checkpoint keeps every finished
read, so re-run once the org answers again.

The checkpoint is removed once a run reads everything without an error.

## Extraction gaps

The manifest's `gaps` names what the endpoint (the CRM 2011 SOAP endpoint or the
Web API) cannot read, or failed to read, rather than leaving it out; each reason
names the endpoint:

- a **plug-in assembly stored on disk or in the GAC**: its record is written, but
  the binary lives on the CRM server, not in the database;
- an **ISV page** (`/ISV/...`) referenced from a web resource or the sitemap: a
  file on the CRM web server;
- **external integrations** (every platform): services that call the org or that
  its plug-ins and processes call;
- the **SQL data sources of SSRS reports** (`crm2011` and on-prem v9, not cloud);
- the **ribbons of entities with no display label** (intersect and internal
  entities): such an entity has no UI, so its ribbon is not read (CRM 2011 faults
  the read), nor is the ribbon of an entity with no collection label in your
  language (Dataverse faults that read), and one gap lists them all;
- **rows naming a component this inventory does not record** (a solution
  membership or dependency on a kind v1 does not define, whose own read failed,
  whose own row has no component key, or, in a `--solution` run, that may belong to
  an entity outside the named solutions),
  counted per kind and component type;
- **rows the key contract cannot hold** (a component key field the org left empty,
  such as a plug-in step with no name), counted per kind with the first error;
- the **source identity and build**, when its `WhoAmI` or `RetrieveVersion` read
  failed: the manifest's `version` then reads `unknown`, and the gap carries the
  fault. A re-run retries the read.

Collect the others by hand from the server or the people who run the integrations.
